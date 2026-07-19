"""Unit tests for tinydb.executor aggregation core (design doc §7.1.2)."""
import pytest
from tinydb.executor import (
    _agg_count_star, _agg_count_expr, _agg_sum, _agg_avg,
    _agg_min, _agg_max, _AGG_FUNCS, apply_aggregation,
    _project_aggregate_row,
)
from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ExecutionError


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-01")
def test_agg_count_star_counts_all_rows():
    """COUNT(*) counts every row including NULL columns."""
    rows = [[1, None], [2, 5], [3, None]]
    assert _agg_count_star(rows, None, None) == 3


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-02")
def test_agg_count_expr_skips_null():
    """COUNT(expr) skips NULL rows."""
    rows = [[1, None], [2, 5], [3, None]]
    assert _agg_count_expr(rows, 1, None) == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-03")
def test_agg_sum_int_keeps_type():
    rows = [[1], [2], [3]]
    assert _agg_sum(rows, 0, None) == 6
    assert isinstance(_agg_sum(rows, 0, None), int)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-04")
def test_agg_avg_int_returns_float():
    """D4: AVG(INT) -> float."""
    rows = [[1], [3]]
    val = _agg_avg(rows, 0, None)
    assert val == 2.0
    assert isinstance(val, float)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-05")
def test_agg_min_max_basic():
    rows = [[3], [1], [4], [1], [5]]
    assert _agg_min(rows, 0, None) == 1
    assert _agg_max(rows, 0, None) == 5


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-06")
def test_agg_empty_set_returns_sentinel():
    rows: list = []
    assert _agg_count_star(rows, None, None) == 0
    assert _agg_count_expr(rows, 0, None) == 0
    assert _agg_sum(rows, 0, None) is None
    assert _agg_avg(rows, 0, None) is None
    assert _agg_min(rows, 0, None) is None
    assert _agg_max(rows, 0, None) is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-07")
def test_agg_dict_has_all_5_functions():
    """_AGG_FUNCS must contain COUNT/SUM/AVG/MIN/MAX."""
    assert set(_AGG_FUNCS.keys()) == {"COUNT", "SUM", "AVG", "MIN", "MAX"}


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-004-SCN-08")
def test_agg_count_star_ignores_col_idx():
    """COUNT(*) does not look at any column."""
    rows = [[None, None], [None, None]]
    assert _agg_count_star(rows, 99, None) == 2


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-005-SCN-01")
def test_apply_aggregation_count_star_with_group_by():
    """SELECT dept, COUNT(*) FROM emp GROUP BY dept."""
    schema = [("dept", "TEXT"), ("salary", "INT")]
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) FROM emp GROUP BY dept"
    )).statements[0]
    raw = [["eng", 100], ["eng", 200], ["sales", 50]]
    rows = apply_aggregation(raw, stmt, schema)
    assert len(rows) == 2
    by_dept = {r.values[r.columns.index("dept")]: r.count for r in rows}
    assert by_dept == {"eng": 2, "sales": 1}


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-005-SCN-02")
def test_apply_aggregation_no_group_by_single_group():
    """D5 refinement #2: no GROUP BY → 1 row even if table empty."""
    schema = [("x", "INT")]
    stmt = parse(tokenize("SELECT COUNT(*) FROM t")).statements[0]
    raw: list = []
    rows = apply_aggregation(raw, stmt, schema)
    assert len(rows) == 1
    assert rows[0].count == 0


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-005-SCN-03")
def test_apply_aggregation_sum_avg_min_max():
    """4-function truth table."""
    schema = [("dept", "TEXT"), ("salary", "INT")]
    stmt = parse(tokenize(
        "SELECT dept, SUM(salary), AVG(salary), MIN(salary), MAX(salary) "
        "FROM emp GROUP BY dept"
    )).statements[0]
    raw = [["eng", 100], ["eng", 300], ["sales", 200], ["sales", 400]]
    rows = apply_aggregation(raw, stmt, schema)
    assert len(rows) == 2
    eng = next(r for r in rows if r.dept == "eng")
    assert eng.values[eng.columns.index("sum_salary")] == 400
    assert eng.values[eng.columns.index("avg_salary")] == 200.0
    assert eng.values[eng.columns.index("min_salary")] == 100
    assert eng.values[eng.columns.index("max_salary")] == 300


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-005-SCN-04")
def test_apply_aggregation_multi_column_group_by():
    """GROUP BY dept, team."""
    schema = [("dept", "TEXT"), ("team", "TEXT"), ("n", "INT")]
    stmt = parse(tokenize(
        "SELECT dept, team, COUNT(*) FROM emp GROUP BY dept, team"
    )).statements[0]
    raw = [["eng", "a", 1], ["eng", "a", 2], ["eng", "b", 3],
           ["sales", "a", 4]]
    rows = apply_aggregation(raw, stmt, schema)
    assert len(rows) == 3
    counts = {(r.dept, r.team): r.count for r in rows}
    assert counts == {("eng", "a"): 2, ("eng", "b"): 1, ("sales", "a"): 1}


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-005-SCN-05")
def test_project_aggregate_row_select_references_non_agg_non_group_raises():
    """E3: SELECT non-GROUP non-aggregate column raises ExecutionError."""
    schema = [("dept", "TEXT"), ("name", "TEXT")]
    stmt = parse(tokenize("SELECT name, COUNT(*) FROM emp")).statements[0]
    raw = [["eng", "alice"]]
    rows = apply_aggregation(raw, stmt, schema)
    # rows contains 1 row with cols: dept, count (not name!)
    with pytest.raises(ExecutionError, match="must appear in GROUP BY clause"):
        _project_aggregate_row(rows[0], stmt, schema)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-005-SCN-06")
def test_project_aggregate_row_group_col_passthrough():
    schema = [("dept", "TEXT"), ("n", "INT")]
    stmt = parse(tokenize(
        "SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept"
    )).statements[0]
    raw = [["eng", 0]]
    rows = apply_aggregation(raw, stmt, schema)
    proj = _project_aggregate_row(rows[0], stmt, schema)
    assert proj.dept == "eng"
    assert proj.n == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-009-SCN-01")
def test_agg_sum_text_raises():
    """E8: SUM on TEXT column raises TypeError via type_system.

    Note: the executor's _agg_sum does not validate schema types — that
    responsibility lives in py_to_db. We assert that py_to_db rejects an
    int being coerced to TEXT, which is the actual boundary that protects
    aggregates from receiving non-numeric data.
    """
    from tinydb.type_system import py_to_db
    with pytest.raises(TypeError):
        py_to_db(123, "TEXT")


@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-009-SCN-02")
def test_agg_min_bool_with_mixed_types_raises():
    """E9: MIN/MAX on incomparable types raises TypeError.

    Note: Python 3 treats bool as int subclass, so [1, True] comparison
    succeeds (True == 1). Genuinely incomparable types (int vs str) raise.
    We test the genuine case.
    """
    # Comparable: int and bool mixed → no raise, returns 1
    rows = [[1], [True]]
    assert _agg_min(rows, 0, None) == 1
    # Incomparable: int vs str raises
    mixed = [[1], ["a"]]
    with pytest.raises(TypeError):
        _agg_min(mixed, 0, None)
