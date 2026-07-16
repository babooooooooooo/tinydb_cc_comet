import pytest
from tinydb.errors import ExecutionError, ConstraintViolation, TinydbError


@pytest.mark.unit
def test_constraint_violation_inherits_execution_error():
    exc = ConstraintViolation(kind="null", column="x", value=None)
    assert isinstance(exc, ExecutionError)
    assert isinstance(exc, TinydbError)


@pytest.mark.unit
def test_constraint_violation_str_includes_kind_column_value():
    exc = ConstraintViolation(kind="null", column="x", value=None)
    text = str(exc)
    assert "kind='null'" in text
    assert "column='x'" in text
    assert "value=None" in text


@pytest.mark.unit
def test_constraint_violation_str_includes_kind_columns_value_for_unique():
    exc = ConstraintViolation(kind="unique", columns=("email",), value=("a@x",))
    text = str(exc)
    assert "kind='unique'" in text
    assert "columns=['email']" in text
    assert "value=('a@x',)" in text


@pytest.mark.unit
def test_constraint_violation_str_for_duplicate_pk():
    exc = ConstraintViolation(kind="duplicate_pk", columns=("id",), value=(1,))
    text = str(exc)
    assert "kind='duplicate_pk'" in text
    assert "columns=['id']" in text
    assert "value=(1,)" in text


@pytest.mark.unit
def test_constraint_violation_kind_column_attributes():
    exc = ConstraintViolation(kind="null", column="x", value=None)
    assert exc.kind == "null"
    assert exc.column == "x"
    assert exc.value is None


@pytest.mark.unit
def test_constraint_violation_supports_caught_by_except_execution_error():
    with pytest.raises(ExecutionError) as exc_info:
        raise ConstraintViolation(kind="unique", columns=("a",), value=("dup",))
    assert isinstance(exc_info.value, ConstraintViolation)
    assert exc_info.value.kind == "unique"