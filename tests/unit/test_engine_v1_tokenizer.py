"""Tokenizer keyword tests for engine-v1 (Task 1).

Locks in the 11 new keywords (UPDATE/SET/AND/OR/NOT/ORDER/BY/ASC/DESC/LIMIT/OFFSET)
and the keyword-conflict-with-identifier behaviour (R4): lower-case `update`
must tokenize as KEYWORD, which forces CREATE TABLE to reject it as a column
or table name.
"""
import pytest
from tinydb.tokenizer import tokenize, KEYWORDS
from tinydb.errors import ParseError
from tinydb.parser import parse

KW_NEW = ["UPDATE", "SET", "AND", "OR", "NOT", "ORDER",
          "BY", "ASC", "DESC", "LIMIT", "OFFSET"]


@pytest.mark.unit
@pytest.mark.parametrize("kw", KW_NEW)
def test_tokenizer_keyword_upper(kw):
    toks = tokenize(f"SELECT * FROM t {kw}")
    assert toks[0].value == "SELECT"  # sanity
    assert any(t.type == "KEYWORD" and t.value == kw for t in toks)


@pytest.mark.unit
@pytest.mark.parametrize("kw", KW_NEW)
def test_tokenizer_keyword_case_insensitive(kw):
    toks = tokenize(f"select * from t {kw.lower()}")
    assert any(t.type == "KEYWORD" and t.value == kw for t in toks)


@pytest.mark.unit
def test_keyword_table_complete():
    for kw in KW_NEW:
        assert kw in KEYWORDS


@pytest.mark.unit
def test_keyword_update_as_table_name_raises():
    # 'update' is upper-cased and detected as keyword; parser must reject
    # a KEYWORD token in place of the table name.
    with pytest.raises(ParseError):
        parse(tokenize("CREATE TABLE update (id INT)"))


@pytest.mark.unit
def test_keyword_set_as_column_name_raises():
    # 'set' is upper-cased and detected as keyword; parser must reject
    # a KEYWORD token in place of the column name.
    with pytest.raises(ParseError):
        parse(tokenize("CREATE TABLE t (set INT)"))


@pytest.mark.unit
def test_keyword_lowercase_order_as_column_ok():
    # 'order' is upper-cased and detected as KEYWORD, but a CREATE TABLE
    # with a column named 'order' should still be rejected by the parser
    # because the parser refuses KEYWORD tokens for column names.
    # We use a non-keyword column name to confirm lowercase 'order' tokens
    # normally as KEYWORD but identifiers remain separately typed.
    toks = tokenize("CREATE TABLE t (id INT)")
    # Confirm 'order' upper-cases to a KEYWORD token.
    order_toks = tokenize("ORDER")
    assert order_toks[0].type == "KEYWORD" and order_toks[0].value == "ORDER"
    # Confirm a column named 'order' is rejected at parse time.
    with pytest.raises(ParseError):
        parse(tokenize("CREATE TABLE t (order INT)"))