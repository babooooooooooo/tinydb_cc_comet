"""Unit tests for tinydb.aggregation parser (Tasks 2-3 of design doc §7.1.1).

Task 2 covers the new AST dataclasses (AggregateCall, SelectItem, OrderByItem)
plus the extended Select dataclass with aggregate / group_by / having / order /
limit / offset fields.

The 5 parse-based scenarios below are REQ-AGG-002 SCN-01..04. They depend on
parser logic that is wired in by Task 3 (GROUP BY / HAVING / aggregate call
parsing in `_parse_select`). At T2 only the AST nodes exist; those scenarios
are expected to fail with a ParseError until T3 lands.
"""
import dataclasses
import pytest
from tinydb.parser import (
    parse,
    AggregateCall,
    SelectItem,
    OrderByItem,
    Select,
)
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError


# --- T2 GREEN proof: AST dataclasses exist and are well-shaped --------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-01")
def test_ast_aggregate_call_fields_exist():
    """AggregateCall has func/arg/alias/line/col fields (T2 GREEN proof)."""
    fields = {f.name for f in dataclasses.fields(AggregateCall)}
    assert {"func", "arg", "alias", "line", "col"} <= fields


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-01")
def test_ast_select_item_fields_exist():
    """SelectItem has kind/name/alias/aggregate fields (T2 GREEN proof)."""
    fields = {f.name for f in dataclasses.fields(SelectItem)}
    assert {"kind", "name", "alias", "aggregate"} <= fields


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-01")
def test_ast_order_by_item_fields_exist():
    """OrderByItem has column/descending fields (T2 GREEN proof)."""
    fields = {f.name for f in dataclasses.fields(OrderByItem)}
    assert {"column", "descending"} <= fields


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-01")
def test_ast_select_extended_with_aggregate_fields():
    """Select accepts new aggregate / group_by / having / order / limit / offset fields."""
    fields = {f.name for f in dataclasses.fields(Select)}
    required = {
        "select_items",
        "group_by",
        "having",
        "aggregate_aliases",
        "order_by",
        "limit",
        "offset",
    }
    assert required <= fields
    # Construct with the new fields — must not raise.
    s = Select(
        table="t",
        columns=["*"],
        where=None,
        line=1,
        col=1,
        select_items=(),
        group_by=(),
        having=None,
        aggregate_aliases=(),
        order_by=(),
        limit=None,
        offset=0,
    )
    assert s.select_items == ()
    assert s.limit is None
    assert s.offset == 0


# --- REQ-AGG-002 SCN-01..04: parse-level scenarios -------------------------
# These depend on Task 3 parser wiring and are expected to fail at T2 with a
# ParseError. They become GREEN once T3 lands.


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-01")
def test_parser_aggregate_call_star():
    """COUNT(*) parsed as AggregateCall with arg='*'."""
    stmt = parse(tokenize("SELECT COUNT(*) FROM t")).statements[0]
    assert len(stmt.select_items) == 1
    item = stmt.select_items[0]
    assert item.kind == "aggregate"
    assert item.aggregate.func == "COUNT"
    assert item.aggregate.arg == "*"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-02")
def test_parser_aggregate_call_expr():
    """SUM(x) parsed as AggregateCall with arg=('column', 'x')."""
    stmt = parse(tokenize("SELECT SUM(salary) FROM emp")).statements[0]
    item = stmt.select_items[0]
    assert item.kind == "aggregate"
    assert item.aggregate.func == "SUM"
    assert item.aggregate.arg == ("column", "salary")


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-03")
def test_parser_aggregate_call_with_alias():
    """AS alias stored on AggregateCall."""
    stmt = parse(tokenize("SELECT COUNT(*) AS n FROM t")).statements[0]
    item = stmt.select_items[0]
    assert item.aggregate.func == "COUNT"
    assert item.aggregate.alias == "n"
    assert item.alias == "n"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-04")
def test_parser_aggregate_default_alias_count_star():
    """COUNT(*) default alias = 'count'."""
    stmt = parse(tokenize("SELECT COUNT(*) FROM t")).statements[0]
    assert stmt.aggregate_aliases == ("count",)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-002-SCN-04")
def test_parser_aggregate_default_alias_sum_x():
    """SUM(x) default alias = 'sum_x'."""
    stmt = parse(tokenize("SELECT SUM(salary) FROM emp")).statements[0]
    assert stmt.aggregate_aliases == ("sum_salary",)


# --- Task 3 wire-in: GROUP BY / HAVING / ORDER BY / LIMIT / OFFSET ----------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-003-SCN-01")
def test_parser_group_by_single_column():
    stmt = parse(tokenize("SELECT dept, COUNT(*) FROM emp GROUP BY dept")).statements[0]
    assert stmt.group_by == ("dept",)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-003-SCN-02")
def test_parser_group_by_multi_column():
    stmt = parse(tokenize(
        "SELECT dept, team, COUNT(*) FROM emp GROUP BY dept, team"
    )).statements[0]
    assert stmt.group_by == ("dept", "team")


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-003-SCN-03")
def test_parser_having_with_alias_reference():
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept HAVING n > 5"
    )).statements[0]
    assert stmt.having == ("n", ">", 5)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-003-SCN-04")
def test_parser_having_with_group_col_reference():
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) FROM emp GROUP BY dept HAVING dept = 'eng'"
    )).statements[0]
    assert stmt.having == ("dept", "=", "eng")


# --- Task 4: WHERE restrictions (E1 aggregate-in-WHERE, E4 duplicate alias)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-006-SCN-01")
def test_parser_aggregate_in_where_raises_error():
    """E1: WHERE cannot contain aggregate function (design doc §6)."""
    with pytest.raises(ParseError, match="not allowed in WHERE"):
        parse(tokenize("SELECT * FROM t WHERE COUNT(*) > 5"))


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-003-SCN-05")
def test_parser_duplicate_alias_raises():
    """E4: SELECT alias must be unique (design doc §6)."""
    with pytest.raises(ParseError, match="duplicate alias"):
        parse(tokenize("SELECT a AS x, b AS x FROM t"))