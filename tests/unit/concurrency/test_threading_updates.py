"""Multi-threaded UPDATE on non-overlapping subsets (Task 6.2)."""
import threading
import pytest

from tinydb.database import Database


@pytest.mark.integration
def test_four_threads_200_updates_non_overlapping_subsets(tmp_path):
    """4 线程各 200 UPDATE 作用不重叠子集 → 最终状态匹配预期."""
    db = Database(str(tmp_path / "a.db"))
    try:
        # Setup: 800 行,id 0..799
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, owner INT, payload TEXT)")
        for i in range(800):
            db.execute(f"INSERT INTO t(id, owner, payload) VALUES ({i}, -1, 'orig')")

        # 4 线程,线程 i 拥有 id ∈ [i*200, (i+1)*200)
        def worker(thread_id: int):
            start = thread_id * 200
            end = start + 200
            for j in range(start, end):
                db.execute(
                    f"UPDATE t SET owner = {thread_id}, payload = 't{thread_id}' WHERE id = {j}"
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = db.execute("SELECT * FROM t")
        assert len(rows) == 800
        for r in rows:
            rid = int(r.values[0])
            expected_owner = rid // 200
            assert int(r.values[1]) == expected_owner
            assert str(r.values[2]) == f"t{expected_owner}"
    finally:
        db.close()


@pytest.mark.integration
def test_concurrent_updates_no_lost_writes(tmp_path):
    """[SKIPPED-MVP-LIMITATION] 4 threads concurrently UPDATE shared row,
    each thread's increment survives: final counter == 400.

    Plan 模板要求 ``UPDATE t SET counter = counter + 1`` 算术;
    tinydb MVP tokenizer 限制 punct 集 = ``(),;=*<>!.``(无 ``+``),
    所以这个特定 invariant 在 MVP 版本无法表达。

    覆盖现状: ``test_threading_inserts.py::test_eight_threads_*`` 通过
    PK-uniqueness 提供不丢写的部分覆盖;真正的 transactional 全
    ACID 语义由 tinydb-acid change 负责(此处不重做)。

    Un-skip 触发条件: tokenizer 增加 ``+`` punct 支持后,补这一行
    SQL::

        for j in range(100):
            db.execute(
                f"UPDATE t SET counter = counter + 1 WHERE id = {j}"
            )

    并替换 ``pytest.skip(...)`` 为 ``assert int(r.values[1]) == 400``
    之类的最终断言。
    """
    pytest.skip(
        "MVP tokenizer lacks '+' punctuation; UPDATE arithmetic not expressible. "
        "See docs/superpowers/specs/concurrency-control-design.md and "
        "test_threading_inserts.py for partial no-lost-write coverage."
    )
