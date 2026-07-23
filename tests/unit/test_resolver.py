"""tinydb-join-query (T4): Resolver 模块单元测试。

覆盖：来源映射、合并 schema、USING/NATURAL JoinKey、列位置解析、GROUP BY/ORDER BY
限定列解析，以及 6 个 ResolutionError 子类型的触发。
"""
import pytest

from tinydb.catalog import Catalog, Column
from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.resolver import (
    resolve,
    UnknownSource, UnknownQualifiedColumn, AmbiguousColumn,
    DuplicateAlias, MissingUsingKey, IncompatibleKeyTypes,
)


@pytest.fixture
def catalog():
    """users(id, name) + orders(id, user_id, total) + audit(ts)。

    users 与 orders 共有 id 列；users 与 audit 无共同列，用于 NATURAL 退化测试。
    """
    c = Catalog()
    c.create_table(
        "users",
        tuple([Column("id", "INT"), Column("name", "TEXT")]),
        root_page_id=2, next_page_id=2,
    )
    c.create_table(
        "orders",
        tuple([
            Column("id", "INT"),
            Column("user_id", "INT"),
            Column("total", "INT"),
        ]),
        root_page_id=3, next_page_id=3,
    )
    c.create_table(
        "audit",
        tuple([Column("ts", "INT")]),  # 与 users 无共同列
        root_page_id=4, next_page_id=4,
    )
    return c


def _sel(sql):
    """Parse SQL and return the first Select statement."""
    return parse(tokenize(sql)).statements[0]


# --- 来源映射 / 单表保留裸列 ----------------------------------------------


def test_resolve_single_table_keeps_bare_columns(catalog):
    plan = resolve(_sel("SELECT id, name FROM users"), catalog)
    assert len(plan.sources) == 1
    src = plan.sources[0]
    assert src.source_id == "users" and src.table_name == "users"
    assert src.schema == ("id", "name")
    assert plan.output_schema == ("id", "name")


def test_resolve_two_table_join_with_alias(catalog):
    sql = "SELECT u.id, o.id FROM users u INNER JOIN orders o ON u.id = o.user_id"
    plan = resolve(_sel(sql), catalog)
    assert [s.source_id for s in plan.sources] == ["u", "o"]
    assert plan.sources[0].alias == "u"
    assert plan.sources[1].alias == "o"


def test_resolve_duplicate_alias_raises(catalog):
    sql = "SELECT * FROM users u JOIN orders u ON users.id = u.user_id"
    with pytest.raises(DuplicateAlias):
        resolve(_sel(sql), catalog)


def test_resolve_unknown_table_raises(catalog):
    sql = "SELECT * FROM users JOIN ghost ON users.id = ghost.user_id"
    with pytest.raises(UnknownSource):
        resolve(_sel(sql), catalog)


# --- 裸列 / 限定列解析 ----------------------------------------------------


def test_resolve_ambiguous_unqualified_column(catalog):
    # bare ``id`` 在 ON 中同时匹配 users.id 与 orders.id —— 必须显式限定。
    sql = "SELECT * FROM users u INNER JOIN orders o ON id = user_id"
    with pytest.raises(AmbiguousColumn):
        resolve(_sel(sql), catalog)


def test_resolve_qualified_column_binds_correct_source(catalog):
    sql = "SELECT u.id FROM users u INNER JOIN orders o ON u.id = o.user_id"
    plan = resolve(_sel(sql), catalog)
    # ``u.id`` 应当解析到 users source 的位置 0。
    pos, src = plan.column_resolver(("u", "id"))
    assert pos == 0 and src.source_id == "u"


def test_resolve_unknown_qualified_column(catalog):
    sql = "SELECT * FROM users u INNER JOIN orders o ON u.missing = o.id"
    with pytest.raises(UnknownQualifiedColumn):
        resolve(_sel(sql), catalog)


# --- USING / NATURAL --------------------------------------------------------


def test_resolve_using_keys_creates_merged_key(catalog):
    sql = "SELECT * FROM users u JOIN orders o USING (id)"
    plan = resolve(_sel(sql), catalog)
    assert len(plan.merged_keys) == 1
    key = plan.merged_keys[0]
    assert key.label == "id" and key.left_col == 0 and key.right_col == 0


def test_resolve_using_missing_column_raises(catalog):
    sql = "SELECT * FROM users u JOIN orders o USING (missing_col)"
    with pytest.raises(MissingUsingKey):
        resolve(_sel(sql), catalog)


def test_resolve_natural_join_discovers_common_columns(catalog):
    # users(id, name) 与 orders(id, user_id, total) 共同列 = id
    sql = "SELECT * FROM users NATURAL INNER JOIN orders"
    plan = resolve(_sel(sql), catalog)
    assert [k.label for k in plan.merged_keys] == ["id"]


def test_resolve_natural_join_no_common_columns_yields_no_keys(catalog):
    sql = "SELECT * FROM users NATURAL LEFT JOIN audit"
    plan = resolve(_sel(sql), catalog)
    # keys 为空 → 执行层走 CROSS；kind 仍为 LEFT
    assert plan.merged_keys == ()
    assert plan.outer_kind == "LEFT"


def test_resolve_using_incompatible_types_raises():
    c = Catalog()
    c.create_table(
        "a", tuple([Column("k", "INT")]), root_page_id=2, next_page_id=2,
    )
    c.create_table(
        "b", tuple([Column("k", "TEXT")]), root_page_id=3, next_page_id=3,
    )
    sql = "SELECT * FROM a JOIN b USING (k)"
    with pytest.raises(IncompatibleKeyTypes):
        resolve(_sel(sql), c)


# --- ON 复合谓词：parser 阶段不支持，Task 8 实现 ----------------------------


@pytest.mark.skip(
    reason="Task 8 implements complex AND/OR/NOT in JOIN ON; "
    "resolver path ready but parser currently raises ParseError "
    "for composed ON predicates."
)
def test_resolve_on_composed_predicate_resolves_positions(catalog):
    sql = (
        "SELECT * FROM users u JOIN orders o "
        "ON u.id = o.user_id AND (o.total > 10 OR o.total = 0)"
    )
    plan = resolve(_sel(sql), catalog)
    # on_resolved 是已 fold 的 (left_pos, op, right_pos / lit) 列表
    assert isinstance(plan.on_resolved, tuple)
    assert len(plan.on_resolved) >= 1


# --- GROUP BY / ORDER BY 限定列解析 ----------------------------------------


def test_resolve_qualified_order_by_and_group_by(catalog):
    sql = (
        "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id "
        "GROUP BY u.id ORDER BY u.id"
    )
    plan = resolve(_sel(sql), catalog)
    # group_resolved / order_resolved 至少各 1 项
    assert len(plan.group_resolved) == 1
    assert len(plan.order_resolved) == 1