"""Subprocess-callable scenarios for cross-process concurrency tests."""
from __future__ import annotations

import json
import sys
import time
from typing import Any


def insert_n(db, n: int) -> dict:
    """INSERT n rows into t(id, payload)."""
    db.execute("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, payload TEXT)")
    db.execute("BEGIN")
    try:
        for i in range(n):
            db.execute(f"INSERT INTO t(id, payload) VALUES ({i}, 'p{i}')")
    except Exception:
        db.execute("ROLLBACK")
        raise
    # Note: tinydb MVP has no commit; we keep the txn open for the
    # test's lifetime so the rows are visible only via fread.
    return {"inserted": n}


def count_users(db) -> dict:
    """SELECT COUNT(*) FROM t."""
    rows = db.execute("SELECT COUNT(*) FROM t")
    return {"count": int(rows[0].values[0]) if rows else 0}


def assert_locked(path: str) -> dict:
    """Open Database(path); catch DatabaseLocked → return 'locked'."""
    from tinydb import Database
    from tinydb.errors import DatabaseLocked
    try:
        db = Database(path)
    except DatabaseLocked as e:
        return {"status": "locked", "path": e.path}
    db.close()
    return {"status": "open"}


def open_and_close(path: str) -> dict:
    """Open Database(path) and close."""
    from tinydb import Database
    db = Database(path)
    db.close()
    return {"status": "closed"}


def continuous_writer_worker(path: str, duration_s: float, start_event) -> dict:
    """Run INSERTs for duration_s seconds. start_event signals main to start."""
    from tinydb import Database
    db = Database(path)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, payload TEXT)")
        start_event.set()
        deadline = time.time() + duration_s
        i = 0
        while time.time() < deadline:
            try:
                db.execute(f"INSERT INTO t(id, payload) VALUES ({i}, 'p{i}')")
            except Exception:
                pass
            i += 1
        return {"inserted": i}
    finally:
        db.close()


def continuous_reader_worker(path: str, duration_s: float, start_event) -> dict:
    """Run COUNT(*) for duration_s seconds. start_event signals main to start."""
    from tinydb import Database
    db = Database(path)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY, payload TEXT)")
        start_event.set()
        counts = []
        deadline = time.time() + duration_s
        while time.time() < deadline:
            try:
                rows = db.execute("SELECT COUNT(*) FROM t")
                if rows:
                    counts.append(int(rows[0].values[0]))
            except Exception:
                pass
        return {"min_count": min(counts) if counts else 0, "max_count": max(counts) if counts else 0}
    finally:
        db.close()


SCENARIOS = {
    "insert_n": insert_n,
    "count_users": count_users,
    "assert_locked": assert_locked,
    "open_and_close": open_and_close,
    "continuous_writer_worker": continuous_writer_worker,
    "continuous_reader_worker": continuous_reader_worker,
}
