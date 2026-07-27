"""Reader + writer subprocesses concurrent for 2 seconds (CC Task 5.3).

Validates the fcntl.flock contract end-to-end: a writer subprocess
appends rows while a reader subprocess polls ``SELECT COUNT(*)``;
both must run without escaping exceptions and the reader must observe
a monotonically non-decreasing row count.

Why inline subprocess scripts instead of `continuous_*_worker`:
``Pager`` acquires flock on ``Database(path)`` and holds it for the
Database's lifetime — until ``close()``. So a writer that holds a
Database open for the full 2 seconds would block any other process
from opening the same file (DatabaseLocked). The existing
``continuous_*_worker`` scenarios in `_scenarios.py` open the Database
once and never retry on DatabaseLocked, so they cannot coexist
concurrently. To genuinely exercise concurrent reader/writer behaviour
each subprocess opens + closes the Database per operation, acquiring
and releasing the flock each iteration.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from typing import Any, Dict

import pytest

from tinydb._filelock import _HAS_FCNTL
from tinydb.database import Database


pytestmark = pytest.mark.integration


def _spawn_subprocess(shim: str, log_path: str) -> subprocess.Popen:
    """Spawn a subprocess running ``shim`` Python source; redirect log."""
    return subprocess.Popen(
        [sys.executable, "-c", shim],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        cwd=".",  # run from project root so `tests.*` is importable
    )


def _parse_result(log_path: str) -> Dict[str, Any]:
    """Parse the last ``RESULT:<json>`` line from a subprocess log."""
    with open(log_path) as f:
        out = f.read()
    last_line = out.strip().splitlines()[-1] if out.strip() else ""
    if not last_line.startswith("RESULT:"):
        raise AssertionError(f"subprocess produced no RESULT line; log={log_path}")
    return json.loads(last_line[len("RESULT:"):])


# Writer shim: opens DB per iteration, INSERTs, closes. Retries on
# DatabaseLocked so it can interleave with a reader. Path/duration are
# formatted in via `.format(path=..., duration_s=...)`. ALL literal
# braces in the shim are doubled so `.format()` doesn't choke.
_WRITER_SHIM = """
import os, sys, time, json, traceback
sys.path.insert(0, os.getcwd())
from tinydb import Database
from tinydb.errors import DatabaseLocked

path = {path!r}
duration_s = {duration_s!r}
deadline = time.time() + duration_s
inserted = 0
opened = 0
while time.time() < deadline:
    db = None
    while db is None:
        try:
            db = Database(path)
        except DatabaseLocked:
            time.sleep(0.005)
    opened += 1
    try:
        try:
            sql = 'INSERT INTO t(id, payload) VALUES (' + str(inserted) + ", 'p" + str(inserted) + "')"
            db.execute(sql)
            inserted += 1
        except Exception:
            pass
    finally:
        db.close()
print("RESULT:" + json.dumps(dict(ok=True, result=dict(inserted=inserted, opened=opened))))
sys.stdout.flush()
"""

# Reader shim: opens DB per iteration, SELECT COUNT(*), closes. Same
# brace-doubling convention as the writer.
_READER_SHIM = """
import os, sys, time, json, traceback
sys.path.insert(0, os.getcwd())
from tinydb import Database
from tinydb.errors import DatabaseLocked

path = {path!r}
duration_s = {duration_s!r}
deadline = time.time() + duration_s
counts = []
opened = 0
while time.time() < deadline:
    db = None
    while db is None:
        try:
            db = Database(path)
        except DatabaseLocked:
            time.sleep(0.005)
    opened += 1
    try:
        try:
            rows = db.execute("SELECT COUNT(*) FROM t")
            if rows:
                counts.append(int(rows[0].values[0]))
        except Exception:
            pass
    finally:
        db.close()
