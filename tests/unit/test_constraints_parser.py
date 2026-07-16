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