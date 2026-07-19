"""IndexPager wrapper that tracks every page the IndexManager/BTree allocates.

Extracted from ``executor.py`` to keep the latter under its module line
budget (Risk R7 in the tinydb-acid design doc).

B+tree splits allocate new pages via ``self.pager.alloc_page()`` inside
``BTree.insert`` / ``BTree._insert_into_parent`` — pages that the
Executor's data-page chain would happily walk into and corrupt on the
next ``PageFull``-driven ``pid += 1`` step. By tracking every page id
the index side hands out, ``Executor._alloc_data_page`` (and the
skip loop in ``Executor._insert_inline_only``) can guarantee the
data chain never collides with a B+tree node.

Forwarded methods: ``read_page``, ``write_page``, ``flush``, ``close``,
``alloc_page``, ``free_page`` (the last two update the tracker).

Transaction routing (tinydb-acid Task 6 follow-up): when an
``executor`` back-reference is supplied, every read/write/free flows
through :class:`Executor`'s ``_txn_*`` helpers so B+tree writes
participate in the active transaction (WAL append + ``_page_buffer``
shadow). Without this routing the B+tree leaf would mutate the main
file directly inside a BEGIN block, leaving a stale entry after
ROLLBACK. The executor parameter is optional for backward
compatibility with unit tests that construct an ``IndexPager``
standalone.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinydb.executor import Executor


class IndexPager:
    """Wrap a Pager to record every page the IndexManager/BTree allocates.

    See module docstring for the full contract.
    """

    def __init__(self, pager, executor: "Executor | None" = None):
        self._pager = pager
        self._executor = executor
        self._allocated: set[int] = set()

    def read_page(self, page_id: int) -> bytes:
        if self._executor is not None:
            return self._executor._txn_read_page(page_id)
        return self._pager.read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        if self._executor is not None:
            self._executor._txn_write_page(page_id, data)
            return
        self._pager.write_page(page_id, data)

    def alloc_page(self) -> int:
        pid = self._pager.alloc_page()
        self._allocated.add(pid)
        return pid

    def free_page(self, page_id: int) -> None:
        # BTree never frees pages, but keep the wrapper symmetric. Routes
        # through the txn layer so DROP TABLE inside a BEGIN block can
        # roll back the free-list head update on ROLLBACK.
        if self._executor is not None:
            self._executor._txn_free_page(page_id)
        else:
            self._pager.free_page(page_id)
        self._allocated.discard(page_id)

    def flush(self) -> None:
        self._pager.flush()

    def close(self) -> None:
        self._pager.close()

    def __deepcopy__(self, memo):
        """Deep-copy an IndexPager sharing the underlying Pager + Executor.

        ``_pager`` ultimately references a ``BufferedRandom`` file handle
        (``Pager._file``) that ``copy.deepcopy`` cannot pickle. Both the
        Pager and the Executor are process-singleton state; sharing the
        references is correct because ``Executor._snapshot_state`` only
        needs the ``_allocated`` set to be preserved (so the rollback
        restore still tracks which pages are owned by index B+trees).
        """
        new = IndexPager.__new__(IndexPager)
        new._pager = self._pager
        new._executor = self._executor
        new._allocated = set(self._allocated)
        memo[id(self)] = new
        return new

    @property
    def allocated(self) -> set[int]:
        return set(self._allocated)