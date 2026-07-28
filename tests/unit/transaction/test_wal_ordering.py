"""Unit tests for WAL write-ahead ordering in Transaction.

Per the write-ahead protocol: ``wal_append_commit`` must be visible (and
fsynced) BEFORE any main-file page write, and ``wal_truncate_before`` must
be called with ``id + 1`` so the commit record survives an immediate crash.

These tests use a real on-disk :class:`Pager` (in ``tmp_path``) and spy on
the relevant bound methods to observe the exact call sequence. The
spies delegate to the real implementation so end-to-end semantics
(page contents, WAL records) are not stubbed.
"""
import os

import pytest

from tinydb.pager import Pager, PAGE_SIZE
from tinydb.transaction import Transaction, TxnState


def _zero_page() -> bytes:
    return b"\x00" * PAGE_SIZE


def test_commit_writes_wal_commit_before_main(tmp_path):
    """Verify ordering via call spy on pager."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    txn = Transaction(1, pager)

    calls: list[tuple[str, int, int | None]] = []
    real_append_commit = pager.wal_append_commit
    real_write_main = pager.write_main_page

    def spy_append_commit(tid):
        calls.append(("wal_commit", tid, None))
        return real_append_commit(tid)

    def spy_write_main(pid, data):
        calls.append(("main_write", 0, pid))
        return real_write_main(pid, data)

    pager.wal_append_commit = spy_append_commit
    pager.write_main_page = spy_write_main

    txn.write_page(2, _zero_page())
    txn.commit()

    # 1. The very first commit-phase call must be wal_append_commit.
    assert calls[0] == ("wal_commit", 1, None), (
        f"first commit-phase call must be wal_append_commit, got {calls[0]}"
    )
    # 2. The main-file page write must appear at least once.
    assert any(c[0] == "main_write" and c[2] == 2 for c in calls), (
        f"no main_write for page 2 found in calls: {calls}"
    )
    # 3. wal_commit must strictly precede every main_write.
    wal_idx = next(i for i, c in enumerate(calls) if c[0] == "wal_commit")
    main_indices = [i for i, c in enumerate(calls) if c[0] == "main_write"]
    assert all(wal_idx < mi for mi in main_indices), (
        f"wal_commit at index {wal_idx} must precede all main_write indices "
        f"{main_indices}; full sequence: {calls}"
    )
    assert txn.state == TxnState.COMMITTED
    pager.close()


def test_commit_failure_does_not_leave_active(tmp_path):
    """Mid-commit exception → state transitions to ROLLED_BACK."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    txn = Transaction(2, pager)

    def boom(pid, data):
        raise IOError("simulated disk failure")

    pager.write_main_page = boom

    txn.write_page(3, _zero_page())
    with pytest.raises(IOError):
        txn.commit()
    # Mid-commit failure must NOT leave the transaction ACTIVE.
    assert txn.state == TxnState.ROLLED_BACK, (
        f"expected ROLLED_BACK after mid-commit IOError, got {txn.state}"
    )
    pager.close()


def test_truncate_uses_id_plus_one(tmp_path):
    """``wal_truncate_before(self.id + 1)`` keeps the current commit record.

    Rationale: truncation removes records with ``txn_id < id`` so the
    commit record for this transaction must remain visible in the WAL
    (a future crash will need to replay it as a no-op idempotently).
    """
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))

    seen_args: list[int] = []
    real = pager.wal_truncate_before

    def spy(before):
        seen_args.append(before)
        return real(before)

    pager.wal_truncate_before = spy
    txn = Transaction(7, pager)
    txn.write_page(2, _zero_page())
    txn.commit()
    assert seen_args, "wal_truncate_before was not called"
    # id=7 → must pass id+1 = 8 so the commit record survives.
    assert seen_args[0] == 8, (
        f"wal_truncate_before must be called with id+1=8, got {seen_args[0]}"
    )
    pager.close()


def test_write_page_wal_first_on_failure(tmp_path):
    """``wal_append_page`` failure → ``pending_writes`` is not mutated.

    The write-ahead contract: the WAL record must be durable BEFORE
    the in-memory pending_writes entry exists, so a crash between the
    two cannot leave a phantom main-page write planned.
    """
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))

    def boom(tid, pid, data):
        raise IOError("wal full")

    pager.wal_append_page = boom
    txn = Transaction(3, pager)
    with pytest.raises(IOError):
        txn.write_page(5, _zero_page())
    # WAL was appended first; the IOError means the entry was never
    # recorded in pending_writes.
    assert 5 not in txn.pending_writes, (
        f"pending_writes must not be mutated when wal_append_page fails; "
        f"got pending_writes={txn.pending_writes!r}"
    )
    # State remains ACTIVE (the IOError is from a single write, not a
    # commit-phase failure).
    assert txn.state == TxnState.ACTIVE
    pager.close()
