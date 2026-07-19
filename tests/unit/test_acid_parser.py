"""Parser tests for transaction-control statements (Task 4).

Locks in:
- Begin/Commit/Rollback AST nodes
- parse_statement dispatches BEGIN/COMMIT/ROLLBACK before any other dispatch
- Tokenizer recognizes COMMIT/ROLLBACK as KEYWORD tokens
- tokenizer accepts BEGIN as a KEYWORD (BEGIN was previously absent)
"""
import pytest
from tinydb.tokenizer import Tokenizer
from tinydb.parser import Parser, Begin, Commit, Rollback


def _parse_one(sql: str):
    toks = Tokenizer(sql).tokenize()
    p = Parser(toks)
    return p.parse_statement()


@pytest.mark.unit
def test_parse_begin():
    stmt = _parse_one("BEGIN")
    assert isinstance(stmt, Begin)


@pytest.mark.unit
def test_parse_commit():
    stmt = _parse_one("COMMIT")
    assert isinstance(stmt, Commit)


@pytest.mark.unit
def test_parse_rollback():
    stmt = _parse_one("ROLLBACK")
    assert isinstance(stmt, Rollback)


@pytest.mark.unit
def test_parse_begin_with_trailing_semicolon():
    stmt = _parse_one("BEGIN;")
    assert isinstance(stmt, Begin)


@pytest.mark.unit
def test_tokenizer_recognizes_commit_rollback_keywords():
    from tinydb.tokenizer import Tokenizer
    toks = Tokenizer("COMMIT ROLLBACK").tokenize()
    assert toks[0].value == "COMMIT"
    assert toks[0].type == "KEYWORD"
    assert toks[1].value == "ROLLBACK"
    assert toks[1].type == "KEYWORD"
