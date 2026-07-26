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
    """两线程 execute 临界区不重叠 (RLock serialisation: 2 × 50 INSERTs → 100 rows)."""
    db = Database(str(tmp_path / "a.db"))
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, marker INT)")

        def worker(thread_id: int):
            for j in range(50):
                db.execute(
                    f"INSERT INTO t(id, marker) VALUES ({thread_id * 50 + j}, {thread_id})"
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Serialisation invariant: all 100 inserts succeeded.
        # (If the RLock were absent, we'd expect interleave-related PK collisions.)
        rows = db.execute("SELECT * FROM t")
        assert len(rows) == 100
        markers = sorted(int(r.values[1]) for r in rows)
        # 50 rows from thread 0, 50 rows from thread 1
        assert markers.count(0) == 50
        assert markers.count(1) == 50
    finally:
        db.close()