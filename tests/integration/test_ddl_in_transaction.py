"""Integration tests for DDL statements inside transactions (Task 6).

These tests lock in that BEGIN ... DDL ... COMMIT/ROLLBACK behaves
correctly for CREATE TABLE and DROP TABLE. They drive the public Database
API (end-to-end: tokenize -> parse -> executor -> storage + WAL) and live
under tests/integration/.

Test matrix (3 cases):
  1. test_create_table_in_txn_rollback_no_side_effect
  2. test_drop_table_in_txn_commit_removes_table
  3. test_create_table_in_txn_commit_persists
"""
from __future__ import annotations

import pytest

from tinydb import Database

pytestmark = pytest.mark.integration


@pytest.fixture
def fresh_db(tmp_path):
    """Yield a freshly-opened Database at a tmp_path file; close on teardown."""
    path = str(tmp_path / "test.db")
    db = Database(path)
    yield db
    db.close()


# --- 1. CREATE TABLE + ROLLBACK discards the table -------------------------


def test_create_table_in_txn_rollback_no_side_effect(fresh_db):
    """CREATE TABLE inside a rolled-back txn must not be visible afterwards.

    Locks in: DDL inside a txn is buffered; ROLLBACK discards the catalog
    change so SELECT on the table raises (table does not exist).
    """
    fresh_db.execute("BEGIN")
    fresh_db.execute(
        "CREATE TABLE t(id INT, name TEXT)"
    )
    fresh_db.execute(
        "INSERT INTO t(id, name) VALUES (1, 'a')"
    )
    fresh_db.execute("ROLLBACK")

    # Table does not exist — SELECT raises ExecutionError.
    from tinydb import errors
    with pytest.raises(errors.ExecutionError, match="does not exist"):
        fresh_db.execute("SELECT * FROM t")


# --- 2. DROP TABLE + COMMIT removes the table ------------------------------


def test_drop_table_in_txn_commit_removes_table(fresh_db):
    """DROP TABLE inside a committed txn removes the table for good.

    Locks in: DDL COMMITs through the catalog; SELECT after COMMIT returns []
    (or the right "does not exist" if the table is gone entirely).
    """
    # Pre-create a table + insert a row outside the txn (autocommit).
    fresh_db.execute("CREATE TABLE t(id INT, name TEXT)")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (2, 'b')")

    fresh_db.execute("BEGIN")
    fresh_db.execute("DROP TABLE t")
    fresh_db.execute("COMMIT")

    # Post-COMMIT, the table must be gone.
    from tinydb import errors
    with pytest.raises(errors.ExecutionError, match="does not exist"):
        fresh_db.execute("SELECT * FROM t")


# --- 3. CREATE TABLE + COMMIT persists the table ----------------------------


def test_create_table_in_txn_commit_persists(fresh_db):
    """CREATE TABLE inside a committed txn is visible to SELECT afterwards.

    Locks in: DDL COMMIT writes the catalog to the main file; SELECT after
    COMMIT returns the row inserted inside the txn.
    """
    fresh_db.execute("BEGIN")
    fresh_db.execute("CREATE TABLE t(id INT, name TEXT)")
    fresh_db.execute(
        "INSERT INTO t(id, name) VALUES (7, 'persisted')"
    )
    fresh_db.execute("COMMIT")

    rows = fresh_db.execute("SELECT * FROM t")
    assert [(r.id, r.name) for r in rows] == [(7, "persisted")]


# --- 4. DROP TABLE + ROLLBACK restores the table ---------------------------


def test_drop_table_in_txn_rollback_restores_table(fresh_db):
    """DROP TABLE inside a rolled-back txn must leave the table intact.

    Locks in: a DROP TABLE that runs inside a BEGIN block but is then
    ROLLED BACK must restore the table and its rows. The ROLLBACK path
    must restore both the catalog entry AND the free-list head
    (DROP TABLE calls ``Pager.free_page`` to return data pages, which
    mutates page 0's ``free_list_head`` field — that mutation must be
    reverted too).
    """
    fresh_db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")

    fresh_db.execute("BEGIN")
    fresh_db.execute("DROP TABLE t")
    fresh_db.execute("ROLLBACK")

    rows = fresh_db.execute("SELECT * FROM t WHERE id = 1")
    assert [(r.id, r.v) for r in rows] == [(1, "a")]