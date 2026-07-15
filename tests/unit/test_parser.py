"""Unit tests for tinydb.parser (Task 15).

Covers CREATE TABLE / DROP TABLE AST shape, duplicate column detection,
unsupported type rejection, and missing-identifier errors per
REQ-PARSE-002 SCN-01/02/03 + REQ-PARSE-003 SCN-01/02.
"""
import pytest
from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-002-SCN-01")
def test_parse_create_table_simple():
    stmt = parse(tokenize("CREATE TABLE users (id INT, name TEXT)"))
    assert stmt.statements[0].name == "users"
    assert stmt.statements[0].columns == [("id", "INT"), ("name", "TEXT")]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-002-SCN-02")
def test_parse_create_table_rejects_duplicate_column():
    with pytest.raises(ParseError, match="duplicate column"):
        parse(tokenize("CREATE TABLE t(id INT, id TEXT)"))


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-002-SCN-03")
def test_parse_create_table_rejects_unsupported_type():
    with pytest.raises(ParseError, match="VARCHAR not supported"):
        parse(tokenize("CREATE TABLE t(id VARCHAR(10))"))


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-003-SCN-01")
def test_parse_drop_table():
    stmt = parse(tokenize("DROP TABLE users"))
    assert stmt.statements[0].name == "users"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-003-SCN-02")
def test_parse_drop_table_missing_name_raises():
    with pytest.raises(ParseError, match="expected table name"):
        parse(tokenize("DROP TABLE"))