"""4 subprocesses concurrent INSERT 250 rows each (CC Task 5.2 / plan §7.1).

Verifies the fcntl.flock contract: 4 independent Python subprocesses each
open the same file-backed Database, INSERT a non-overlapping 250-row
slice, then close. The parent process opens the DB after all subprocesses
have exited and asserts exactly 1000 unique rows are visible.

`insert_n(db, n)` from `_scenarios.py` is not directly callable from a
subprocess because its first arg is a live Database handle (Database
is not JSON-serializable for CLI args). We instead define a module-level
`_writer_scenario(path, offset, n)` helper in this test file and have
each subprocess import + invoke it through a tiny ``python -c`` shim.
The shim emits ``RESULT:<json>`` on stdout so the parent can parse
results uniformly with `_driver.py`'s contract used in
`test_lock_release_on_close.py`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any, Dict

import pytest

from tinydb._filelock import _HAS_FCNTL
from tinydb.database import Database
from tinydb.errors import DatabaseLocked


pytestmark = pytest.mark.integration


# Each subprocess must retry ``Database(path)`` while another writer
# holds the flock. ``_LOCK_RETRY_BACKOFF_S`` keeps the spin short
# enough that the test stays under 30s wall-time but spaced enough that
# contenders actually yield the lock between attempts. Set this as a
# module constant so tests can override it if needed.
_LOCK_RETRY_BACKOFF_S = 0.01


def _writer_scenario(path: str, offset: int, n: int) -> Dict[str, Any]:
    """Subprocess-callable: open DB, INSERT n rows starting at ``offset``.

    Lives in this test file (not `_scenarios.py`) because the plan
    already shipped `_scenarios.py` with a different ``insert_n(db, n)``
    signature that takes a Database handle. Each subprocess imports
    this function via ``from tests.integration.concurrency...`` and
    runs it; the test asserts every subprocess returns
    ``{"inserted": n, "offset": offset}``.

    Retries ``Database(path)`` on ``DatabaseLocked`` because the fcntl
    flock is exclusive and non-blocking: while another subprocess holds
    the lock, ``Pager._open_file`` raises immediately. Without retry,
    the test degenerates into "first writer wins, others bail".
    """
    db = None
    while db is None:
        try:
            db = Database(path)
        except DatabaseLocked:
            time.sleep(_LOCK_RETRY_BACKOFF_S)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, payload TEXT)")
        for i in range(n):
            db.execute(f"INSERT INTO t(id, payload) VALUES ({offset + i}, 'p{offset + i}')")
        return {"inserted": n, "offset": offset}
    finally:
        db.close()


def _spawn_writer_subprocess(path: str, offset: int, n: int, log_path: str) -> subprocess.Popen:
    """Spawn a subprocess that runs `_writer_scenario` and returns its Popen.

    The subprocess writes ``RESULT:<json>`` on stdout; the parent reads
    ``log_path`` after ``wait()`` and parses the line. The shim prepends
    ``os.getcwd()`` to ``sys.path`` so the ``tests.*`` package is
    importable regardless of how pytest was invoked.
    """
    shim = (
        "import os, sys, json, traceback\n"
        "sys.path.insert(0, os.getcwd())\n"
        "from tests.integration.concurrency.test_multiprocess_writers import _writer_scenario\n"
        "try:\n"
        f"    result = _writer_scenario({json.dumps(path)}, {offset}, {n})\n"
        '    print("RESULT:" + json.dumps({"ok": True, "result": result}))\n'
        "except Exception as e:\n"
        '    print("RESULT:" + json.dumps({\n'
        '        "ok": False,\n'
        '        "type": type(e).__name__,\n'
        '        "msg": str(e),\n'
        '        "traceback": traceback.format_exc(),\n'
        "    }))\n"
        "finally:\n"
        "    sys.stdout.flush()\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", shim],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
    )


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_four_subprocess_writers_1000_unique_rows(tmp_path):
    """4 subprocesses × 250 INSERTs → parent sees 1000 unique ids."""
    path = str(tmp_path / "test.db")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Stagger the spawns by ~10ms so all 4 subprocesses race for the
    # flock instead of strictly serializing through one-at-a-time
    # acquires. Without the stagger, on fast disks P1 fully completes
    # before P2 even imports tinydb and we never exercise lock contention.
    procs = []
    for i in range(4):
        log = str(log_dir / f"writer_{i}.log")
        proc = _spawn_writer_subprocess(path, i * 250, 250, log)
        procs.append((proc, log, i))
        time.sleep(0.01)

    for proc, log, i in procs:
        rc = proc.wait(timeout=60)
        assert rc == 0, f"writer {i} exited with rc={rc}; log={log}"
        # Sanity: every subprocess must report it inserted exactly 250 rows.
        with open(log) as f:
            out = f.read()
        last_line = out.strip().splitlines()[-1] if out.strip() else ""
        assert last_line.startswith("RESULT:"), f"writer {i} missing RESULT line; log={log}"
        payload = json.loads(last_line[len("RESULT:"):])
        assert payload["ok"], f"writer {i} reported failure: {payload}"
        assert payload["result"]["inserted"] == 250, (
            f"writer {i} inserted {payload['result']['inserted']} rows, expected 250"
        )

    # Parent opens the DB AFTER all subprocesses have closed their flock.
    db = Database(path)
    try:
        rows = db.execute("SELECT * FROM t")
        assert len(rows) == 1000, (
            f"expected 1000 rows after 4×250 inserts, got {len(rows)}"
        )
        ids = [int(r.id) for r in rows]
        assert len(set(ids)) == 1000, (
            f"duplicate ids detected: {len(ids)} total, {len(set(ids))} unique"
        )
        # Sanity: ids span the full expected range 0..999 with no gaps.
        assert min(ids) == 0 and max(ids) == 999, (
            f"id range unexpected: min={min(ids)}, max={max(ids)}"
        )
    finally:
        db.close()