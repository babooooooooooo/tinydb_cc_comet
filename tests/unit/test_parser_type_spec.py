"""Unit tests for tinydb.parser type spec (Task 12).

Task 12 covers VARCHAR(N) / CHAR(N) / DECIMAL(p, s) parameter parsing plus
parametric validation:
- parametric type without params raises
- parametric type with wrong param count raises
- DECIMAL(p, s) with invalid p (e.g. 20 > 18) raises
- non-parametric type without params still works (backward compat)

Per plan section "Task 12: Parser — type_spec with VARCHAR(N) / DECIMAL(p,s)".
"""
import pytest
from tinydb.parser import parse, ColumnDefinition
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError


def _tokenize(s: str):
    return tokenize(s)


def _tokenize_parse(s: str):
    return parse(_tokenize(s))


@pytest.mark.unit
def test_parse_varchar_with_max_len():
    stmts = _tokenize_parse("CREATE TABLE t (name VARCHAR(64))")
    col = stmts.statements[0].columns[0]
    assert col.name == "name"
    assert col.type == "VARCHAR"
    assert col.type_params == (64,)


@pytest.mark.unit
def test_parse_char_with_length():
    stmts = _tokenize_parse("CREATE TABLE t (code CHAR(5))")
    col = stmts.statements[0].columns[0]
    assert col.type == "CHAR"
    assert col.type_params == (5,)


@pytest.mark.unit
def test_parse_decimal_with_precision_scale():
    stmts = _tokenize_parse("CREATE TABLE t (amount DECIMAL(10, 2))")
    col = stmts.statements[0].columns[0]
    assert col.type == "DECIMAL"
    assert col.type_params == (10, 2)


@pytest.mark.unit
def test_parse_int_without_params():
    stmts = _tokenize_parse("CREATE TABLE t (id INT)")
    col = stmts.statements[0].columns[0]
    assert col.type == "INT"
    assert col.type_params == ()


@pytest.mark.unit
def test_parse_text_without_params():
    # Backward compat: TEXT (non-parametric) still works.
    stmts = _tokenize_parse("CREATE TABLE t (name TEXT)")
    col = stmts.statements[0].columns[0]
    assert col.type == "TEXT"
    assert col.type_params == ()


@pytest.mark.unit
def test_parse_varchar_missing_param_raises():
    with pytest.raises(ParseError, match="VARCHAR requires"):
        _tokenize_parse("CREATE TABLE t (name VARCHAR)")


@pytest.mark.unit
def test_parse_char_missing_param_raises():
    with pytest.raises(ParseError, match="CHAR requires"):
        _tokenize_parse("CREATE TABLE t (code CHAR)")


@pytest.mark.unit
def test_parse_decimal_missing_scale_raises():
    with pytest.raises(ParseError, match="DECIMAL requires"):
        _tokenize_parse("CREATE TABLE t (amount DECIMAL(10))")


@pytest.mark.unit
def test_parse_decimal_invalid_p_raises():
    # precision 20 exceeds max of 18.
    with pytest.raises(ParseError, match="DECIMAL"):
        _tokenize_parse("CREATE TABLE t (amount DECIMAL(20, 2))")


@pytest.mark.unit
def test_parse_varchar_zero_max_len_raises():
    # VARCHAR(0) violates max_len >= 1.
    with pytest.raises(ParseError, match="VARCHAR"):
        _tokenize_parse("CREATE TABLE t (name VARCHAR(0))")


@pytest.mark.unit
def test_parse_decimal_scale_equals_precision_raises():
    # scale must be < precision.
    with pytest.raises(ParseError, match="DECIMAL"):
        _tokenize_parse("CREATE TABLE t (amount DECIMAL(5, 5))")


@pytest.mark.unit
def test_parse_non_parametric_with_params_raises():
    # INT is non-parametric — must reject INT(10).
    with pytest.raises(ParseError, match="INT does not accept"):
        _tokenize_parse("CREATE TABLE t (id INT(10))")


@pytest.mark.unit
def test_parse_text_with_params_raises():
    # TEXT is non-parametric — must reject TEXT(10).
    with pytest.raises(ParseError, match="TEXT does not accept"):
        _tokenize_parse("CREATE TABLE t (name TEXT(10))")


@pytest.mark.unit
def test_parse_truly_unsupported_type_raises():
    # BLOB is not registered at all — must reject.
    with pytest.raises(ParseError, match="not supported"):
        _tokenize_parse("CREATE TABLE t (data BLOB)")


@pytest.mark.unit
def test_parse_decimal_missing_both_params_raises():
    # DECIMAL() — empty params; parser catches this at the int-parsing stage
    # inside the parens rather than at the arity check.
    with pytest.raises(ParseError, match="expected integer in type params"):
        _tokenize_parse("CREATE TABLE t (amount DECIMAL())")


@pytest.mark.unit
def test_parse_varchar_non_int_param_raises():
    # VARCHAR(3.5) — must be an integer.
    with pytest.raises(ParseError, match="expected integer"):
        _tokenize_parse("CREATE TABLE t (name VARCHAR(3.5))")


@pytest.mark.unit
def test_parse_column_definition_dataclass_exposes_type_params():
    # Backward compat: ColumnDefinition still has the old fields; type_params
    # is a new optional defaulting to ().
    cd = ColumnDefinition(name="x", type="INT")
    assert cd.type_params == ()
    cd2 = ColumnDefinition(name="y", type="VARCHAR", type_params=(10,))
    assert cd2.type_params == (10,)