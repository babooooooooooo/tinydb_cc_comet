"""locking=False opt-out 路径 (Task 6.4)."""
import threading
import pytest

from tinydb._filelock import _HAS_FCNTL
from tinydb.database import Database


@pytest.mark.integration
def test_locking_false_does_not_call_flock(tmp_path, monkeypatch):
    """locking=False → fcntl.flock 调用次数为 0."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl
    calls = []
    monkeypatch.setattr(
        real_fcntl, "flock",
        lambda fd, op: calls.append((fd, op))
    )
    db = Database(str(tmp_path / "a.db"), locking=False)
    try:
        assert calls == []
    finally:
        db.close()


@pytest.mark.integration
def test_locking_false_does_not_construct_rlock(tmp_path, monkeypatch):
    """locking=False → threading.RLock 不被构造."""
    import threading
    original = threading.RLock
    instances = []
    real_init = original.__init__

    def counting_init(self, *args, **kwargs):
        instances.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(threading.RLock, "__init__", counting_init)
    db = Database(str(tmp_path / "a.db"), locking=False)
    try:
        assert len(instances) == 0
    finally:
        db.close()


@pytest.mark.integration
def test_locking_false_can_open_already_locked_db(tmp_path):
    """进程 A 持锁,locking=False 进程 B 仍可打开(spec REQ-LOCK-002 scenario)."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    p1 = Database(str(tmp_path / "a.db"))
    try:
        p2 = Database(str(tmp_path / "a.db"), locking=False)
        try:
            p2.execute("CREATE TABLE t (id INT PRIMARY KEY)")
        finally:
            p2.close()
    finally:
        p1.close()


@pytest.mark.integration
def test_locking_false_short_circuits_lock_acquire(tmp_path, monkeypatch):
    """locking=False → execute / explain_plan / close 期间 RLock.__enter__ 调用次数为 0."""
    import threading
    import _thread
    enter_counts = []
    exit_counts = []

    class TrackedRLock:
        """Wraps a real ``_thread.RLock`` so any enter/exit is observable.

        In Python 3.12+ ``threading.RLock`` is a factory function returning
        a C-level ``_thread.RLock`` whose ``__enter__``/``__exit__`` are
        unbound C slots — ``monkeypatch.setattr(threading.RLock, ...)`` on
        the module-level factory has no effect, and ``setattr`` on the
        instance's dunder fails because the slot is read-only. The cleanest
        way to instrument enters is to replace the factory with a tracked
        wrapper; ``locking=False`` should make the wrapper never be called.
        """

        def __init__(self):
            self._inner = _thread.RLock()

        def __enter__(self):
            enter_counts.append(self._inner)
            self._inner.acquire()
            return self

        def __exit__(self, *args):
            exit_counts.append(self._inner)
            self._inner.release()
            return False

        def acquire(self, blocking=True, timeout=-1):
            return self._inner.acquire(blocking, timeout)

        def release(self):
            return self._inner.release()

    # Database imports ``RLock`` via ``from threading import RLock`` so we
    # must patch BOTH the threading module and the captured name in the
    # database module for any construction inside production code to be
    # routed through our wrapper.
    monkeypatch.setattr(threading, "RLock", TrackedRLock)
    from tinydb import database as _db_mod
    monkeypatch.setattr(_db_mod, "RLock", TrackedRLock)

    db = Database(str(tmp_path / "a.db"), locking=False)
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
        db.explain_plan("SELECT * FROM t")
        assert enter_counts == [], f"RLock.__enter__ called when locking=False: {enter_counts}"
        assert exit_counts == [], f"RLock.__exit__ called when locking=False: {exit_counts}"
    finally:
        db.close()