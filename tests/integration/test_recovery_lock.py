"""Integration tests for Recovery + file-lock interaction (CC Task 8 / plan §4.3).

Each test simulates a crash mid-transaction via ``os._exit(1)`` in Process A
and verifies that Process B (the test itself, on the parent side) sees a
clean post-recovery state. Process A's ``os._exit(1)`` simulates a kill -9
where the OS releases the fcntl.flock atomically and the WAL is left on
disk in an arbitrary (in-flight) state.

These tests rely on:

* ``_HAS_FCNTL`` is true (Linux/WSL); on Windows the tests are skipped
  because the cross-process flock contract cannot be exercised.
* The ``write_uncommitted_inline`` / ``write_committed_inline`` /
  ``corrupt_wal_inline`` shims (defined below) match the
  ``RESULT:<json>`` contract used by ``tests/integration/concurrency/_driver.py``.

The shims are defined as inline strings (not added to ``_scenarios.py``)
because the change scope is test-only — the coordinator's task spec
explicitly says "Test-only change" and "Do NOT modify recovery.py or
pager.py or _filelock.py." ``_scenarios.py` is also left untouched so
the existing cross-process test surface is not perturbed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tinydb._filelock import _HAS_FCNTL
from tinydb.database import Database
from tinydb.wal import WalCorruption

pytestmark = pytest.mark.integration


# -- Process A shims (inline; see module docstring) -------------------------

# Each shim:
#   1. Inserts the cwd into sys.path so ``tinydb`` resolves.
#   2. Opens Database(path) with the default locking=True (so the parent
#      can't sneak in mid-crash — but os._exit releases the OS lock
#      atomically, so the parent's reopen is well-defined).
#   3. Performs the desired action (uncommitted txn / commit-then-exit /
#      corrupt-WAL) and calls os._exit(1) WITHOUT graceful close — that
#      is the kill -9 simulation.
#   4. Emits "RESULT:..." on stdout before exit so the parent can confirm
#      Process A reached the expected point.
#
# Literal-Python dicts use dict(...) because the strings are passed
# through subprocess directly (no .format() brace collisions) — keeping
# the strings as plain triple-quoted literals for readability.

_WRITE_UNCOMMITTED_SHIM = r"""
import os, sys, json, traceback
sys.path.insert(0, os.getcwd())
from tinydb import Database

path = __PATH__
db = Database(path)
try:
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    for i in range(5):
        db.execute(f"INSERT INTO t(id, v) VALUES ({i}, 'p{i}')")
    print("RESULT:" + json.dumps(dict(ok=True, result=dict(phase="wrote"))))
    sys.stdout.flush()
except Exception as e:
    print("RESULT:" + json.dumps(dict(
        ok=False, type=type(e).__name__, msg=str(e),
        traceback=traceback.format_exc(),
    )))
    sys.stdout.flush()
# Simulate kill -9: skip COMMIT/ROLLBACK/close. ``os._exit`` does NOT
# run finally blocks or atexit handlers, so the WAL on disk retains
# the BEGIN+PAGE_WRITE records (no COMMIT) — exactly what a kill -9
# mid-transaction would leave behind. The OS releases the fcntl.flock
# when the process exits.
os._exit(1)
"""


_WRITE_COMMITTED_SHIM = r"""
import os, sys, json, traceback
sys.path.insert(0, os.getcwd())
from tinydb import Database

path = __PATH__
db = Database(path)
try:
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    db.execute("INSERT INTO t(id, v) VALUES (42, 'durable')")
    db.execute("COMMIT")
    print("RESULT:" + json.dumps(dict(ok=True, result=dict(phase="committed"))))
    sys.stdout.flush()
except Exception as e:
    print("RESULT:" + json.dumps(dict(
        ok=False, type=type(e).__name__, msg=str(e),
        traceback=traceback.format_exc(),
    )))
    sys.stdout.flush()
# Simulate kill -9 AFTER COMMIT. The row is durable in the main file
# (Transaction.commit writes main first, then fsync, then WAL COMMIT,
# then truncate_before). The WAL still carries the BEGIN+PAGE_WRITE+COMMIT
# records because truncate_before(txn_id=1) drops records with id<1 (no-op).
# Replay by Process B will re-apply the page write to the main file (idempotent).
os._exit(1)
"""


# This shim does a clean close(), then reopens the WAL file in append
# mode to write 50 bytes of garbage, then os._exit(1) — leaving a torn
# WAL record at the tail. Process B's first open will trigger Recovery
# which detects the corruption, applies the valid prefix (the prior
# committed row is already in the main file), truncates the WAL, and
# re-raises WalCorruption. Process B's SECOND open succeeds and sees
# the clean state.
_CORRUPT_WAL_SHIM = r"""
import os, sys, json, traceback
sys.path.insert(0, os.getcwd())
from tinydb import Database

