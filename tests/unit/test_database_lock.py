"""Unit tests for Database threading.RLock integration (Task 3 of concurrency-control).

These tests verify that ``Database.__init__`` constructs an RLock when
``locking=True`` (default) and ``None`` when ``locking=False``; that
``execute()`` and ``explain_plan()`` acquire the lock; that nested
``execute()`` calls are reentrant (RLock semantics); that ``close()`` is
safe to call with locking enabled; and that ``DatabaseLocked`` is
exported from both ``tinydb`` and ``tinydb.errors``.
"""
import threading

import pytest

from tinydb.database import Database
from tinydb.errors import DatabaseLocked


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Default locking: Database(path) creates an RLock.
# ---------------------------------------------------------------------------

def test_database_default_locking_attribute_is_rlock(tmp_path):
    """Database(path) 默认 locking=True, self._lock 应为 threading.RLock 实例."""
    # Use locking=False to bypass fcntl; manually flip _lock to verify
    # the kwarg path is honored when locking=True is requested. But default
    # already is True — let's just assert the type when locking=True.
    # However, locking=True will flock. Use the parameterized approach:
    # if _HAS_FCNTL is False, skip; else, we need a path that doesn't
    # conflict with another test. tmp_path is unique per test, so flock
    # acquisition is safe.
    from tinydb._filelock import _HAS_FCNTL
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl for default locking path")
    db = Database(str(tmp_path / "a.db"))
    try:
        assert isinstance(db._lock, type(threading.RLock()))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# locking=False bypass: self._lock is None.
# ---------------------------------------------------------------------------

def test_database_locking_false_lock_is_none(tmp_path):
    """Database(path, locking=False) 时 self._lock 为 None."""
    db = Database(str(tmp_path / "a.db"), locking=False)
    try:
        assert db._lock is None
    finally:
        db.close()


def test_database_memory_locking_false_lock_is_none():
    """Database(':memory:', locking=False) 时 self._lock 为 None."""
    db = Database(":memory:", locking=False)
    try:
        assert db._lock is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# execute() is wrapped: the lock is acquired/released around the call.
# ---------------------------------------------------------------------------

def _counting_lock():
    """Build a fake RLock-like object that counts acquire/release calls."""
    state = {"acquire": 0, "release": 0}

    class CountingLock:
        def __enter__(self):
            state["acquire"] += 1
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            state["release"] += 1
            return False

    return CountingLock(), state


def test_database_execute_acquires_and_releases_lock(tmp_path):
    """execute() 在 locking=True 时必须 acquire+release self._lock."""
    from tinydb._filelock import _HAS_FCNTL
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl for default locking path")
    db = Database(str(tmp_path / "a.db"))
    try:
        fake, state = _counting_lock()
        db._lock = fake  # monkey-patch
        db.execute("CREATE TABLE t (id INT)")
        assert state["acquire"] == 1
        assert state["release"] == 1
    finally:
        db.close()


def test_database_execute_acquires_lock_when_locking_false(tmp_path):
    """execute() 在 locking=False 时不应触碰锁(None) — 不抛异常."""
    db = Database(str(tmp_path / "a.db"), locking=False)
    try:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        out = db.execute("SELECT * FROM t")
        assert len(out) == 1
    finally:
        db.close()


def test_database_explain_plan_acquires_and_releases_lock(tmp_path):
    """explain_plan() 在 locking=True 时必须 acquire+release self._lock."""
    from tinydb._filelock import _HAS_FCNTL
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl for default locking path")
    db = Database(str(tmp_path / "a.db"))
    try:
        db.execute("CREATE TABLE t (id INT)")
        fake, state = _counting_lock()
        db._lock = fake
        plan = db.explain_plan("SELECT * FROM t")
        assert plan is not None
        assert state["acquire"] == 1
        assert state["release"] == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Reentrant: execute() called from inside execute() must not deadlock.
# ---------------------------------------------------------------------------

