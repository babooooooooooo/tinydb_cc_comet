import pytest
from tinydb.parser import _Parser
from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError


def _parse(sql):
    """Parse a single DECIMAL literal and return its Python float value."""
    return _Parser(tokenize(sql))._parse_decimal_literal()


def test_parse_decimal_literal_simple():
    val = _parse("DECIMAL '1.23'")
    assert val == 1.23


def test_parse_decimal_literal_negative():
    val = _parse("DECIMAL '-123.45'")
    assert val == -123.45


def test_parse_decimal_literal_integer_form():
    val = _parse("DECIMAL '100'")
    assert val == 100.0


def test_parse_decimal_literal_zero():
    val = _parse("DECIMAL '0.00'")
    assert val == 0.0


def test_parse_decimal_literal_rejects_no_quote():
    with pytest.raises(ParseError):
        _parse("DECIMAL 1.23")


def test_parse_decimal_literal_rejects_invalid_text():
    with pytest.raises(ParseError, match="DECIMAL literal"):
        _parse("DECIMAL 'not-a-number'")


def test_decimal_literal_used_in_insert():
    """Full INSERT path uses DECIMAL literal."""
    sql = "INSERT INTO t (amount) VALUES (DECIMAL '99.99')"
    stmt_list = parse(tokenize(sql))
    stmt = stmt_list.statements[0]
    assert stmt.values[0][0] == 99.99