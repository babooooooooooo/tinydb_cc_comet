"""Multi-threaded INSERT race (Task 6.1)."""
import threading
import pytest

from tinydb.database import Database


@pytest.mark.integration
def test_eight_threads_100_inserts_each_unique_ids(tmp_path):
    """8 线程 × 100 INSERT → 800 行,所有 ID 唯一,所有值匹配."""
    db = Database(str(tmp_path / "a.db"))
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
        ids = [int(r.values[0]) for r in rows]
        assert len(set(ids)) == 800  # no duplicates
        # Each src encodes thread_id; verify the threading invariant
        for r in rows:
            rid = int(r.values[0])
            tid = rid // 100
            assert str(r.values[1]) == f"t{tid}", (
                f"row id={rid} src={r.values[1]!r} doesn't match thread {tid}"
            )
    finally:
        db.close()


@pytest.mark.integration
def test_two_threads_concurrent_executes_do_not_overlap_critical_section(tmp_path):
    """两线程 execute 临界区不重叠(CRITICAL 锁定正确性).

    使用 ``threading.Event`` instrumentation (plan §5.1 spirit),但把 events
    放在 RLock context **内部** — 通过 monkey-patch ``Database._acquire_lock``
    实现。这样:
      - 有 RLock 时,只有一个线程能进入 context,in_cs 永远不会被
        两个线程同时持有,observed_overlap 不会 fire。
      - 无 RLock (negative test: monkeypatch 临时返回 nullcontext),两个
        线程能同时进入 context,in_cs 会被两个线程同时持有,
        observed_overlap fire。

    注:计划 §5.1 把 events 放在 ``db.execute(...)`` 之外,即使 RLock
    正常工作,both threads 也会短暂同时持有 in_cs (一个 about-to-enter
    等 acquire,另一个已经在 critical section),产生 false-positive。
    Verbatim 计划代码 (lines 1224-1256) 在 RLock 工作时也会 fail。
    """
    from tinydb import database as _db_mod

    in_cs = threading.Event()
    observed_overlap = threading.Event()
    original_acquire_lock = _db_mod.Database._acquire_lock

    def tracked_acquire_lock(self):
        inner = original_acquire_lock(self)

        class _Wrap:
            def __enter__(_self):
                # 真正进入 RLock (or nullcontext) 之后再记 events:
                # events 现在代表 "这个线程已在 critical section 内"。
                _self._cm = inner.__enter__()
                if in_cs.is_set():
                    observed_overlap.set()
                in_cs.set()
                return _self._cm

            def __exit__(_self, *args):
                try:
                    in_cs.clear()
                finally:
                    return inner.__exit__(*args)

        return _Wrap()

    _db_mod.Database._acquire_lock = tracked_acquire_lock

    db = Database(str(tmp_path / "a.db"))
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, marker INT)")

        def worker(thread_id: int):
            for j in range(50):
                db.execute(f"INSERT INTO t(id, marker) VALUES ({thread_id * 50 + j}, {thread_id})")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not observed_overlap.is_set(), "critical section overlapped"
        rows = db.execute("SELECT * FROM t")
        assert len(rows) == 100
    finally:
        db.close()
        _db_mod.Database._acquire_lock = original_acquire_lock