"""Integration tests for autocommit (Task 6).

Statements outside an explicit BEGIN must each run in their own
implicit single-statement transaction. The implicit txn auto-commits
on success and auto-rolls-back on exception so the main file never
holds a half-applied mutation.

These tests drive the public Database API end-to-end so they live under
tests/integration/.

Test matrix (3 cases):
  1. test_single_insert_auto_commits
  2. test_failed_insert_auto_rolls_back
  3. test_select_outside_txn_works
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


# --- 1. Two consecutive autocommit INSERTs both land -----------------------


@pytest.mark.integration
def test_single_insert_auto_commits(fresh_db):
    """Two INSERTs without BEGIN/COMMIT both persist (each is its own txn)."""
    fresh_db.execute("CREATE TABLE t(id INT, name TEXT)")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (2, 'b')")

    rows = fresh_db.execute("SELECT * FROM t")
    assert sorted((r.id, r.name) for r in rows) == [(1, "a"), (2, "b")]


# --- 2. A failed autocommit INSERT auto-rolls-back -------------------------


@pytest.mark.integration
def test_failed_insert_auto_rolls_back(fresh_db):
    """A failing INSERT inside autocommit must not leave the txn half-applied.

    Sequence:
      - INSERT (1, 'a') succeeds, autocommit -> COMMITTED.
      - INSERT (1, 'b') violates PK UNIQUE; autocommit must auto-rollback
        so the failed txn's pending writes are discarded.
      - SELECT must show only the first row.
    """
    from tinydb import errors

    fresh_db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
    with pytest.raises(errors.TinydbError):
        # Same PK -> ConstraintViolation (duplicate_pk).
        fresh_db.execute("INSERT INTO t(id, name) VALUES (1, 'b')")

    rows = fresh_db.execute("SELECT * FROM t")
    # Only the first row must remain — the failed txn's writes were rolled back.
    assert [(r.id, r.name) for r in rows] == [(1, "a")]


# --- 3. SELECT outside any txn works ---------------------------------------


@pytest.mark.integration
def test_select_outside_txn_works(fresh_db):
    """SELECT runs outside any txn — the read path must not be txn-gated."""
    fresh_db.execute("CREATE TABLE t(id INT, name TEXT)")
    fresh_db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")

    rows = fresh_db.execute("SELECT * FROM t")
    assert [(r.id, r.name) for r in rows] == [(1, "a")]