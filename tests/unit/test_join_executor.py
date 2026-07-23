"""tinydb-join-query (T6): JoinExecutor 单元测试。"""
import pytest
from dataclasses import dataclass

from tinydb.catalog import Catalog, Column
from tinydb.parser import parse
from tinydb.plan import build_plan
from tinydb.tokenizer import tokenize
from tinydb._join_executor import JoinExecutor


@dataclass
class _FakeExecutor:
    """最小 stub：模拟 Executor 接口供 JoinExecutor 调用。"""

    catalog: object
    table_rows: dict  # {table_name: list[list[value]]}

    def _scan_table(self, ti):
        return [(i, list(row), 0) for i, row in enumerate(self.table_rows.get(ti.name, []))]


@pytest.fixture
def catalog():
    c = Catalog()
    c.create_table(
        "users",
        tuple([Column("id", "INT"), Column("name", "TEXT")]),
        root_page_id=2, next_page_id=2,
    )
    c.create_table(
        "orders",
        tuple([Column("id", "INT"), Column("user_id", "INT"), Column("total", "INT")]),
        root_page_id=3, next_page_id=3,
    )
    return c


def _build_plan(sql, catalog):
    ast = parse(tokenize(sql)).statements[0]
    return build_plan(ast, catalog)


def test_inner_join_returns_matched_rows(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "alice"], [2, "bob"]],
            "orders": [[10, 1, 100], [11, 2, 200], [12, 3, 50]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u INNER JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, schema = exe.execute_plan(plan)
    # 匹配：u.id=1->o.id=10, u.id=2->o.id=11；o.user_id=3 不匹配
    assert len(rows) == 2
    # 收集 u.id -> o.id 映射
    pairs = set()
    for r in rows:
        pairs.add((r[0], r[1]))
    assert pairs == {(1, 10), (2, 11)}


def test_cross_join_returns_cartesian_product(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[10, 1, 0], [11, 2, 0]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan("SELECT * FROM users CROSS JOIN orders", catalog)
    rows, _ = exe.execute_plan(plan)
    assert len(rows) == 4  # 2 x 2


def test_inner_join_with_empty_right(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={"users": [[1, "a"]], "orders": []},
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    assert rows == []


def test_inner_join_with_empty_left(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={"users": [], "orders": [[10, 1, 0]]},
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    assert rows == []


def test_using_id_produces_single_merged_column(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[1, 1, 100], [2, 2, 200]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan("SELECT * FROM users u JOIN orders o USING (id)", catalog)
    rows, schema = exe.execute_plan(plan)
    # schema 应包含 'id'（合并键）+ 'name' + 'user_id' + 'total'
    assert "id" in schema
    # 'id' 在 schema 中只出现一次
    assert schema.count("id") == 1
    # 行数 = 2
    assert len(rows) == 2


def test_chained_three_table_join(catalog):
    c2 = Catalog()
    c2.create_table("a", tuple([Column("id", "INT")]), root_page_id=2, next_page_id=2)
    c2.create_table("b", tuple([Column("id", "INT")]), root_page_id=3, next_page_id=3)
    c2.create_table("c", tuple([Column("id", "INT")]), root_page_id=4, next_page_id=4)
    fe = _FakeExecutor(
        catalog=c2,
        table_rows={
            "a": [[1], [2]],
            "b": [[1], [2], [3]],
            "c": [[1], [2]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT a.id FROM a JOIN b ON a.id = b.id JOIN c ON b.id = c.id",
        c2,
    )
    rows, _ = exe.execute_plan(plan)
    # INNER JOIN 要求 a.id=b.id AND b.id=c.id，所以仅有 (a=1,b=1,c=1) 与
    # (a=2,b=2,c=2) 两条满足。
    assert len(rows) == 2
    pairs = {(r[0],) for r in rows}
    assert pairs == {(1,), (2,)}


def test_inner_join_routes_through_executor_scan(catalog, monkeypatch):
    """ACID 验收：JOIN 路径必须调用 executor._scan_table（间接走 _txn_read_page）。"""
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={"users": [[1, "a"]], "orders": [[10, 1, 0]]},
    )
    calls = {"n": 0}

    real = type(fe)._scan_table

    def counting(self, ti):
        calls["n"] += 1
        return real(self, ti)

    monkeypatch.setattr(type(fe), "_scan_table", counting)
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    exe.execute_plan(plan)
    # 至少调用 2 次（users + orders）
    assert calls["n"] >= 2


def test_inner_join_with_qualified_projection(catalog):
    """SELECT u.id, o.user_id 限定投影：验证 Project 节点能正确提取列。"""
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "alice"], [2, "bob"]],
            "orders": [[10, 1, 100], [11, 2, 200], [12, 3, 50]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.name, o.user_id FROM users u INNER JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, schema = exe.execute_plan(plan)
    # 匹配 u.id=1->o.user_id=1, u.id=2->o.user_id=2
    assert len(rows) == 2
    pairs = {(r[0], r[1]) for r in rows}
    assert pairs == {("alice", 1), ("bob", 2)}