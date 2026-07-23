"""Tests for the LogicalPlan intermediate layer.

Covers Task 5 (plan module + build_plan + format):

- single-table → Scan + Project
- two-table INNER JOIN → left-deep Join + Project
- NATURAL LEFT no common keys → Join(keys=())
- USING keys → Join has 1 JoinKey with correct left/right positions
- WHERE → Filter node
- ORDER BY DESC + LIMIT + OFFSET → Sort + Limit
- plan.format() stable text output contains Project / Join / Scan keywords
- build_plan does not mutate catalog.tables or touch pager.page_count()

Design Doc §3.4 / §4.1 / §5.4.
"""
import pytest

from tinydb.catalog import Catalog, Column
from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.plan import (
    Aggregate, Filter, Join, Limit, LogicalPlan, Project, Scan, Sort,
    build_plan, format_plan,
)


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
        tuple([Column("id", "INT"), Column("user_id", "INT")]),
        root_page_id=3, next_page_id=3,
    )
    return c


def _sel(sql):
    return parse(tokenize(sql)).statements[0]


def test_plan_for_single_table_is_scan_project(catalog):
    plan = build_plan(_sel("SELECT id FROM users"), catalog)
    assert isinstance(plan, LogicalPlan)
    assert isinstance(plan, Project)
    assert isinstance(plan.source, Scan)
    assert plan.source.table == "users"


def test_plan_for_two_table_inner_join_is_left_deep(catalog):
    sql = "SELECT u.id FROM users u INNER JOIN orders o ON u.id = o.user_id"
    plan = build_plan(_sel(sql), catalog)
    # 顶层为 Project，下层为 Join(INNER, Scan(u), Scan(o))
    assert isinstance(plan, Project)
    inner = plan.source
    assert isinstance(inner, Join)
    assert inner.kind == "INNER"
    assert isinstance(inner.left, Scan)
    assert isinstance(inner.right, Scan)
    assert inner.left.table == "users"
    assert inner.right.table == "orders"


def test_plan_natural_left_join_no_common_keys_yields_empty_keys(catalog):
    # users 与 audit 无共同列（需创建 audit）
    catalog.create_table(
        "audit",
        tuple([Column("ts", "INT")]), root_page_id=4, next_page_id=4,
    )
    sql = "SELECT * FROM users NATURAL LEFT JOIN audit"
    plan = build_plan(_sel(sql), catalog)
    join = plan.source
    assert isinstance(join, Join)
    assert join.kind == "LEFT"
    assert join.keys == ()


def test_plan_using_keys_record_left_and_right_positions(catalog):
    sql = "SELECT * FROM users u JOIN orders o USING (id)"
    plan = build_plan(_sel(sql), catalog)
    join = plan.source
    assert isinstance(join, Join)
    assert len(join.keys) == 1
    key = join.keys[0]
    assert key.label == "id" and key.left_col == 0 and key.right_col == 0


def test_plan_constructs_filter_from_where(catalog):
    sql = "SELECT id FROM users WHERE id = 1"
    plan = build_plan(_sel(sql), catalog)
    # 顶层 Project -> Filter(source=Scan) -> Scan
    assert isinstance(plan, Project)
    assert isinstance(plan.source, Filter)
    assert isinstance(plan.source.source, Scan)


def test_plan_constructs_sort_and_limit(catalog):
    sql = "SELECT id FROM users ORDER BY id DESC LIMIT 5 OFFSET 2"
    plan = build_plan(_sel(sql), catalog)
    # Project -> Limit(source=Sort(source=Scan))
    assert isinstance(plan, Project)
    assert isinstance(plan.source, Limit)
    assert isinstance(plan.source.source, Sort)


def test_plan_format_stable_text(catalog):
    sql = "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id"
    plan = build_plan(_sel(sql), catalog)
    text = format_plan(plan)
    assert "Project" in text
    assert "Join(INNER" in text
    assert "Scan(users AS u" in text
    assert "Scan(orders AS o" in text


def test_plan_construct_does_not_mutate_catalog(catalog):
    sql = "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id"
    keys_before = set(catalog.tables.keys())
    plan = build_plan(_sel(sql), catalog)
    keys_after = set(catalog.tables.keys())
    assert keys_before == keys_after


def test_plan_construct_does_not_touch_pager(tmp_path):
    # property-like: 通过 Database 验证 pager / WAL 不变。
    import tinydb
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    try:
        d.execute("CREATE TABLE a(id INT)")
        d.execute("CREATE TABLE b(id INT)")
        d.execute("INSERT INTO a(id) VALUES (1)")
        # 记录 page_count
        pc_before = d.pager.page_count()
        # 触发 build_plan
        from tinydb.parser import parse
        from tinydb.tokenizer import tokenize
        from tinydb.plan import build_plan
        ast = parse(tokenize("SELECT a.id FROM a JOIN b ON a.id = b.id")).statements[0]
        plan = build_plan(ast, d.catalog)
        assert d.pager.page_count() == pc_before
    finally:
        d.close()