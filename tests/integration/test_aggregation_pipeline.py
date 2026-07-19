"""Integration tests for the 5-phase ``_exec_select`` aggregation pipeline.

These tests drive full SQL statements through ``Database.execute`` (the
public API) so they exercise the rewrite end-to-end: parser -> Executor
-> Row shaping -> database pass-through. They cover:

* Group-by path (no HAVING, no ORDER, no LIMIT) -- exercises phase 2 + 5.
* Group-by + HAVING -- exercises phase 2 + 3 + 5.
* Group-by + HAVING + ORDER + LIMIT + OFFSET -- exercises phase 2 + 3 + 4 + 5.
* Backward compat: plain ``SELECT *`` / ``SELECT col`` / ``SELECT WHERE`` still
  return rows shaped exactly as before the aggregation work landed.

Design doc: §5.3 (5-phase pipeline), §7.2 (integration tests).
"""
import pytest

import tinydb


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "agg.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup_emp(db):
    db.execute("CREATE TABLE emp(dept TEXT, salary INT, team TEXT)")
    db.execute("INSERT INTO emp(dept, salary, team) VALUES ('eng', 100, 'a')")
    db.execute("INSERT INTO emp(dept, salary, team) VALUES ('eng', 200, 'a')")
    db.execute("INSERT INTO emp(dept, salary, team) VALUES ('eng', 300, 'b')")
    db.execute("INSERT INTO emp(dept, salary, team) VALUES ('sales', 50, 'a')")
    db.execute("INSERT INTO emp(dept, salary, team) VALUES ('sales', 400, 'b')")


# --- aggregation path ------------------------------------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-01")
def test_group_by_count_basic_uses_5phase(db):
    """Phase 2 (aggregation) + Phase 5 (projection) without HAVING/ORDER."""
    _setup_emp(db)
    rows = db.execute("SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept")
    by_dept = {r.dept: r.n for r in rows}
    assert by_dept == {"eng": 3, "sales": 2}


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-02")
def test_group_by_with_having_uses_phase3(db):
    """Phase 2 + Phase 3 (HAVING) + Phase 5."""
    _setup_emp(db)
    rows = db.execute(
        "SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept HAVING n > 1"
    )
    depts = {r.dept for r in rows}
    assert depts == {"eng", "sales"}
    # Both groups have count > 1 (eng=3, sales=2).


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-03")
def test_group_by_with_order_limit_offset_uses_phase4(db):
    """Phase 2 + Phase 4 (ORDER BY + LIMIT + OFFSET) + Phase 5."""
    _setup_emp(db)
    # After GROUP BY: eng has n=3, sales has n=2.
    # ORDER BY n DESC => [eng(3), sales(2)].
    # OFFSET 1 LIMIT 1 => [sales(2)].
    rows = db.execute(
        "SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept "
        "ORDER BY n DESC LIMIT 1 OFFSET 1"
    )
    assert len(rows) == 1
    assert rows[0].dept == "sales"
    assert rows[0].n == 2


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-04")
def test_no_group_by_with_count_returns_single_row(db):
    """Refinement #2: no GROUP BY + COUNT(*) on non-empty table -> one row."""
    db.execute("CREATE TABLE t(x INT)")
    db.execute("INSERT INTO t(x) VALUES (10)")
    db.execute("INSERT INTO t(x) VALUES (20)")
    rows = db.execute("SELECT COUNT(*) FROM t")
    assert len(rows) == 1
    assert rows[0].count == 2


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-05")
def test_sum_with_where_filter_phase1_then_phase2(db):
    """WHERE (phase 1) -> aggregate (phase 2) -> project (phase 5).

    MVP WHERE only supports ``=``; pick a value that keeps rows from more
    than one dept so the GROUP BY still produces two output rows.
    Filtering by ``team = 'a'`` drops the 'b' team rows and exercises the
    where -> aggregate transition.
    """
    _setup_emp(db)
    # After WHERE team = 'a': (eng,100), (eng,200), (sales,50)
    rows = db.execute(
        "SELECT dept, SUM(salary) AS total "
        "FROM emp WHERE team = 'a' GROUP BY dept"
    )
    by_dept = {r.dept: r.total for r in rows}
    assert by_dept == {"eng": 300, "sales": 50}


# --- backward compat: legacy SELECT without any aggregate clause ----------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-06")
def test_legacy_select_star_unchanged(db):
    """Plain ``SELECT *`` must still return all schema columns in order."""
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    db.execute("INSERT INTO t(id, name) VALUES (1, 'alice')")
    db.execute("INSERT INTO t(id, name) VALUES (2, 'bob')")
    rows = db.execute("SELECT * FROM t")
    assert len(rows) == 2
    assert rows[0].id == 1
    assert rows[0].name == "alice"
    assert rows[1].id == 2
    assert rows[1].name == "bob"
    # Schema column order preserved.
    assert rows[0].columns == ("id", "name")


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-07")
def test_legacy_select_named_columns_unchanged(db):
    """Plain ``SELECT col1, col2`` must still project in declared order."""
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    db.execute("INSERT INTO t(id, name) VALUES (1, 'alice')")
    rows = db.execute("SELECT name, id FROM t")
    assert rows[0].columns == ("name", "id")
    assert rows[0].name == "alice"
    assert rows[0].id == 1


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-08")
def test_legacy_select_where_equality_unchanged(db):
    """``SELECT * FROM t WHERE col = lit`` keeps MVP behavior."""
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    db.execute("INSERT INTO t(id, name) VALUES (1, 'alice')")
    db.execute("INSERT INTO t(id, name) VALUES (2, 'bob')")
    rows = db.execute("SELECT * FROM t WHERE id = 2")
    assert len(rows) == 1
    assert rows[0].id == 2
    assert rows[0].name == "bob"


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-PIPE-09")
def test_legacy_select_empty_table_returns_empty_list(db):
    """No rows -> empty list, both for ``SELECT *`` and ``SELECT col``."""
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    assert db.execute("SELECT * FROM t") == []
    assert db.execute("SELECT id FROM t") == []


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-010-SCN-01")
def test_empty_table_no_group_by_returns_one_row(db):
    """E10: empty table + no GROUP BY → 1 row, count=0, sum/avg=None."""
    db.execute("CREATE TABLE t(x INT)")
    rows = db.execute("SELECT COUNT(*), SUM(x), AVG(x) FROM t")
    assert len(rows) == 1
    assert rows[0].count == 0
    assert rows[0].sum_x is None
    assert rows[0].avg_x is None


@pytest.mark.integration
@pytest.mark.spec_id("REQ-AGG-010-SCN-02")
def test_empty_table_with_group_by_returns_zero_rows(db):
    """E10: empty table + GROUP BY → 0 rows."""
    db.execute("CREATE TABLE t(dept TEXT, x INT)")
    rows = db.execute("SELECT dept, COUNT(*) FROM t GROUP BY dept")
    assert rows == []