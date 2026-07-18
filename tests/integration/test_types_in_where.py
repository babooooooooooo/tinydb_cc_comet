"""Integration tests for WHERE clause strict same-type comparison (Task 18).

Design D6: WHERE comparisons require column type and literal type to match
exactly (both type name AND type_params). These tests exercise the executor
wiring through the SQL surface.

The tests cover only types expressible via the current parser:
- INT/TEXT/BOOL/FLOAT literals (no width prefix)
- DATE / TIME / TIMESTAMP prefix literals
- DECIMAL prefix literals

Cross-type mismatch cases (SMALLINT vs INT, VARCHAR vs TEXT, etc.) that the
parser cannot express as SQL literals are covered in the unit test
``tests/unit/test_validate_compare_types.py``.
"""
import datetime
import pytest

from tinydb.database import Database


# --- happy paths: same-type WHERE evaluates correctly ---------------------


def test_where_int_eq_int_literal():
    """INT col vs INT lit — same type, OK, returns matching row."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        db.execute("INSERT INTO t (id) VALUES (2)")
        rows = db.execute("SELECT * FROM t WHERE id = 1")
        assert len(rows) == 1
        assert rows[0].id == 1


def test_where_text_eq_text_literal():
    """TEXT col vs TEXT lit — same type, OK, returns matching row."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, name TEXT)")
        db.execute("INSERT INTO t (id, name) VALUES (1, 'alice')")
        db.execute("INSERT INTO t (id, name) VALUES (2, 'bob')")
        rows = db.execute("SELECT * FROM t WHERE name = 'alice'")
        assert len(rows) == 1
        assert rows[0].id == 1


def test_where_bool_eq_bool_literal():
    """BOOL col vs BOOL lit — same type, OK, returns matching row."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, active BOOL)")
        db.execute("INSERT INTO t (id, active) VALUES (1, TRUE)")
        db.execute("INSERT INTO t (id, active) VALUES (2, FALSE)")
        rows = db.execute("SELECT * FROM t WHERE active = TRUE")
        assert len(rows) == 1
        assert rows[0].id == 1


def test_where_date_eq_date_literal():
    """DATE col vs DATE '...' lit — same type, OK, returns matching row."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, d DATE)")
        db.execute("INSERT INTO t (id, d) VALUES (1, DATE '2026-07-16')")
        db.execute("INSERT INTO t (id, d) VALUES (2, DATE '2026-07-17')")
        rows = db.execute("SELECT * FROM t WHERE d = DATE '2026-07-16'")
        assert len(rows) == 1
        assert rows[0].id == 1


def test_where_timestamp_eq_timestamp_literal():
    """TIMESTAMP col vs TIMESTAMP '...' lit — same type, OK."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, ts TIMESTAMP)")
        db.execute(
            "INSERT INTO t (id, ts) VALUES (1, TIMESTAMP '2026-07-16 14:30:00')"
        )
        db.execute(
            "INSERT INTO t (id, ts) VALUES (2, TIMESTAMP '2026-07-17 09:00:00')"
        )
        rows = db.execute(
            "SELECT * FROM t WHERE ts = TIMESTAMP '2026-07-16 14:30:00'"
        )
        assert len(rows) == 1
        assert rows[0].id == 1


# --- strict mismatch: cross-type comparisons raise -----------------------


def test_where_int_vs_text_raises():
    """INT col vs TEXT lit — strict mismatch, must raise TypeError."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        with pytest.raises(TypeError):
            db.execute("SELECT * FROM t WHERE id = '1'")


def test_where_int_vs_bool_raises():
    """INT col vs BOOL literal — strict mismatch, must raise TypeError.

    ``bool`` is a subclass of ``int`` in Python so the underlying value is
    valid for the INT codec, but the strict check sees bool as BOOL type
    which differs from INT.
    """
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        with pytest.raises(TypeError):
            db.execute("SELECT * FROM t WHERE id = TRUE")


def test_where_text_vs_int_raises():
    """TEXT col vs INT lit — strict mismatch, must raise TypeError."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (name TEXT)")
        db.execute("INSERT INTO t (name) VALUES ('alice')")
        with pytest.raises(TypeError):
            db.execute("SELECT * FROM t WHERE name = 1")


def test_where_date_vs_text_raises():
    """DATE col vs quoted TEXT lit — strict mismatch, must raise TypeError."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (d DATE)")
        db.execute("INSERT INTO t (d) VALUES (DATE '2026-07-16')")
        with pytest.raises(TypeError):
            db.execute("SELECT * FROM t WHERE d = '2026-07-16'")


def test_where_date_vs_timestamp_raises():
    """DATE col vs TIMESTAMP '...' lit — strict mismatch, must raise TypeError."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (d DATE)")
        db.execute("INSERT INTO t (d) VALUES (DATE '2026-07-16')")
        with pytest.raises(TypeError):
            db.execute(
                "SELECT * FROM t WHERE d = TIMESTAMP '2026-07-16 14:30:00'"
            )


# --- DELETE with strict comparison ---------------------------------------


def test_where_in_delete_path_int_eq():
    """DELETE WHERE id = <int> uses the same eval_expr strict check."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        db.execute("INSERT INTO t (id) VALUES (2)")
        db.execute("DELETE FROM t WHERE id = 1")
        rows = db.execute("SELECT * FROM t")
        assert len(rows) == 1
        assert rows[0].id == 2


def test_where_in_delete_path_int_vs_text_raises():
    """DELETE WHERE id = '<text>' raises via the same strict check."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        with pytest.raises(TypeError):
            db.execute("DELETE FROM t WHERE id = '1'")


# --- UPDATE strict check on WHERE -----------------------------------------


def test_where_in_update_path_text_neq_int_raises():
    """UPDATE WHERE b = 1 against TEXT col b must raise strict mismatch."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (a INT, b TEXT)")
        db.execute("INSERT INTO t (a, b) VALUES (1, 'x')")
        with pytest.raises(TypeError):
            db.execute("UPDATE t SET a = 99 WHERE b = 1")
