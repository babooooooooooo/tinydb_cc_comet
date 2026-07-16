"""Parser AST + structural tests for engine-v1 (Task 2).

Locks in the new dataclasses (EqualsExpr, AndExpr, OrExpr, NotExpr,
OrderByItem, Update) and the upgraded Select (frozen=True, tuple columns,
defaults for order_by/limit/offset, line/col default to 0).
"""
import pytest
from tinydb.parser import (
    EqualsExpr, AndExpr, OrExpr, NotExpr,
    OrderByItem, Update, Select,
)
from tinydb.errors import ParseError
from tinydb.tokenizer import tokenize
from tinydb.parser import parse


def _parse(sql: str):
    return parse(tokenize(sql)).statements[0]


@pytest.mark.unit
def test_ast_equals_expr_dataclass():
    e = EqualsExpr(column="x", value=1)
    assert e.column == "x" and e.value == 1


@pytest.mark.unit
def test_ast_and_or_not_dataclass():
    a = AndExpr(left=EqualsExpr("a", 1), right=OrExpr(
        left=EqualsExpr("b", 2),
        right=NotExpr(operand=EqualsExpr("c", 3))))
    assert isinstance(a, AndExpr)


@pytest.mark.unit
def test_ast_order_by_item_dataclass():
    o = OrderByItem(column="x", descending=True)
    assert o.descending is True


@pytest.mark.unit
def test_ast_update_dataclass():
    u = Update(table="t",
               sets=(("a", EqualsExpr("a", 1)),),
               where=EqualsExpr("b", 2),
               line=1, col=1)
    assert u.table == "t" and len(u.sets) == 1


@pytest.mark.unit
def test_ast_select_defaults_compatible_with_mvp():
    # Backward compat: positional/keyword args used by MVP still work
    s = Select(table="t", columns=("x",), line=1, col=1)
    assert s.where is None
    assert s.order_by == ()
    assert s.limit is None
    assert s.offset is None


@pytest.mark.unit
def test_ast_select_frozen():
    # frozen=True means assignments raise FrozenInstanceError.
    from dataclasses import FrozenInstanceError
    s = Select(table="t", columns=("x",), line=1, col=1)
    with pytest.raises(FrozenInstanceError):
        s.table = "u"  # type: ignore[misc]


@pytest.mark.unit
def test_ast_select_columns_is_tuple():
    s = Select(table="t", columns=("x",), line=1, col=1)
    assert isinstance(s.columns, tuple)
    assert s.columns == ("x",)


# --- Task 4: UPDATE statement ----------------------------------------------


@pytest.mark.unit
def test_parse_update_basic():
    stmt = _parse("UPDATE t SET a=1 WHERE b=2")
    assert isinstance(stmt, Update)
    assert stmt.table == "t"
    assert len(stmt.sets) == 1
    assert stmt.sets[0][0] == "a"
    assert isinstance(stmt.sets[0][1], EqualsExpr)
    assert stmt.sets[0][1].value == 1
    assert isinstance(stmt.where, EqualsExpr)
    assert stmt.where.column == "b"


@pytest.mark.unit
def test_parse_update_multi_set():
    stmt = _parse("UPDATE t SET a=1, b='x'")
    assert [s[0] for s in stmt.sets] == ["a", "b"]
    assert stmt.where is None


@pytest.mark.unit
def test_parse_update_no_set_raises():
    with pytest.raises(ParseError):
        _parse("UPDATE t")


@pytest.mark.unit
def test_parse_update_set_rhs_expr_raises():
    # '*' is PUNCT, not a literal token; parser rejects it as the RHS.
    with pytest.raises(ParseError):
        _parse("UPDATE t SET a=*")


@pytest.mark.unit
def test_parse_update_missing_comma_raises():
    with pytest.raises(ParseError):
        _parse("UPDATE t SET a=1 b=2")