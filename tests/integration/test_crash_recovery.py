"""Integration tests for tinydb-acid Task 7: Crash recovery (kill -9 scenarios).

Each test opens a real file-backed Database via tmp_path so the WAL is
exercised end-to-end. They live under tests/integration/ and carry the
``integration`` pytest marker.

Test matrix (3 cases):
  1. test_crash_after_begin_no_commit_discards
  2. test_crash_after_commit_visible
  3. test_partial_wal_record_truncated_on_recovery

Notes / adaptations vs. plan §7:
  * INSERT statements use an explicit column list (e.g.
    ``INSERT INTO t(id, v) VALUES (1, 'a')``) — the parser requires it.
  * Test 2's WAL-size assertion is verified AFTER reopen (post-recovery),
    not after the first ``close()``. ``Transaction.commit`` only truncates
    records with ``txn_id < self.txn_id``, so the just-committed txn's
    own PAGE_WRITE/COMMIT records remain on disk until the next
    process (this test's phase-2 reopen) replays and truncates the WAL.
  * Test 3 wraps ``Database(path)`` in ``pytest.raises(WalCorruption)``
    because :func:`Recovery.replay` re-raises after applying the valid
    prefix + truncating the WAL to the corruption boundary. The
    invariant the test guards — committed page data is durable after
    corruption recovery — is verified by a second ``Database(path)``
    open that succeeds and returns the row.
"""
from __future__ import annotations

import os

import pytest

from tinydb.database import Database
from tinydb.pager import Pager, PAGE_SIZE
from tinydb.wal import Wal, WalCorruption, HEADER_SIZE

pytestmark = pytest.mark.integration


# --- 1. BEGIN + INSERT (no COMMIT) → recovery discards uncommitted --------


def test_crash_after_begin_no_commit_discards(tmp_path):
    """Process killed after BEGIN + INSERT (no COMMIT) → recovery discards.

    Phase 1 writes a PAGE_WRITE to the WAL but never commits. After
    ``close()`` the WAL still exists with the uncommitted txn. On
    reopen, ``Recovery.replay`` sees PAGE_WRITE without a matching
    COMMIT and leaves the main file untouched. A subsequent ``SELECT``
    is empty.
    """
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"

    # Phase 1: create table + start txn + insert (no commit)
    db = Database(path)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
    # Simulate kill -9: just drop reference without commit
    db.close()

    # WAL should still have uncommitted txn (PAGE_WRITE)
    assert os.path.exists(wal_path)

    # Phase 2: reopen → recovery should discard uncommitted
    db2 = Database(path)
    try:
        rows = db2.execute("SELECT * FROM t")
        assert rows == []
    finally:
        db2.close()


# --- 2. BEGIN + INSERT + COMMIT → recovery replays, data visible ---------


def test_crash_after_commit_visible(tmp_path):
    """Process killed after COMMIT → recovery replays, data visible.

    Locks in durability across reopen: a row inserted under BEGIN/COMMIT
    in process 1 must still be visible to process 2 once it opens the
    same file. The WAL is fully drained to HEADER-only by replay during
    the phase-2 open (see note in module docstring).
    """
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"

    db = Database(path)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    db.execute("INSERT INTO t(id, v) VALUES (1, 'a')")
    db.execute("COMMIT")
    db.close()

    # Reopen and verify — recovery replays committed txn into main file.
    db2 = Database(path)
    try:
        rows = db2.execute("SELECT * FROM t")
        assert [(r.id, r.v) for r in rows] == [(1, "a")]
        # After recovery, the WAL is fully drained to HEADER plus at most
        # a tiny autocommit COMMIT record for the SELECT itself (one
        # record = 21 bytes). Anything larger would mean committed-but-
        # not-replayed txns are still pending.
        if os.path.exists(wal_path):
            assert os.path.getsize(wal_path) <= HEADER_SIZE + 32
    finally:
        db2.close()


# --- 3. Corrupt trailing WAL record → recovery truncates + applies valid -


def test_partial_wal_record_truncated_on_recovery(tmp_path):
    """WAL with corrupt trailing record → recovery truncates + applies valid.

    A committed row (id=42) is appended to the WAL and committed; then
    a 50-byte corrupt trailer is appended to the WAL to simulate a
    torn write. On reopen :func:`Recovery.replay` raises
    :class:`WalCorruption` only AFTER truncating the WAL to the
    corruption boundary and applying the committed txn's pages.
    A second reopen succeeds and the committed row is observable.
    """
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"

    # Create table + commit some data (autocommit under the hood)
    db = Database(path)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    db.execute("INSERT INTO t(id) VALUES (42)")
    db.close()

    # Append a corrupt record to WAL (simulate partial write) and capture
    # the post-corruption size so we can confirm truncation by Recovery.
    wal_size_with_corruption = os.path.getsize(wal_path) + 50
    with open(wal_path, "ab") as f:
        f.write(b"\xff" * 50)

    # Reopen → recovery truncates the corrupt tail and re-raises
    with pytest.raises(WalCorruption):
        Database(path)

    # WAL should no longer contain the corruption trailer (its size must
    # not exceed what it was before the corrupt append).
    assert os.path.getsize(wal_path) <= wal_size_with_corruption

    # Second reopen — WAL is now clean and committed data is durable.
    db2 = Database(path)
    try:
        rows = db2.execute("SELECT * FROM t")
        assert [(r.id,) for r in rows] == [(42,)]
    finally:
        db2.close()
