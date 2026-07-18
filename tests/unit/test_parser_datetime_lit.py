import datetime
import pytest
from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError


def _parse_value(sql):
    """Parse a single literal and return its parsed value."""
    from tinydb.parser import _Parser
    tokens = tokenize(sql)
    parser = _Parser(tokens)
    return parser._parse_datetime_literal()


def test_parse_date_literal():
    val = _parse_value("DATE '2026-07-16'")
    assert val == datetime.date(2026, 7, 16)


def test_parse_time_literal():
    val = _parse_value("TIME '14:30:00'")
    assert val == datetime.time(14, 30, 0)


def test_parse_timestamp_literal():
    val = _parse_value("TIMESTAMP '2026-07-16 14:30:00'")
    assert val == datetime.datetime(2026, 7, 16, 14, 30, 0)


def test_parse_invalid_date_literal_raises():
    with pytest.raises(ParseError, match="DATE literal"):
        _parse_value("DATE '2026/07/16'")


def test_parse_invalid_time_literal_raises():
    with pytest.raises(ParseError, match="TIME literal"):
        _parse_value("TIME '25:00:00'")


def test_parse_invalid_timestamp_literal_raises():
    with pytest.raises(ParseError, match="TIMESTAMP literal"):
        _parse_value("TIMESTAMP 'not-a-date'")


def test_date_literal_used_in_insert():
    """Full INSERT path uses DATE literal."""
    sql = "INSERT INTO events (d) VALUES (DATE '2026-01-01')"
    stmt_list = parse(tokenize(sql))
    stmt = stmt_list.statements[0]
    # stmt.values is a list of rows; each row is a list of column values.
    assert stmt.values[0][0] == datetime.date(2026, 1, 1)