"""Subprocess-callable scenarios for cross-process concurrency tests."""
from __future__ import annotations

import time


def insert_n(db, n: int) -> dict:
    """INSERT n rows into t(id, payload)."""
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, payload TEXT)")
    except Exception:
        pass
    db.execute("BEGIN")
    try:
        for i in range(n):
            db.execute(f"INSERT INTO t(id, payload) VALUES ({i}, 'p{i}')")
    except Exception:
        db.execute("ROLLBACK")
        raise
    return {"inserted": n}


def count_users(db) -> dict:
    """SELECT COUNT(*) FROM t."""
    try:
        rows = db.execute("SELECT COUNT(*) FROM t")
    except Exception as exc:
        if "does not exist" in str(exc):
            return {"count": 0}
        raise
    return {"count": int(rows[0].values[0]) if rows else 0}


def assert_locked(path: str) -> dict:
    """Open Database(path); catch DatabaseLocked and report its status."""
    from tinydb import Database
    from tinydb.errors import DatabaseLocked

    try:
        db = Database(path)
    except DatabaseLocked as exc:
        return {"status": "locked", "path": exc.path}
    db.close()
    return {"status": "open"}


def open_and_close(path: str) -> dict:
    """Open Database(path) and close it."""
    from tinydb import Database

    db = Database(path)
    db.close()
    return {"status": "closed"}


def continuous_writer_worker(path: str, duration_s: float) -> dict:
    """Run INSERTs for duration_s seconds."""
    from tinydb import Database

    db = Database(path)
    try:
        try:
            db.execute("CREATE TABLE t (id INT PRIMARY KEY, payload TEXT)")
        except Exception:
            pass
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


def continuous_reader_worker(path: str, duration_s: float) -> dict:
    """Run COUNT(*) for duration_s seconds."""
    from tinydb import Database

    db = Database(path)
    try:
        try:
            db.execute("CREATE TABLE t (id INT PRIMARY KEY, payload TEXT)")
        except Exception:
            pass
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

# The driver uses this registry to decode CLI arguments and decide whether
# the first argument is a database path that it should open itself.
SCENARIOS_META = {
    "insert_n": {"needs_db": True, "args": [("n", int)]},
    "count_users": {"needs_db": True, "args": []},
    "assert_locked": {"needs_db": False, "args": [("path", str)]},
    "open_and_close": {"needs_db": False, "args": [("path", str)]},
    "continuous_writer_worker": {"needs_db": False, "args": [("path", str), ("duration_s", float)]},
    "continuous_reader_worker": {"needs_db": False, "args": [("path", str), ("duration_s", float)]},
}
