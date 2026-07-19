"""Integration tests for B+tree writes inside transactions (Task 6 follow-up).

Critical 1 from Task 6 review: ``BTree.insert`` / ``BTree.delete`` /
``BTree._insert_into_parent`` call ``self.pager.write_page(...)``
directly. When ``BTree.pager`` is the ``_IndexPager`` wrapper installed
by ``Database._install_index_pagers``, those writes bypass the txn
layer (no WAL append, no ``_page_buffer`` shadow) so an autocommit
ROLLBACK discards the data page but leaves a stale entry in the
B+tree leaf. Subsequent SELECT WHERE pk returns ``IndexError`` on the
stale entry.

These tests lock in the fix: every B+tree read/write/free must flow
through the Executor's txn helpers so a ROLLBACK or autocommit rollback
leaves the B+tree in a coherent state.

Test matrix (3 cases):
  1. test_begin_insert_pk_commit_selects_via_index
  2. test_begin_insert_pk_rollback_clears_index
  3. test_autocommit_pk_duplicate_rolls_back_index
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


# --- 1. BEGIN + PK INSERT + COMMIT: SELECT sees the row via B+tree fast path


@pytest.mark.integration
def test_begin_insert_pk_commit_selects_via_index(fresh_db):
    """BEGIN + INSERT (PK table) + COMMIT + SELECT WHERE pk returns the row.

    Locks in the happy path: B+tree writes inside the txn go through the
    WAL + page buffer; COMMIT flushes the leaf to main; SELECT after
    COMMIT finds the row via the B+tree fast path (which reads from
    main file post-commit).
    """
    fresh_db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")
    fresh_db.execute("BEGIN")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (2, 'b')")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (3, 'c')")
    fresh_db.execute("COMMIT")

    # PK lookup via B+tree fast path.
    rows = fresh_db.execute("SELECT * FROM t WHERE id = 2")
    assert [(r.id, r.v) for r in rows] == [(2, "b")]


# --- 2. BEGIN + PK INSERT + ROLLBACK: pre-existing B+tree root is clean


@pytest.mark.integration
def test_begin_insert_pk_rollback_clears_index(fresh_db):
    """BEGIN + INSERT (PK table) + ROLLBACK on a PRE-EXISTING B+tree root.

    Pre-existing rows autocommit-committed, so the B+tree has a real
    root. BEGIN + INSERT + ROLLBACK then SELECT WHERE pk must return []
    (no stale leaf entry, no IndexError).

    Without the fix, the B+tree leaf write bypasses the txn layer; the
    rolled-back data page no longer has the row but the leaf still has
    the stale (key, slot_ref) entry, and SELECT WHERE pk would either
    raise IndexError or return a row from the wrong slot.
    """
    fresh_db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")
    # Pre-existing rows so the B+tree root is allocated and reused.
    fresh_db.execute("INSERT INTO t(id, v) VALUES (10, 'x'), (20, 'y')")

    fresh_db.execute("BEGIN")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (30, 'z')")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (40, 'w')")
    fresh_db.execute("ROLLBACK")

    # PK lookups for the rolled-back keys must return empty (no IndexError).
    rows = fresh_db.execute("SELECT * FROM t WHERE id = 30")
    assert rows == []

    rows = fresh_db.execute("SELECT * FROM t WHERE id = 40")
    assert rows == []

    # Pre-existing rows are still there.
    rows = fresh_db.execute("SELECT * FROM t WHERE id = 10")
    assert [(r.id, r.v) for r in rows] == [(10, "x")]

    rows = fresh_db.execute("SELECT * FROM t WHERE id = 20")
    assert [(r.id, r.v) for r in rows] == [(20, "y")]

    # Full scan sees only the pre-existing rows.
    rows = fresh_db.execute("SELECT * FROM t")
    assert sorted((r.id, r.v) for r in rows) == [(10, "x"), (20, "y")]


# --- 3. Autocommit PK duplicate rolls back the data page AND the index entry


@pytest.mark.integration
def test_autocommit_pk_duplicate_rolls_back_index(fresh_db):
    """Autocommit PK duplicate must leave the B+tree coherent (no stale entries).

    Sequence (mirrors Critical 1 repro):
      - INSERT (1, 'a') succeeds, autocommit -> COMMITTED.
      - INSERT (1, 'd') violates PK UNIQUE, autocommit rolls back.
      - The data page is rolled back AND any B+tree leaf updates made
        before the constraint check fired must also be rolled back, so
        a subsequent SELECT WHERE id=3 must not raise IndexError.
    """
    fresh_db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (1, 'a'), (2, 'b')")
    fresh_db.execute("BEGIN")
    fresh_db.execute("INSERT INTO t(id, v) VALUES (3, 'c')")
    with pytest.raises(errors.TinydbError):
        # Duplicate PK triggers ConstraintViolation; autocommit rolls
        # back the buffered data page AND the buffered B+tree leaf.
        fresh_db.execute("INSERT INTO t(id, v) VALUES (1, 'd')")

    # Without the fix, this raises IndexError because the B+tree leaf
    # still references slot (2, 2) which the rolled-back data page no
    # longer contains.
    rows = fresh_db.execute("SELECT * FROM t WHERE id = 3")
    assert rows == []

    # And the pre-existing rows are still there.
    rows = fresh_db.execute("SELECT * FROM t WHERE id = 1")
    assert [(r.id, r.v) for r in rows] == [(1, "a")]

    rows = fresh_db.execute("SELECT * FROM t WHERE id = 2")
    assert [(r.id, r.v) for r in rows] == [(2, "b")]
