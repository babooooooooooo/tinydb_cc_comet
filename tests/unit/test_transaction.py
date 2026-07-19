"""Unit tests for the Transaction state machine.

All Pager interactions are mocked — these tests focus purely on state
transitions and the order of WAL / main-page operations during commit
and rollback.
"""
from unittest.mock import MagicMock

import pytest

from tinydb.transaction import Transaction, TxnState, InvalidTxnState


def test_txn_starts_in_active_state():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    assert txn.state == TxnState.ACTIVE
    assert txn.pending_writes == {}


def test_txn_write_page_buffers_and_appends_wal():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=42, data=b"hello")
    assert txn.pending_writes == {42: b"hello"}
    pager.wal_append_page.assert_called_once_with(1, 42, b"hello")


def test_txn_write_page_in_non_active_state_raises():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn._state = TxnState.COMMITTED
    with pytest.raises(InvalidTxnState):
        txn.write_page(page_id=1, data=b"x")


def test_txn_commit_writes_pages_then_appends_commit_then_fs_syncs_then_truncates():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=10, data=b"page10")
    txn.write_page(page_id=20, data=b"page20")
    txn.commit()
    assert pager.write_main_page.call_count == 2
    pager.wal_append_commit.assert_called_once_with(1)
    pager.fsync_main.assert_called_once()
    pager.wal_truncate_before.assert_called_once_with(1)
    assert txn.state == TxnState.COMMITTED


def test_txn_commit_after_commit_raises():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.commit()
    with pytest.raises(InvalidTxnState):
        txn.commit()


def test_txn_rollback_appends_rollback_then_truncates_and_never_writes_main():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=10, data=b"page10")
    txn.rollback()
    pager.write_main_page.assert_not_called()
    pager.wal_append_rollback.assert_called_once_with(1)
    pager.wal_truncate_before.assert_called_once_with(1)
    assert txn.state == TxnState.ROLLED_BACK


def test_txn_rollback_after_commit_raises():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.commit()
    with pytest.raises(InvalidTxnState):
        txn.rollback()


def test_txn_multiple_writes_to_same_page_overwrite_pending():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=10, data=b"v1")
    txn.write_page(page_id=10, data=b"v2")
    assert txn.pending_writes[10] == b"v2"
