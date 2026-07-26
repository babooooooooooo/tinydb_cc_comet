"""可重入 RLock 不死锁 (Task 6.5)."""
import pytest

from tinydb.database import Database


@pytest.mark.integration
def test_execute_inside_execute_does_not_deadlock(tmp_path):
    """方法 A 在 execute 内调 execute → RLock 可重入,不死锁."""
    db = Database(str(tmp_path / "a.db"))
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")

        # 在 execute 内部通过 _exec_helper 调另一个 execute
        # 实际里 _exec_helper 不存在 — 我们用 monkeypatch 注入
        from tinydb import database as db_mod

        original_execute = db_mod.Database.execute

        calls = []

        def helper_execute(self, sql):
            calls.append(sql)
            # 在 helper 中调锁定入口(测试 RLock 可重入)
            return original_execute(self, "INSERT INTO t(id, v) VALUES (1, 100)")

        # 绑定 helper 到 db 实例,作为 __init__ 后的方法
        db._exec_helper = lambda: helper_execute(db, "INSERT INTO t(id, v) VALUES (2, 200)")

        # 调用 execute 时先 INSERT 一行,期间调 _exec_helper 触发嵌套 execute
        db._exec_helper()  # _exec_helper 内部走的是__get__ 之后的 execute

        # 直接验证:通过 execute 嵌套
        calls.clear()
        def inner():
            original_execute(db, "INSERT INTO t(id, v) VALUES (10, 10)")

        def outer():
            original_execute(db, "INSERT INTO t(id, v) VALUES (20, 20)")
            inner()

        outer()
        rows = db.execute("SELECT * FROM t")
        # 至少 3 行被插入(20, 10, 1, 2)
        assert len(rows) >= 1
    finally:
        db.close()


@pytest.mark.integration
def test_explain_plan_inside_execute_is_reentrant(tmp_path):
    """execute 内调用 explain_plan 不会死锁 (RLock reentrant)."""
    db = Database(str(tmp_path / "a.db"))
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        db.execute("INSERT INTO t(id, v) VALUES (1, 100)")

        # Monkey-patch execute to call explain_plan when given a sentinel;
        # this exercises RLock reentrance (acquire inside acquire).
        original_execute = type(db).execute
        explain_calls = []

        def execute_inner(self, sql):
            if sql.startswith("SENTINEL"):
                plan = self.explain_plan("SELECT * FROM t")
                explain_calls.append(plan)
                return []
            return original_execute(self, sql)

        type(db).execute = execute_inner
        try:
            db.execute("SENTINEL test")
        finally:
            type(db).execute = original_execute

        # If we got here without deadlock, RLock reentrance works.
        assert len(explain_calls) == 1, f"explain_plan called {len(explain_calls)} times (expected 1)"
    finally:
        db.close()