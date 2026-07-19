"""Unit tests for HAVING clause (design doc §5.1, §5.2, §7.1.3) and ORDER BY phase1."""
import pytest
from tinydb.parser import OrderByItem, parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ExecutionError
from tinydb.executor import (
    apply_aggregation, apply_having, apply_order_limit_phase1,
)


def _make_agg_rows(raw, sql):
    """Helper: parse SQL, run apply_aggregation on raw."""
    schema = [("dept", "TEXT"), ("salary", "INT"), ("n", "INT")]
    stmt = parse(tokenize(sql)).statements[0]
    return apply_aggregation(raw, stmt, schema)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-007-SCN-01")
def test_having_alias_filter_only_aggregate_rows():
    """HAVING alias filters aggregate rows."""
    raw = [["eng", 100], ["eng", 200], ["eng", 300], ["sales", 50]]
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept HAVING n > 1"
    )).statements[0]
    rows = _make_agg_rows(raw, "SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept")
    rows = apply_having(rows, stmt.having, stmt.aggregate_aliases,
                        stmt.group_by, schema=None)
    assert len(rows) == 1
    assert rows[0].dept == "eng"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-007-SCN-02")
def test_having_group_col_reference():
    """HAVING references GROUP BY column."""
    raw = [["eng", 100], ["sales", 200]]
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) FROM emp GROUP BY dept HAVING dept = 'eng'"
    )).statements[0]
    rows = _make_agg_rows(raw, "SELECT dept, COUNT(*) FROM emp GROUP BY dept")
    rows = apply_having(rows, stmt.having, stmt.aggregate_aliases,
                        stmt.group_by, schema=None)
    assert len(rows) == 1
    assert rows[0].dept == "eng"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-007-SCN-03")
def test_having_unknown_col_raises():
    """E2: HAVING references unknown column raises ExecutionError."""
    raw = [["eng", 100]]
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) FROM emp GROUP BY dept HAVING foo > 5"
    )).statements[0]
    rows = _make_agg_rows(raw, "SELECT dept, COUNT(*) FROM emp GROUP BY dept")
    with pytest.raises(ExecutionError, match="unknown column 'foo' in HAVING"):
        apply_having(rows, stmt.having, stmt.aggregate_aliases,
                     stmt.group_by, schema=None)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-008-SCN-01")
def test_order_by_group_col_ascending():
    """ORDER BY group column ascending."""
    raw = [["c", 1], ["a", 2], ["b", 3]]
    rows = _make_agg_rows(raw, "SELECT dept, COUNT(*) FROM t GROUP BY dept")
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) FROM t GROUP BY dept ORDER BY dept"
    )).statements[0]
    rows = apply_order_limit_phase1(rows, stmt.order_by, stmt.aggregate_aliases,
                                    stmt.group_by)
    assert [r.dept for r in rows] == ["a", "b", "c"]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-008-SCN-02")
def test_order_by_alias_descending():
    """ORDER BY aggregate alias descending."""
    raw = [["a", 1], ["a", 2], ["a", 3], ["b", 4]]
    rows = _make_agg_rows(
        raw, "SELECT dept, COUNT(*) AS n FROM t GROUP BY dept",
    )
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) AS n FROM t GROUP BY dept ORDER BY n DESC"
    )).statements[0]
    rows = apply_order_limit_phase1(rows, stmt.order_by, stmt.aggregate_aliases,
                                    stmt.group_by)
    assert [r.n for r in rows] == [3, 1]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-008-SCN-03")
def test_order_by_unknown_col_raises():
    """E7: ORDER BY column not in GROUP BY or alias raises."""
    raw = [["a", 1]]
    rows = _make_agg_rows(raw, "SELECT dept, COUNT(*) FROM t GROUP BY dept")
    bad_order = (OrderByItem(column="z"),)
    with pytest.raises(ExecutionError, match="ORDER BY column 'z'"):
        apply_order_limit_phase1(rows, bad_order, ("count",), ("dept",))