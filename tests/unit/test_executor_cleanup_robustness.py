"""Unit tests for Executor cleanup robustness (Task 6 follow-up).

Important 3 from Task 6 review: the autocommit wrapper's teardown
sequence (clear ``_current_txn``, ``_page_buffer``, restore
``_txn_snapshot``) must run even when ``Transaction.rollback`` itself
raises — otherwise the Executor is left in a half-state where the
next statement sees a stale txn reference and a dirty page buffer.

These tests monkey-patch ``Transaction.rollback`` to raise and verify:
  1. The Executor's cleanup state is fully reset.
  2. The rollback error is surfaced (not silently swallowed).
  3. The next statement runs cleanly (no stale txn).
"""
from __future__ import annotations

import pytest

from tinydb import Database, errors
from tinydb.executor import Executor
from tinydb.transaction import Transaction


@pytest.fixture
def fresh_db(tmp_path):
    """Yield a freshly-opened Database at a tmp_path file; close on teardown."""
    path = str(tmp_path / "test.db")
    db = Database(path)
    yield db
    db.close()


# --- Cleanup must run even if Transaction.rollback() raises ------------------


def test_cleanup_runs_when_rollback_raises(fresh_db, monkeypatch):
    """When Transaction.rollback() raises, Executor cleanup must still run.

    Without the fix, the ``finally:`` block in ``_exec_in_txn`` only
    cleared ``_current_txn``; if rollback raised between that and the
    remaining cleanup lines, ``_page_buffer`` and ``_txn_snapshot``
    would leak and the next statement would see a half-state.
    """
    db = fresh_db
    db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")

    # Force Transaction.rollback to raise.
    def boom(self):
        raise RuntimeError("simulated rollback failure")

    monkeypatch.setattr(Transaction, "rollback", boom)

    # Trigger a failing statement inside autocommit so the rollback path runs.
    with pytest.raises(RuntimeError, match="simulated rollback failure"):
        db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
        db.execute("INSERT INTO t(id, v) VALUES (1, 'b')")  # duplicate PK

    # Executor state must be reset despite rollback failure.
    executor = db.executor
    assert executor._current_txn is None, "_current_txn must be cleared"
    assert executor._page_buffer == {}, "_page_buffer must be cleared"
    assert executor._txn_snapshot is None, "_txn_snapshot must be cleared"


def test_next_statement_runs_cleanly_after_rollback_failure(
    fresh_db, monkeypatch
):
    """After a rollback failure, the next statement must run without stale state.

    Verifies the user-visible contract: a buggy rollback does not
    brick the Executor. The next statement runs (and may succeed or
    fail normally) — but it must not see a dangling txn.
    """
    db = fresh_db
    db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")  # pre-existing row

    fail_once = {"count": 0}

    def boom_once(self):
        fail_once["count"] += 1
        if fail_once["count"] == 1:
            raise RuntimeError("simulated rollback failure")

    monkeypatch.setattr(Transaction, "rollback", boom_once)

    # First call inside with: rollback fails (count=1 -> raises).
    with pytest.raises(RuntimeError, match="simulated rollback failure"):
        db.execute("INSERT INTO t(id, v) VALUES (2, 'b')")  # would succeed
        db.execute("INSERT INTO t(id, v) VALUES (1, 'c')")  # duplicate_pk -> rollback

    # Second call: rollback should now succeed (boom_once only fails once).
    # Use a fresh PK so the test isn't confused with prior duplicates.
    # Must NOT raise "nested BEGIN" or "no active" — Executor was cleaned up.
    db.execute("INSERT INTO t(id, v) VALUES (3, 'd')")
    rows = db.execute("SELECT * FROM t WHERE id = 3")
    assert [(r.id, r.v) for r in rows] == [(3, "d")]


def test_rollback_failure_surfaces_error_not_silent(fresh_db, monkeypatch):
    """A rollback failure must propagate (not be silently swallowed).

    Decision: rollback failure is a data integrity concern; the
    RuntimeError from rollback must be visible to the caller. Without
    the fix, an outer try/finally that caught the rollback error but
    not the original statement error could silently succeed.
    """
    db = fresh_db
    db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")  # pre-existing row

    def boom(self):
        raise RuntimeError("rollback_failed_marker")

    monkeypatch.setattr(Transaction, "rollback", boom)

    # Insert a duplicate PK — fails. Then rollback itself fails. The
    # rollback error must propagate, NOT the ConstraintViolation.
    with pytest.raises(RuntimeError, match="rollback_failed_marker"):
        db.execute("INSERT INTO t(id, v) VALUES (1, 'b')")
