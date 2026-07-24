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


# --- Task 7: LEFT / RIGHT / FULL + USING/NATURAL Coalesce ----------------


def test_left_join_emits_unmatched_left_row_with_nulls(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"], [3, "c"]],  # u=3 无订单
            "orders": [[10, 1, 100], [11, 2, 200]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u LEFT JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    # 3 行：u=1->o=10, u=2->o=11, u=3(NULL)
    assert len(rows) == 3
    # u=3 行右部为 NULL
    by_u = {r[0]: r[1] for r in rows}
    assert by_u[3] is None


def test_right_join_preserves_right_unmatched(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[10, 1, 100], [11, 2, 200], [12, 99, 0]],  # o.user_id=99 无用户
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u RIGHT JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    # 3 行：u=1->o=10, u=2->o=11, NULL->o=12
    assert len(rows) == 3
    # 末尾是右未匹配行
    assert rows[-1][0] is None and rows[-1][1] == 12


def test_right_join_select_star_preserves_left_first_column_order(catalog):
    """Regression: RIGHT JOIN via swap-recurse produced right-first column order
    for SELECT * (masked by explicit SELECT u.id, o.id). Direct _nested_loop_right
    preserves strict-left-deep-insertion ordering.
    """
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"]],
            "orders": [[10, 1, 100], [11, 99, 0]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u RIGHT JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, schema = exe.execute_plan(plan)
    # Columns must be left-first: u.id, u.name, o.id, o.user_id, o.total
    assert schema[:2] == ["u.id", "u.name"]
    assert schema[2:] == ["o.id", "o.user_id", "o.total"]
    # 两行：u=1->o=10 (matched), NULL->o=11 (right unmatched)
    assert len(rows) == 2
    assert rows[0] == [1, "a", 10, 1, 100]
    assert rows[1] == [None, None, 11, 99, 0]  # left NULL-padded, right intact


def test_full_join_emits_both_unmatched(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"], [3, "c"]],
            "orders": [[10, 1, 100], [11, 99, 200]],  # u=3 无订单；o.user_id=99 无用户
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u FULL JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    # 4 行：u=1->o=10, NULL(u=3), NULL(o.user_id=99->o=11)
    assert len(rows) == 4


def test_left_join_with_using_emits_coalesced_id(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[1, 1, 100], [2, 2, 200]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u LEFT JOIN orders o USING (id)",
        catalog,
    )
    rows, schema = exe.execute_plan(plan)
    # schema 含 'id'（合并）+ 'name' + 'user_id' + 'total'
    assert "id" in schema
    assert schema.count("id") == 1


def test_using_chained_join_merges_keys(catalog):
    """I-1 修复：``USING (...) JOIN ... USING (...)`` chained JOIN 应正确合并键。

    第二个 USING(id) 的左源是 Join 子树（输出 schema 已合并），需要 remap
    ``k.left_col``（orders.id 是 source-local 位置 0）→ subtree-local 位置
    （合并后的 'id' 位置 0）。
    """
    c2 = Catalog()
    c2.create_table(
        "users",
        tuple([Column("id", "INT"), Column("name", "TEXT")]),
        root_page_id=2, next_page_id=2,
    )
    c2.create_table(
        "orders",
        tuple([Column("id", "INT"), Column("user_id", "INT")]),
        root_page_id=3, next_page_id=3,
    )
    c2.create_table(
        "audit",
        tuple([Column("id", "INT"), Column("note", "TEXT")]),
        root_page_id=4, next_page_id=4,
    )
    fe = _FakeExecutor(
        catalog=c2,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[1, 1, 100], [2, 2, 200]],
            "audit": [[1, "x"], [2, "y"], [3, "z"]],  # audit.id=3 无匹配
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u JOIN orders o USING (id) "
        "LEFT JOIN audit a USING (id)",
        c2,
    )
    rows, schema = exe.execute_plan(plan)
    # 合并 schema：['id', 'name', 'user_id', 'note']
    assert "id" in schema and schema.count("id") == 1
    # audit.id=3 无左侧匹配（LEFT audit）→ 不应输出未匹配行
    # LEFT audit 表示左表全保留、右表缺则 NULL；audit 是右表
    # 期望 2 行（u=1 匹配 a=1, u=2 匹配 a=2）
    assert len(rows) == 2
    for r in rows:
        assert r[0] in (1, 2)


def test_using_coalesce_picks_right_when_left_null(catalog):
    """USING 合并键 Coalesce：left NULL 时取 right。"""
    from tinydb._join_executor import JoinExecutor as _JE
    from tinydb.plan import Join as _Join
    from tinydb.parser import JoinKey as _JK
    je = _JE(None)
    join = _Join(
        kind="LEFT", left=None, right=None,
        keys=(_JK(label="id", source_left="u", source_right="o",
                   left_col=0, right_col=0),),
        on_expr=(), natural=False,
    )
    left = [None, "a"]
    right = [1, 100]
    out = je._coalesce_row(left, right, join, ["id", "name"], ["id", "total"])
    # 合并键 'id'：left[0]=None → right[0]=1
    assert out[0] == 1
    assert out[1] == "a"
    assert out[2] == 100