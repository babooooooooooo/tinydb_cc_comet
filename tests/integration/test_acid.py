"""Integration tests for tinydb-acid Task 6: Executor transaction routing.

These tests lock in the public Database-level semantics of BEGIN/COMMIT/
ROLLBACK through the full pipeline: tokenize -> parse -> executor -> storage.

Each test opens a real file-backed Database via tmp_path so the WAL is
exercised end-to-end. They live under tests/integration/ and carry the
``integration`` pytest marker (registered in pyproject.toml).

Test matrix (6 cases):
  1. test_begin_insert_commit_persists
  2. test_begin_insert_rollback_discards
  3. test_nested_begin_raises
  4. test_commit_without_begin_raises
  5. test_rollback_without_begin_raises
  6. test_commit_visible_after_reopen
"""
from __future__ import annotations

import pytest

from tinydb import Database, errors

pytestmark = pytest.mark.integration


@pytest.fixture
def fresh_db(tmp_path):
    """Yield a freshly-opened Database at a tmp_path file; close on teardown."""
    path = str(tmp_path / "test.db")
    db = Database(path)
    yield db
    db.close()


# --- 1. Happy path: BEGIN + INSERT + COMMIT ---------------------------------


@pytest.mark.integration
def test_begin_insert_commit_persists(fresh_db):
    """BEGIN ... INSERT ... COMMIT then SELECT outside the txn sees the rows.

    Locks in: BEGIN opens a txn, INSERT inside the txn is buffered, COMMIT
    flushes to main, and a SELECT after COMMIT (still in the same connection,
    outside the txn) returns both rows.
    """
    fresh_db.execute("CREATE TABLE t(id INT, name TEXT)")
    fresh_db.execute("BEGIN")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (2, 'b')")
    fresh_db.execute("COMMIT")

    rows = fresh_db.execute("SELECT * FROM t")
    assert sorted((r.id, r.name) for r in rows) == [(1, "a"), (2, "b")]


# --- 2. ROLLBACK discards pending writes -------------------------------------


@pytest.mark.integration
def test_begin_insert_rollback_discards(fresh_db):
    """BEGIN ... INSERT ... ROLLBACK then SELECT returns [].

    Locks in: ROLLBACK never touches the main file; the INSERTs are
    discarded and a subsequent SELECT (outside the txn) returns [].
    """
    fresh_db.execute("CREATE TABLE t(id INT, name TEXT)")
    fresh_db.execute("BEGIN")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (2, 'b')")
    fresh_db.execute("ROLLBACK")

    rows = fresh_db.execute("SELECT * FROM t")
    assert rows == []


# --- 3. Nested BEGIN is rejected --------------------------------------------


@pytest.mark.integration
def test_nested_begin_raises(fresh_db):
    """BEGIN inside an open BEGIN raises ExecutionError.

    Locks in: nested-BEGIN detection; error message mentions BEGIN so the
    caller can recognize the failure mode.
    """
    fresh_db.execute("CREATE TABLE t(id INT)")
    fresh_db.execute("BEGIN")
    with pytest.raises(errors.ExecutionError, match="(?i)BEGIN"):
        fresh_db.execute("BEGIN")
    # Cleanup so close() doesn't trip on a half-open txn.
    fresh_db.execute("ROLLBACK")


# --- 4. Bare COMMIT is rejected --------------------------------------------


@pytest.mark.integration
def test_commit_without_begin_raises(fresh_db):
    """COMMIT without an open BEGIN raises ExecutionError."""
    fresh_db.execute("CREATE TABLE t(id INT)")
    with pytest.raises(errors.ExecutionError, match="(?i)COMMIT|no active"):
        fresh_db.execute("COMMIT")


# --- 5. Bare ROLLBACK is rejected -------------------------------------------


@pytest.mark.integration
def test_rollback_without_begin_raises(fresh_db):
    """ROLLBACK without an open BEGIN raises ExecutionError."""
    fresh_db.execute("CREATE TABLE t(id INT)")
    with pytest.raises(errors.ExecutionError, match="(?i)ROLLBACK|no active"):
        fresh_db.execute("ROLLBACK")


# --- 6. COMMIT persists across reopen ---------------------------------------


@pytest.mark.integration
def test_commit_visible_after_reopen(tmp_path):
    """A committed transaction survives close + reopen.

    Locks in durability: a row inserted under BEGIN/COMMIT in db1 must
    still be visible to db2 when it opens the same file.
    """
    path = str(tmp_path / "durable.db")
    with Database(path) as db1:
        db1.execute("CREATE TABLE t(id INT, name TEXT)")
        db1.execute("BEGIN")
        db1.execute("INSERT INTO t(id, name) VALUES (42, 'durable')")
        db1.execute("COMMIT")

    with Database(path) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert [(r.id, r.name) for r in rows] == [(42, "durable")]