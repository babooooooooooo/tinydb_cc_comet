"""DROP TABLE machinery for the Executor.

This module holds the DROP TABLE implementation + helpers that were
extracted from ``executor.py`` to keep the latter under its module line
budget (Risk R7 in the tinydb-acid design doc). All the helpers are
free functions that take an :class:`Executor` instance as their first
argument so they can reuse the Executor's catalog, index manager, and
txn-aware page-IO helpers (``_txn_read_page`` / ``_txn_write_page`` /
``_txn_free_page``).

The Executor's :meth:`Executor._exec_drop_table` is a thin delegator
over :func:`exec_drop_table` so callers see the same API. Unit tests
that reach for the helpers directly should import from this module.

Why a separate module (not a method on Executor)?
================================================

The DROP machinery plus the two collection helpers is ~150 lines. Pulling
it into its own file keeps ``executor.py`` focused on dispatch and the
DML hot path (INSERT/SELECT/DELETE/UPDATE) and makes the reclamation
contract easier to reason about in isolation. Nothing about the
``_exec_drop_table`` API has changed — callers continue to dispatch via
the Executor's public ``execute`` method.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from tinydb.btree import InternalNode, NODE_TYPE_INTERNAL
from tinydb.catalog import TableInfo
from tinydb.errors import ExecutionError
from tinydb.parser import DropTable
from tinydb.slotted_page import NULL_PAGE_ID

if TYPE_CHECKING:
    from tinydb.executor import Executor


def exec_drop_table(executor: "Executor", stmt: DropTable) -> list:
    """Drop a table and reclaim its data + index pages via the free list.

    Walks the table's contiguous data page chain (skipping any page id
    owned by a B+tree wrapper) plus any per-page overflow chains for
    spilled rows; frees them all. Then walks every B+tree index for
    the table's PK + UNIQUE columns, frees their nodes via the per-
    BTree ``_IndexPager`` wrapper (which clears the wrapper's
    ``_allocated`` set), and finally forgets the B+trees in
    :class:`IndexManager`. The catalog is persisted inline (single
    page) to stay consistent with ``_exec_create_table``.

    Task 8 of ``tinydb-engine-v2`` (DROP TABLE reclamation), with
    ``tinydb-acid`` (Task 6) adding txn routing so a ROLLBACK of this
    DROP restores the free-list head.
    """
    catalog = executor.catalog
    ti = catalog.get_table(stmt.name)
    if ti is None:
        raise ExecutionError(f"table {stmt.name!r} does not exist")

    # Collect page ids BEFORE removing from catalog (we need ``ti``).
    data_pids = collect_table_data_pages(executor, ti)
    index_pids = collect_index_pages(executor, ti)

    # Drop from catalog first so subsequent persistence writes a
    # consistent catalog. The page ids are already captured above.
    catalog.drop_table(stmt.name)
    # Drop the per-table data page list so a future CREATE TABLE with
    # the same name starts with a fresh entry (no stale page ids).
    executor._table_data_pages.pop(stmt.name, None)

    # Free data pages via the txn layer so a ROLLBACK of this DROP
    # restores the free-list head. ``Pager.free_page`` modifies
    # page 0's free_list_head and the freed page's first 4 bytes
    # directly; routing through ``_txn_free_page`` writes both to
    # the WAL + page buffer so they participate in the txn.
    for pid in data_pids:
        executor._txn_free_page(pid)

    # Free index pages. When a BTree's pager is an ``IndexPager``
    # wrapper (the Database-installed path), use the wrapper's
    # ``free_page`` so its ``_allocated`` tracking is cleared; this
    # prevents phantom "owned" entries from polluting the Executor's
    # collision avoidance after the BTree is forgotten. When no
    # wrapper is present (e.g., standalone Executor in unit tests),
    # fall back to freeing via the raw pager.
    for col in ti.columns:
        if not (col.primary_key or col.unique):
            continue
        bt = executor.index_manager.get_btree(stmt.name, col.name)
        if bt is None:
            continue
        wrapper = bt.pager if type(bt.pager).__name__ in ("_IndexPager", "IndexPager") else None
        if wrapper is not None:
            for pid in list(wrapper._allocated):
                wrapper.free_page(pid)
        else:
            for pid in index_pids:
                executor.pager.free_page(pid)

    # Forget B+trees for this table. After this the IndexManager has
    # no record of the dropped table; the corresponding wrapper
    # instances are left in ``self._index_pagers`` with empty
    # ``_allocated`` sets (harmless).
    executor.index_manager.forget_table(stmt.name)

    # Persist catalog. We use the inline format ``write_page(1,
    # to_bytes())`` to stay consistent with ``_exec_create_table`` and
    # ``_insert_inline_only`` (which both write inline format). The
    # chain-format writer ``Pager.write_catalog_chain`` is reserved
    # for future multi-page overflow support; mixing the two
    # formats breaks ``Catalog.from_bytes`` on subsequent opens.
    executor._txn_write_page(1, catalog.to_bytes())
    executor.pager.flush()
    return []


def collect_table_data_pages(executor: "Executor", ti: TableInfo) -> list[int]:
    """Return every page id used by ``ti``'s data chain + spill chains.

    Walks the range ``[ti.root_page_id, ti.next_page_id]`` and collects
    only DATA pages, skipping any page id that is currently tracked by
    a B+tree ``_IndexPager`` wrapper. The data chain is NOT contiguous
    in the presence of indexes — ``_insert_inline_only`` advances by
    ``pid += 1`` while skipping index pages — so the catalog's
    ``next_page_id`` may equal a page id owned by a B+tree.

    For each data page, follows its ``overflow_next`` link to pick up
    any overflow pages used for spilled rows. We defensively treat
    both ``0`` and ``NULL_PAGE_ID`` as "no overflow chain" because
    freshly-allocated data pages haven't had ``overflow_next``
    initialized; we additionally require the target page to have
    ``page_type == 2`` (overflow) before following it.
    """
    pids: list[int] = []
    seen: set[int] = set()
    if ti.root_page_id == 0 or ti.next_page_id < ti.root_page_id:
        return pids
    # Index page ids are owned by B+tree wrappers — they live in the
    # address space between data pages and must not be freed as data.
    index_pages = executor._index_pages()
    pid = ti.root_page_id
    end = ti.next_page_id
    while pid <= end:
        if pid in index_pages:
            pid += 1
            continue
        if pid not in seen:
            seen.add(pid)
            pids.append(pid)
            # Follow the per-page overflow chain (bytes 4:8 of every
            # data page hold the next overflow page id, 0 or
            # ``NULL_PAGE_ID`` on the tail).
            nxt = int.from_bytes(executor._txn_read_page(pid)[4:8], "big")
            while nxt > 0 and nxt != NULL_PAGE_ID and nxt not in seen:
                target_raw = executor._txn_read_page(nxt)
                if target_raw[0] != 2:
                    break
                seen.add(nxt)
                pids.append(nxt)
                nxt = int.from_bytes(target_raw[4:8], "big")
        pid += 1
    return pids


def collect_index_pages(executor: "Executor", ti: TableInfo) -> list[int]:
    """Return every page id used by every B+tree index for ``ti``.

    Walks each indexed column's B+tree by iterative descent: pop a
    page off the stack, deserialize it, and push internal-node
    children until only leaves remain. Leaf pages are collected but
    not descended into. Duplicate page ids (which can't occur in a
    well-formed B+tree) are skipped defensively.
    """
    pids: list[int] = []
    seen: set[int] = set()
    for col in ti.columns:
        if not (col.primary_key or col.unique):
            continue
        bt = executor.index_manager.get_btree(ti.name, col.name)
        if bt is None or bt.root_page_id is None:
            continue
        stack: list[int] = [bt.root_page_id]
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            pids.append(pid)
            page = executor._txn_read_page(pid)
            if page[0] == NODE_TYPE_INTERNAL:
                node = InternalNode.deserialize(page)
                stack.extend(node.children)
    return pids