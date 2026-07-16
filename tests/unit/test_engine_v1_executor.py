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