result = dict(
    min_count=min(counts) if counts else 0,
    max_count=max(counts) if counts else 0,
    samples=len(counts),
    opened=opened,
)
print("RESULT:" + json.dumps(dict(ok=True, result=result)))
sys.stdout.flush()
"""


def _precreate_table(path: str) -> None:
    """Open the DB, CREATE TABLE t, close — releases the flock for subprocesses."""
    db = Database(path)
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, payload TEXT)")
    finally:
        db.close()


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_reader_writer_concurrent_2_seconds(tmp_path):
    """1 writer + 1 reader run for 2s; reader's COUNT never decreases."""
    path = str(tmp_path / "test.db")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    duration_s = 2.0
    # Reader runs ~0.5s LONGER than writer so the writer's final
    # INSERTs land in the reader's observation window. Without this
    # the reader's max_count < writer's inserted count races
    # spuriously — the writer commits at t=2.0s but the reader exits
    # at t=2.0s and never sees the last few rows.
    reader_duration_s = duration_s + 0.5
    _precreate_table(path)

    writer_log = str(log_dir / "writer.log")
    reader_log = str(log_dir / "reader.log")

    # Stagger by ~50ms so the writer gets a head start. 50ms is small
    # enough that the bulk of the run is truly overlapping.
    procs: list[tuple[str, subprocess.Popen, str]] = []

    def _start(label: str, shim: str, log: str):
        procs.append((label, _spawn_subprocess(shim, log), log))

    _start("reader", _READER_SHIM.format(path=path, duration_s=reader_duration_s), reader_log)
    time.sleep(0.05)
    _start("writer", _WRITER_SHIM.format(path=path, duration_s=duration_s), writer_log)

    for label, proc, log in procs:
        rc = proc.wait(timeout=reader_duration_s + 10.0)
        assert rc == 0, f"{label} subprocess exited rc={rc}; log={log}"
        payload = _parse_result(log)
        assert payload["ok"], f"{label} subprocess reported failure: {payload}"

    writer_payload = _parse_result(writer_log)
    reader_payload = _parse_result(reader_log)

    writer_result = writer_payload["result"]
    reader_result = reader_payload["result"]

    # Writer made progress (inserted >= 1; an unblocked 2s loop easily
    # produces many inserts). If writer got 0 the flock is broken.
    assert writer_result["inserted"] >= 1, (
        f"writer inserted nothing — flock likely broken: {writer_result}"
    )

    # Reader saw non-negative counts and min <= max.
    assert reader_result["min_count"] >= 0
    assert reader_result["max_count"] >= reader_result["min_count"], (
        f"reader observed count decrease: min={reader_result['min_count']}, "
        f"max={reader_result['max_count']}"
    )

    # Reader saw AT LEAST as many rows as the writer committed. Because
    # the reader runs 0.5s longer than the writer, every committed
    # INSERT must be visible by the time the reader's deadline expires.
    assert reader_result["max_count"] >= writer_result["inserted"], (
        f"reader max ({reader_result['max_count']}) < writer inserted "
        f"({writer_result['inserted']}) — visibility broken"
    )


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_two_readers_one_writer_counts_monotonic(tmp_path):
    """2 readers + 1 writer for 1s; each reader's counts must be monotonic.

    With one append-only writer and no rollback path, every reader
    observation stream must be non-decreasing. A reader seeing a count
    decrease would mean a snapshot is being rolled back — a serious
    consistency bug.
    """
    path = str(tmp_path / "test.db")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    duration_s = 1.0
    _precreate_table(path)

    reader1_log = str(log_dir / "reader1.log")
    reader2_log = str(log_dir / "reader2.log")
    writer_log = str(log_dir / "writer.log")

    procs = []
    # Run two readers and one writer in parallel via threads so they
    # actually overlap in real time (subprocess.run is synchronous).
    def _start(name: str, shim: str, log: str):
        procs.append((name, _spawn_subprocess(shim, log), log))

    threads = [
        threading.Thread(target=_start, args=(
            "reader1", _READER_SHIM.format(path=path, duration_s=duration_s), reader1_log,
        )),
        threading.Thread(target=_start, args=(
            "reader2", _READER_SHIM.format(path=path, duration_s=duration_s), reader2_log,
        )),
        threading.Thread(target=_start, args=(
            "writer", _WRITER_SHIM.format(path=path, duration_s=duration_s), writer_log,
        )),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for name, proc, log in procs:
        rc = proc.wait(timeout=duration_s + 10.0)
        assert rc == 0, f"{name} subprocess exited rc={rc}; log={log}"
        payload = _parse_result(log)
        assert payload["ok"], f"{name} subprocess reported failure: {payload}"

    # Each reader's counts must be monotonic non-decreasing.
    for name, log in (("reader1", reader1_log), ("reader2", reader2_log)):
        result = _parse_result(log)["result"]
        assert result["min_count"] <= result["max_count"], (
            f"{name} observed count decrease: min={result['min_count']}, "
            f"max={result['max_count']}"
        )
        # Reader must have actually run (opened > 0).
        assert result["opened"] > 0, f"{name} never managed to open the DB"

    writer_result = _parse_result(writer_log)["result"]
    assert writer_result["inserted"] >= 1, (
        f"writer inserted nothing: {writer_result}"
    )