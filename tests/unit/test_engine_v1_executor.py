"""eval_expr unit tests for engine-v1 (Task 7).

Locks in recursive-descent expression evaluator semantics: EQ / AND / OR /
NOT, plus strict-type + unknown-column error paths.

Note on type mismatch: eval_expr raises ``TypeError`` (preserving MVP
behavior via ``py_to_db``) on direct EqualsExpr calls. When the EqualsExpr
is a sub-expression reached only after AND/OR short-circuit, the bad branch
is never evaluated (Python ``and``/``or`` short-circuit), so the type
mismatch is hidden — which is the desired behavior.
"""
import pytest

from tinydb.executor import eval_expr
from tinydb.parser import EqualsExpr, AndExpr, OrExpr, NotExpr
from tinydb.errors import ExecutionError


SCHEMA = [("a", "INT"), ("b", "INT"), ("c", "INT")]


@pytest.mark.unit
def test_eval_expr_equals_basic():
    row = [1, 2, 3]
    assert eval_expr(EqualsExpr("a", 1), row, SCHEMA) is True
    assert eval_expr(EqualsExpr("a", 9), row, SCHEMA) is False


@pytest.mark.unit
def test_eval_expr_and_short_circuits_left_false():
    # Right side raises; left is False so AND short-circuits and never raises
    bad = EqualsExpr("nonexistent", 1)  # would raise ExecutionError
    expr = AndExpr(left=EqualsExpr("a", 999), right=bad)
    assert eval_expr(expr, [1, 2, 3], SCHEMA) is False


@pytest.mark.unit
def test_eval_expr_or_short_circuits_left_true():
    bad = EqualsExpr("nonexistent", 1)
    expr = OrExpr(left=EqualsExpr("a", 1), right=bad)
    assert eval_expr(expr, [1, 2, 3], SCHEMA) is True


@pytest.mark.unit
def test_eval_expr_not_negates():
    expr = NotExpr(operand=EqualsExpr("a", 1))
    assert eval_expr(expr, [1, 2, 3], SCHEMA) is False
    assert eval_expr(expr, [9, 2, 3], SCHEMA) is True


@pytest.mark.unit
def test_eval_expr_unknown_column_raises():
    with pytest.raises(ExecutionError):
        eval_expr(EqualsExpr("z", 1), [1, 2, 3], SCHEMA)


@pytest.mark.unit
def test_eval_expr_type_mismatch_raises():
    # a is INT, but literal is str
    with pytest.raises(TypeError):
        eval_expr(EqualsExpr("a", "x"), [1, 2, 3], SCHEMA)


@pytest.mark.unit
def test_eval_expr_nested_and_or_not():
    # a=1 AND NOT (b=2 OR c=3)
    expr = AndExpr(
        left=EqualsExpr("a", 1),
        right=NotExpr(operand=OrExpr(
            left=EqualsExpr("b", 2),
            right=EqualsExpr("c", 3),
        )),
    )
    # Row 1: a=1, b=2, c=99 — b=2 is True, OR short-circuits True, NOT False,
    # AND True AND False -> False
    assert eval_expr(expr, [1, 2, 99], SCHEMA) is False
    # Row 2: a=1, b=9, c=99 — b=2 False, c=3 False, OR False, NOT True,
    # AND True AND True -> True
    assert eval_expr(expr, [1, 9, 99], SCHEMA) is True


# --- Task 9: SELECT chain (ORDER BY + LIMIT + OFFSET) ---------------------


import os
import tempfile
from tinydb.database import Database


def _db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    os.unlink(path)
    db = Database(path)
    return db, path


@pytest.mark.integration
def test_executor_select_sorts_and_slices():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT, b INT)")
        for i, v in enumerate([3, 1, 4, 1, 5, 9, 2, 6]):
            db.execute(f"INSERT INTO t (a, b) VALUES ({v}, {i})")
        out = db.execute("SELECT * FROM t ORDER BY a ASC LIMIT 3")
        assert [r.a for r in out] == [1, 1, 2]
        out = db.execute("SELECT * FROM t ORDER BY a DESC LIMIT 3")
        assert [r.a for r in out] == [9, 6, 5]
        out = db.execute("SELECT * FROM t ORDER BY a ASC OFFSET 5")
        assert [r.a for r in out] == [5, 6, 9]
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_select_order_by_stable_when_tied():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT, b INT)")
        for i, k in enumerate([2, 1, 2, 1, 2]):
            db.execute(f"INSERT INTO t (a, b) VALUES ({k}, {i})")
        out = db.execute("SELECT b FROM t ORDER BY a ASC")
        # b values where a=1: 1, 3; where a=2: 0, 2, 4
        assert [r.b for r in out] == [1, 3, 0, 2, 4]
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_select_limit_zero():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT)")
        db.execute("INSERT INTO t (a) VALUES (1)")
        assert db.execute("SELECT * FROM t LIMIT 0") == []
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_select_offset_beyond_rows():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT)")
        db.execute("INSERT INTO t (a) VALUES (1), (2)")
        assert db.execute("SELECT * FROM t OFFSET 10") == []
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_select_offset_negative_raises():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT)")
        with pytest.raises(ExecutionError):
            db.execute("SELECT * FROM t OFFSET -1")
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_select_order_by_unknown_column_raises():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT)")
        db.execute("INSERT INTO t (a) VALUES (1)")
        with pytest.raises(ExecutionError):
            db.execute("SELECT * FROM t ORDER BY z")
    finally:
        os.unlink(path)


# --- Task 10: UPDATE executor ---------------------------------------------


@pytest.mark.integration
def test_executor_update_in_place_no_grow():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT, b TEXT)")
        db.execute("INSERT INTO t (a, b) VALUES (1, 'x')")
        out = db.execute("UPDATE t SET a=99 WHERE b='x'")
        assert out == []  # DML protocol
        rows = db.execute("SELECT a, b FROM t")
        assert [r.a for r in rows] == [99]
        assert [r.b for r in rows] == ["x"]
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_update_in_place_shrink():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT, b TEXT)")
        db.execute("INSERT INTO t (a, b) VALUES (1, 'hello world')")
        db.execute("UPDATE t SET b='hi'")
        rows = db.execute("SELECT a, b FROM t")
        assert [r.a for r in rows] == [1]
        assert [r.b for r in rows] == ["hi"]
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_update_compound_where():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT, b INT)")
        for a, b in [(1, 1), (1, 2), (2, 1)]:
            db.execute(f"INSERT INTO t (a, b) VALUES ({a}, {b})")
        db.execute("UPDATE t SET b=99 WHERE a=1 AND b=2")
        rows = db.execute("SELECT b FROM t ORDER BY a ASC, b ASC")
        # a=1,b=1 unchanged; a=1,b=2 -> 99; a=2,b=1 unchanged
        assert [r.b for r in rows] == [1, 99, 1]
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_update_no_where_updates_all():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT)")
        db.execute("INSERT INTO t (a) VALUES (1), (2), (3)")
        db.execute("UPDATE t SET a=0")
        rows = sorted(r.a for r in db.execute("SELECT a FROM t"))
        assert rows == [0, 0, 0]
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_update_set_unknown_column_raises():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT)")
        with pytest.raises(ExecutionError):
            db.execute("UPDATE t SET z=1")
    finally:
        os.unlink(path)


@pytest.mark.integration
def test_executor_update_set_type_mismatch_raises():
    db, path = _db()
    try:
        db.execute("CREATE TABLE t (a INT)")
        with pytest.raises(TypeError):
            db.execute("UPDATE t SET a='x'")
    finally:
        os.unlink(path)