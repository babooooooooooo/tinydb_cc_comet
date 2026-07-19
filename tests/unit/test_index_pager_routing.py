"""Unit tests for _IndexPager txn routing (Task 6 follow-up).

Critical 1 from Task 6 review: ``_IndexPager.write_page`` /
``_IndexPager.read_page`` / ``_IndexPager.free_page`` must route
through the Executor's ``_txn_*`` helpers when a transaction is
open. Without the back-reference, B+tree writes would bypass the
WAL + ``_page_buffer`` and mutate the main file directly.

These tests verify the wrapper's routing in isolation, independent
of any actual B+tree operation.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tinydb.executor import Executor, _IndexPager
from tinydb.pager import Pager


@pytest.fixture
def fresh_pager():
    """Yield an in-memory Pager; caller closes."""
    p = Pager(":memory:")
    yield p
    p.close()


def test_index_pager_no_executor_delegates_directly(fresh_pager):
    """Without an executor back-ref, _IndexPager proxies raw Pager calls."""
    wrapper = _IndexPager(fresh_pager)
    # write_page + read_page should be raw passthrough.
    page = b"\x00" * 4096
    page = b"\x42" * 4096
    wrapper.write_page(5, page)
    assert fresh_pager.read_page(5) == page
    assert fresh_pager.read_page(5) == wrapper.read_page(5)


def test_index_pager_with_executor_routes_through_txn(fresh_pager):
    """With an executor, _IndexPager.write_page routes through _txn_write_page."""
    # Fake executor that exposes _txn_write_page / _txn_read_page / _txn_free_page.
    fake_exec = MagicMock(spec=Executor)
    wrapper = _IndexPager(fresh_pager, executor=fake_exec)

    page = b"\x42" * 4096
    wrapper.write_page(5, page)
    fake_exec._txn_write_page.assert_called_once_with(5, page)

    # Read should also route.
    fake_exec._txn_read_page.return_value = page
    assert wrapper.read_page(5) == page
    fake_exec._txn_read_page.assert_called_once_with(5)

    # Free should route too.
    wrapper.free_page(5)
    fake_exec._txn_free_page.assert_called_once_with(5)


def test_index_pager_deepcopy_preserves_executor_ref(fresh_pager):
    """_IndexPager.__deepcopy__ must share the executor reference (singleton state).

    The Executor is process-singleton state owned by the Database. Sharing
    the reference in the deep copy is required for ``_snapshot_state``
    to preserve the routing through rollback.
    """
    fake_exec = MagicMock(spec=Executor)
    wrapper = _IndexPager(fresh_pager, executor=fake_exec)
    import copy
    copy_ = copy.deepcopy(wrapper)
    assert copy_._executor is wrapper._executor
    # allocated set is independent (mutable per-instance state).
    assert copy_._allocated == wrapper._allocated
    assert copy_._allocated is not wrapper._allocated
