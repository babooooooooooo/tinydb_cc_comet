""":memory: + locking=True 必须 NOT 调 fcntl.flock (Task 6.3)."""
import threading
import pytest

from tinydb._filelock import _HAS_FCNTL
from tinydb.database import Database


@pytest.mark.integration
def test_memory_mode_does_not_call_flock(monkeypatch):
    """:memory: 模式下 fcntl.flock 调用计数必须为 0."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl
    calls = []
    monkeypatch.setattr(
        real_fcntl, "flock",
        lambda fd, op: calls.append((fd, op))
    )
    db = Database(":memory:", locking=True)
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
        assert calls == [], f"flock was called in memory mode: {calls}"
    finally:
        db.close()


@pytest.mark.integration
def test_memory_mode_locking_true_serializes_threads():
    """:memory: + locking=True 仍用 RLock 串行化线程(sanity check)."""
    db = Database(":memory:", locking=True)
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, src TEXT)")
        barrier = threading.Barrier(8)

        def worker(thread_id: int):
            barrier.wait()
            for j in range(100):
                db.execute(
                    f"INSERT INTO t(id, src) VALUES ({thread_id * 100 + j}, 't{thread_id}')"
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = db.execute("SELECT * FROM t")
        assert len(rows) == 800
    finally:
        db.close()