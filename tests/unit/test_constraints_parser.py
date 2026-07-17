"""Unit tests for CREATE TABLE column constraints parsing (Task 5).

Introduces the ``ColumnDefinition`` AST node and constraint clause chain
(NOT NULL / UNIQUE / PRIMARY KEY) in the parser. The parser remains pure:
no catalog lookup, no constraint enforcement — that lives in the executor
(Task 7).

Coverage:
* Default nullability (no constraint) for a multi-column CREATE TABLE.
* Single NOT NULL clause.
* Single PRIMARY KEY clause (nullable default preserved).
* Combined NOT NULL + UNIQUE + PRIMARY KEY on one column.
"""

import pytest

from tinydb import Database
from tinydb.parser import parse, CreateTable, ColumnDefinition
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError


@pytest.mark.unit
def test_create_table_column_definition_default_nullable_true():
    stmt = parse(tokenize("CREATE TABLE t(id INT, name TEXT)"))
    ct = stmt.statements[0]
    assert isinstance(ct, CreateTable)
    assert all(isinstance(c, ColumnDefinition) for c in ct.columns)
    assert ct.columns[0] == ColumnDefinition(
        name="id", type="INT", nullable=True, unique=False, primary_key=False
    )
    assert ct.columns[1] == ColumnDefinition(
        name="name", type="TEXT", nullable=True, unique=False, primary_key=False
    )


@pytest.mark.unit
def test_create_table_column_definition_not_null():
    stmt = parse(tokenize("CREATE TABLE t(id INT NOT NULL)"))
    cd = stmt.statements[0].columns[0]
    assert cd == ColumnDefinition(
        name="id", type="INT", nullable=False, unique=False, primary_key=False
    )


@pytest.mark.unit
def test_create_table_column_definition_primary_key():
    stmt = parse(tokenize("CREATE TABLE t(id INT PRIMARY KEY)"))
    cd = stmt.statements[0].columns[0]
    assert cd == ColumnDefinition(
        name="id", type="INT", nullable=True, unique=False, primary_key=True
    )


@pytest.mark.unit
def test_create_table_column_definition_all_three():
    stmt = parse(tokenize("CREATE TABLE t(id INT NOT NULL UNIQUE PRIMARY KEY)"))
    cd = stmt.statements[0].columns[0]
    assert cd == ColumnDefinition(
        name="id", type="INT", nullable=False, unique=True, primary_key=True
    )


@pytest.mark.unit
def test_insert_accepts_null_literal_when_column_nullable():
    stmt = parse(tokenize("INSERT INTO t(x) VALUES (NULL)"))
    ins = stmt.statements[0]
    assert hasattr(ins, "values")
    assert ins.values == [[None]]


@pytest.mark.unit
def test_insert_accepts_null_literal_mixed_with_int():
    stmt = parse(tokenize("INSERT INTO t(x, y) VALUES (1, NULL)"))
    assert stmt.statements[0].values == [[1, None]]


@pytest.mark.unit
def test_create_table_rejects_bare_null_after_type():
    with pytest.raises(ParseError, match="bare NULL not allowed"):
        parse(tokenize("CREATE TABLE t(x INT NULL)"))


@pytest.mark.unit
def test_create_table_rejects_not_without_null():
    with pytest.raises(ParseError, match="expected NULL after NOT"):
        parse(tokenize("CREATE TABLE t(x INT NOT)"))


@pytest.mark.unit
def test_create_table_rejects_primary_without_key():
    with pytest.raises(ParseError, match="expected KEY after PRIMARY"):
        parse(tokenize("CREATE TABLE t(x INT PRIMARY)"))


@pytest.mark.unit
def test_create_table_rejects_duplicate_unique_constraint():
    with pytest.raises(ParseError, match="duplicate UNIQUE"):
        parse(tokenize("CREATE TABLE t(x INT UNIQUE NOT NULL UNIQUE)"))


@pytest.mark.unit
def test_create_table_rejects_duplicate_primary_key():
    with pytest.raises(ParseError, match="duplicate PRIMARY KEY"):
        parse(tokenize("CREATE TABLE t(x INT PRIMARY KEY PRIMARY KEY)"))


@pytest.mark.unit
def test_create_table_rejects_bare_key_token():
    with pytest.raises(ParseError, match="unexpected KEY"):
        parse(tokenize("CREATE TABLE t(x INT KEY)"))


@pytest.mark.unit
def test_create_table_constraint_order_independent():
    stmt = parse(tokenize("CREATE TABLE t(x INT PRIMARY KEY NOT NULL UNIQUE)"))
    cd = stmt.statements[0].columns[0]
    assert cd == ColumnDefinition(
        name="x", type="INT", nullable=False, unique=True, primary_key=True
    )


@pytest.mark.unit
def test_create_table_multi_column_pk_merges_into_one_group(tmp_path):
    # Two single-column PK declarations land on different columns;
    # the executor builds a single composite key group (R4 裁决).
    with Database(str(tmp_path / "mcpk.db")) as db:
        db.execute("CREATE TABLE t(a INT PRIMARY KEY, b INT PRIMARY KEY)")
        ti = db.catalog.get_table("t")
    assert ti.columns[0].primary_key is True
    assert ti.columns[1].primary_key is True