path = __PATH__
db = Database(path)
try:
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
    db.execute("COMMIT")
    print("RESULT:" + json.dumps(dict(ok=True, result=dict(phase="committed"))))
    sys.stdout.flush()
except Exception as e:
    print("RESULT:" + json.dumps(dict(
        ok=False, type=type(e).__name__, msg=str(e),
        traceback=traceback.format_exc(),
    )))
    sys.stdout.flush()
    os._exit(2)
db.close()  # clean close: releases flock, flushes mmap

# Now reopen the WAL in raw append mode and write garbage. We use the
# existing WAL file path that Pager already created.
wal_path = path + ".wal"
if os.path.exists(wal_path):
    with open(wal_path, "ab") as f:
        f.write(b"\xff" * 50)
print("RESULT:" + json.dumps(dict(ok=True, result=dict(phase="corrupted"))))
sys.stdout.flush()
os._exit(1)
"""


def _spawn_shim_subprocess(shim: str, path: str, log_path: str) -> subprocess.Popen:
    """Spawn a subprocess that runs ``shim`` (with ``__PATH__`` substituted) and returns the Popen.

    The shim emits ``RESULT:<json>`` on stdout before calling os._exit.
    The parent reads ``log_path`` after wait() to confirm the shim reached
    its expected milestone. We use ``-c`` mode (not -m) so the shim can
    call os._exit() without triggering pytest atexit hooks.
    """
    populated = shim.replace("__PATH__", json.dumps(path))
    return subprocess.Popen(
        [sys.executable, "-c", populated],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
    )


def _read_result(log_path: str) -> dict:
    """Read ``log_path`` and return the last ``RESULT:...`` payload."""
    with open(log_path) as f:
        out = f.read()
    last_line = out.strip().splitlines()[-1] if out.strip() else ""
    assert last_line.startswith("RESULT:"), f"missing RESULT line; log={log_path}\n{out}"
    return json.loads(last_line[len("RESULT:"):])


# -- Tests -----------------------------------------------------------------


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_uncommitted_transaction_not_visible_after_recovery(tmp_path):
    """Process A writes uncommitted WAL, dies; Process B sees clean state.

    Process A: open DB → CREATE TABLE → BEGIN → INSERT 5 rows → os._exit(1)
    (no COMMIT, no ROLLBACK, no close). The OS releases the flock when
    the process exits.

    Process B: open DB → Recovery.replay scans WAL, sees BEGIN+5xPAGE_WRITE
    with no matching COMMIT → no pages applied to main → WAL truncated.
    B then SELECTs and must see zero rows.
    """
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    a_log = str(log_dir / "a.log")

    # 1. Process A — crash mid-transaction
    proc_a = _spawn_shim_subprocess(_WRITE_UNCOMMITTED_SHIM, path, a_log)
    rc_a = proc_a.wait(timeout=30)
    # os._exit(1) ⇒ returncode 1
    assert rc_a == 1, f"process A exit code {rc_a} (expected 1 from os._exit)"
    a_payload = _read_result(a_log)
    assert a_payload["ok"], f"process A failed before crash: {a_payload}"
    assert a_payload["result"]["phase"] == "wrote", a_payload

    # Sanity: WAL file exists with uncommitted records
    assert os.path.exists(wal_path), "WAL file missing — recovery can't be exercised"

    # 2. Process B — parent opens DB, recovery runs, verify clean state
    db = Database(path)
    try:
        rows = db.execute("SELECT * FROM t")
        # Uncommitted rows must NOT be visible after recovery.
        assert rows == [], (
            f"uncommitted rows leaked across recovery: got {len(rows)} rows, "
            f"expected 0 (recovery should have discarded uncommitted txn)"
        )
    finally:
        db.close()

    # 3. Process B can also commit subsequent transactions
    db2 = Database(path)
    try:
        db2.execute("BEGIN")
        db2.execute("INSERT INTO t(id, v) VALUES (100, 'post-recovery')")
        db2.execute("COMMIT")
        rows = db2.execute("SELECT * FROM t")
        ids = sorted(int(r.id) for r in rows)
        assert ids == [100], f"post-recovery INSERT not visible: got {ids}"
    finally:
        db2.close()


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_committed_transaction_visible_after_recovery(tmp_path):
    """Process A commits then dies; Process B sees the committed row.

    Process A: open → CREATE TABLE → BEGIN → INSERT(42,'durable') → COMMIT
    → os._exit(1). The row is durable in the main file (commit() writes
    main first, then fsync, then WAL COMMIT, then truncate_before). The
    WAL still has the BEGIN+PAGE_WRITE+COMMIT records because
    truncate_before(txn_id=1) drops records with id<1 (no-op for txn_id=1).

    Process B: open DB → Recovery.replay sees status[t1]="committed",
    re-applies the page write to main (idempotent — same data), truncates
    WAL. B SELECTs and must see (42, 'durable').
    """
    path = str(tmp_path / "crash.db")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    a_log = str(log_dir / "a.log")

    # 1. Process A — commit then crash
    proc_a = _spawn_shim_subprocess(_WRITE_COMMITTED_SHIM, path, a_log)
    rc_a = proc_a.wait(timeout=30)
    assert rc_a == 1, f"process A exit code {rc_a} (expected 1 from os._exit)"
    a_payload = _read_result(a_log)
    assert a_payload["ok"], f"process A failed: {a_payload}"
    assert a_payload["result"]["phase"] == "committed", a_payload

    # 2. Process B — open, recovery replays committed txn
    db = Database(path)
    try:
        rows = db.execute("SELECT * FROM t")
        # The row must be visible after recovery.
        ids_values = sorted((int(r.id), r.v) for r in rows)
        assert ids_values == [(42, "durable")], (
            f"expected [(42, 'durable')] after recovery, got {ids_values}"
        )
    finally:
        db.close()

    # 3. Process B can commit additional transactions
    db2 = Database(path)
    try:
        db2.execute("BEGIN")
        db2.execute("INSERT INTO t(id, v) VALUES (7, 'second')")
        db2.execute("COMMIT")
        rows = db2.execute("SELECT * FROM t")
        ids_values = sorted((int(r.id), r.v) for r in rows)
        assert ids_values == [(7, "second"), (42, "durable")], (
            f"post-recovery state unexpected: {ids_values}"
        )
    finally:
        db2.close()


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_partial_wal_then_recovery_clean_state(tmp_path):
    """Process A writes partial WAL, dies; Process B opens and recovers.

    Process A: open → CREATE TABLE → BEGIN → INSERT(1, 'a') → COMMIT
    (clean state with one durable row) → close() (releases flock) → reopen
    WAL file in raw append mode → write 50 bytes of garbage → os._exit(1).
    The torn WAL record simulates a process killed mid-fsync.

    Process B: first open → Recovery.replay detects WalCorruption,
    applies the valid prefix (the committed row was already in the main
    file before corruption so re-application is a no-op), truncates the
    WAL to the corruption boundary, then re-raises WalCorruption.

    Process B (second open): WAL is now clean → succeeds → row(1, 'a')
    is visible. This mirrors the contract documented in
    tests/integration/test_crash_recovery.py::test_partial_wal_record_truncated_on_recovery.
    """
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    a_log = str(log_dir / "a.log")

    # 1. Process A — commit cleanly, then corrupt WAL, then crash
    proc_a = _spawn_shim_subprocess(_CORRUPT_WAL_SHIM, path, a_log)
    rc_a = proc_a.wait(timeout=30)
    # The shim first calls db.close() (rc 0 path not taken — we os._exit(1)
    # after appending garbage), so we expect returncode 1.
    assert rc_a == 1, f"process A exit code {rc_a} (expected 1 from os._exit)"
    a_payload = _read_result(a_log)
    assert a_payload["ok"], f"process A failed: {a_payload}"
    assert a_payload["result"]["phase"] == "corrupted", a_payload

    # 2. Sanity: WAL has been extended with garbage (size > original)
    assert os.path.exists(wal_path), "WAL file missing"
    wal_size_after_corruption = os.path.getsize(wal_path)
    assert wal_size_after_corruption >= 50 + 16, (
        f"WAL too small to contain corruption trailer: {wal_size_after_corruption}"
    )

    # 3. Process B — first open raises WalCorruption (recovery applies
    #    valid prefix + truncates + re-raises). This matches
    #    tests/integration/test_crash_recovery.py::test_partial_wal_record_truncated_on_recovery.
    with pytest.raises(WalCorruption):
        Database(path)

    # 4. WAL was truncated to the corruption boundary — not zero, but
    #    smaller than the post-corruption size.
    assert os.path.getsize(wal_path) <= wal_size_after_corruption, (
        f"WAL not truncated by recovery: pre={wal_size_after_corruption}, "
        f"post={os.path.getsize(wal_path)}"
    )

    # 5. Process B — second open succeeds with clean state
    db = Database(path)
    try:
        rows = db.execute("SELECT * FROM t")
        ids_values = sorted((int(r.id), r.v) for r in rows)
        assert ids_values == [(1, "a")], (
            f"expected [(1, 'a')] after partial-WAL recovery, got {ids_values}"
        )
    finally:
        db.close()
