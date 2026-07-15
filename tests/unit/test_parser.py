"""Unit tests for tinydb.parser (Tasks 15 + 16).

Task 15 covers CREATE TABLE / DROP TABLE AST shape, duplicate column detection,
unsupported type rejection, and missing-identifier errors per
REQ-PARSE-002 SCN-01/02/03 + REQ-PARSE-003 SCN-01/02.

Task 16 covers INSERT / SELECT / DELETE / StatementList + parser purity per
REQ-PARSE-004/005/006/007/008.
"""
import pytest
from tinydb.parser import parse, CreateTable, Insert
from tinydb.tokenizer import tokenize, Token
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


# --- Task 16: INSERT --------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-004-SCN-01")
def test_parse_insert_single_row():
    stmt = parse(tokenize("INSERT INTO users(id, name) VALUES (1, 'alice')"))
    ins = stmt.statements[0]
    assert ins.table == "users"
    assert ins.columns == ["id", "name"]
    assert ins.values == [[1, "alice"]]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-004-SCN-02")
def test_parse_insert_multi_row():
    stmt = parse(tokenize("INSERT INTO users(id, name) VALUES (1, 'a'), (2, 'b')"))
    assert stmt.statements[0].values == [[1, "a"], [2, "b"]]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-004-SCN-03")
def test_parse_insert_count_mismatch_raises():
    with pytest.raises(ParseError, match="value count mismatch"):
        parse(tokenize("INSERT INTO users(id, name) VALUES (1)"))


# --- Task 16: SELECT --------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-01")
def test_parse_select_star():
    stmt = parse(tokenize("SELECT * FROM users"))
    s = stmt.statements[0]
    assert s.columns == ["*"]
    assert s.table == "users"
    assert s.where is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-03")
def test_parse_select_with_where():
    stmt = parse(tokenize("SELECT * FROM users WHERE id = 1"))
    s = stmt.statements[0]
    assert s.where == ("id", "=", 1)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-04")
def test_parse_select_rejects_unsupported_operator():
    # Build tokens directly: tokenizer.py's PUNCT list is `(),;=*` (Task 13)
    # and does NOT include `>`. The spec scenario `WHERE id > 1` therefore
    # cannot reach the parser without bypassing tokenization. Pre-built
    # tokens let us exercise the same parser branch the spec mandates.
    tokens = [
        Token("KEYWORD", "SELECT", 1, 1),
        Token("PUNCT", "*", 1, 8),
        Token("KEYWORD", "FROM", 1, 10),
        Token("IDENT", "users", 1, 15),
        Token("KEYWORD", "WHERE", 1, 21),
        Token("IDENT", "id", 1, 27),
        Token("PUNCT", ">", 1, 30),
        Token("INT", 1, 1, 32),
        Token("EOF", None, 1, 33),
    ]
    with pytest.raises(ParseError, match=r"operator > not supported"):
        parse(tokens)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-05")
def test_parse_select_missing_from_raises():
    with pytest.raises(ParseError, match="expected FROM"):
        parse(tokenize("SELECT id"))


# --- Task 16: DELETE --------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-006-SCN-01")
def test_parse_delete_all():
    stmt = parse(tokenize("DELETE FROM users"))
    d = stmt.statements[0]
    assert d.table == "users"
    assert d.where is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-006-SCN-02")
def test_parse_delete_with_where():
    stmt = parse(tokenize("DELETE FROM users WHERE id = 1"))
    assert stmt.statements[0].where == ("id", "=", 1)


# --- Task 16: StatementList + purity ---------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-007-SCN-02")
def test_parse_multiple_statements():
    # Two statements separated by `;`. INSERT uses an explicit column list
    # (per REQ-PARSE-004 grammar `INSERT INTO table(col, ...) VALUES ...`).
    # The spec's SCN-02 example shows `INSERT INTO t VALUES (1)` without
    # columns, but REQ-PARSE-004 mandates columns — we use the explicit form
    # so the plan code's required-column branch is exercised end-to-end.
    stmt = parse(tokenize("CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1)"))
    assert len(stmt.statements) == 2
    assert isinstance(stmt.statements[0], CreateTable)
    assert isinstance(stmt.statements[1], Insert)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-008-SCN-01")
def test_parser_is_pure_deterministic():
    sql = "CREATE TABLE t(id INT, name TEXT)"
    a = parse(tokenize(sql))
    b = parse(tokenize(sql))
    assert a.statements[0].columns == b.statements[0].columns