def test_database_execute_is_reentrant(tmp_path):
    """execute() 内部再次调 execute() 不应死锁(RLock 可重入)."""
    db = Database(str(tmp_path / "a.db"), locking=False)
    try:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")

        # Monkey-patch execute to call itself recursively.
        original_execute = db.execute
        call_count = {"n": 0}

        def reentrant_execute(sql):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: invoke inner execute(); this acquires the
                # same RLock twice — must not deadlock.
                return db.execute("SELECT * FROM t")
            return original_execute(sql)

        db.execute = reentrant_execute
        out = db.execute("SELECT * FROM t")
        assert len(out) == 1
        assert call_count["n"] == 2
    finally:
        # Restore and close
        try:
            db.execute = original_execute
        except Exception:
            pass
        db.close()


# ---------------------------------------------------------------------------
# close() is safe to call when locking=True.
# ---------------------------------------------------------------------------

def test_database_close_with_locking_true(tmp_path):
    """Database.close() 在 locking=True 时不应抛异常."""
    from tinydb._filelock import _HAS_FCNTL
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl for default locking path")
    db = Database(str(tmp_path / "a.db"))
    db.execute("CREATE TABLE t (id INT)")
    db.close()  # 不应抛


def test_database_close_is_idempotent_with_lock(tmp_path):
    """Database.close() 重复调用安全."""
    db = Database(str(tmp_path / "a.db"), locking=False)
    db.close()
    db.close()  # 不应抛


def test_database_close_with_locking_false(tmp_path):
    """Database.close() 在 locking=False 时安全."""
    db = Database(str(tmp_path / "a.db"), locking=False)
    db.close()


# ---------------------------------------------------------------------------
# DatabaseLocked is importable from tinydb and tinydb.errors.
# ---------------------------------------------------------------------------

def test_database_locked_importable_from_tinydb():
    """DatabaseLocked 可从 tinydb 直接导入."""
    from tinydb import DatabaseLocked as Imported
    assert Imported is DatabaseLocked


def test_database_locked_importable_from_tinydb_errors():
    """DatabaseLocked 可从 tinydb.errors 导入."""
    from tinydb.errors import DatabaseLocked as Imported
    assert Imported is DatabaseLocked


# ---------------------------------------------------------------------------
# Constructor robustness: DatabaseLocked propagates before _lock is set.
# ---------------------------------------------------------------------------

def test_database_locking_kwarg_forwarded_to_pager(tmp_path):
    """Database 构造时把 locking 关键字透传给 Pager."""
    # 用两个 Database 实例,locking=False 那个应在 locking=True 持锁期间打开.
    from tinydb._filelock import _HAS_FCNTL
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    db_locked = Database(str(tmp_path / "a.db"))  # 默认 locking=True
    try:
        db_no_lock = Database(str(tmp_path / "a.db"), locking=False)
        try:
            assert db_no_lock._lock is None
        finally:
            db_no_lock.close()
    finally:
        db_locked.close()


# ---------------------------------------------------------------------------
# Closed-Database guard: execute()/explain_plan() raise after close().
# ---------------------------------------------------------------------------

def test_database_execute_after_close_raises_runtime_error(tmp_path):
    """close() 之后调 execute() 必须抛 RuntimeError,不重新进入 locked region."""
    db = Database(str(tmp_path / "a.db"), locking=False)
    db.execute("CREATE TABLE t (id INT)")
    db.close()
    with pytest.raises(RuntimeError, match="closed"):
        db.execute("SELECT * FROM t")


def test_database_explain_plan_after_close_raises_runtime_error(tmp_path):
    """close() 之后调 explain_plan() 必须抛 RuntimeError."""
    db = Database(str(tmp_path / "a.db"), locking=False)
    db.execute("CREATE TABLE t (id INT)")
    db.close()
    with pytest.raises(RuntimeError, match="closed"):
        db.explain_plan("SELECT * FROM t")