"""Integration tests for tinydb.executor — DDL CREATE/DROP TABLE execution.

These tests touch the filesystem (real I/O via tmp_path) so they live under
tests/integration/ and carry the `integration` pytest marker. They drive the
Executor end-to-end: parse SQL → execute AST → catalog persisted to page 1.
"""
import pytest

from tinydb.pager import Pager
from tinydb.catalog import Catalog
from tinydb.executor import Executor
from tinydb.errors import ExecutionError
from tinydb.parser import parse
from tinydb.tokenizer import tokenize


def _exec(pager, sql):
    """Drive a SQL string through parse → Executor.

    The Executor persists the catalog to page 1 and flushes itself, so the
    helper does NOT re-persist — that way the test genuinely exercises the
    Executor's persistence path (SCN-04/05) rather than masking it.
    """
    cat = Catalog.from_bytes(pager.read_page(1))
    ex = Executor(pager, cat)
    stmts = parse(tokenize(sql)).statements
    for s in stmts:
        ex.execute(s)


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-04")
def test_create_table_persists_to_catalog(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE users(id INT, name TEXT)")
        cat = Catalog.from_bytes(p.read_page(1))
        assert "users" in cat.tables
        assert cat.get_table("users").schema == [
            ("id", "INT"),
            ("name", "TEXT"),
        ]
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-05")
def test_drop_table_removes_from_catalog(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE users(id INT)")
        _exec(p, "DROP TABLE users")
        cat = Catalog.from_bytes(p.read_page(1))
        assert "users" not in cat.tables
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-04")
def test_create_duplicate_table_raises(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE users(id INT)")
        with pytest.raises(ExecutionError, match="already exists"):
            _exec(p, "CREATE TABLE users(id INT)")
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-05")
def test_drop_missing_table_raises(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        with pytest.raises(ExecutionError, match="does not exist"):
            _exec(p, "DROP TABLE ghost")
    finally:
        p.close()