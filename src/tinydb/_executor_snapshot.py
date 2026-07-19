"""Snapshot / restore helpers for the Executor's ROLLBACK path.

Extracted from ``executor.py`` to keep the latter under its module line
budget (Risk R7 in the tinydb-acid design doc). Both helpers are free
functions taking an :class:`Executor` instance as their first argument
so they can read/write the Executor's mutable catalog, per-table data
pages, and index manager.
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinydb.executor import Executor


def snapshot_state(executor: "Executor") -> dict:
    """Capture the Executor's mutable state for ROLLBACK restore.

    Deep-copies the catalog (TableInfo graph) and the per-table data
    page list; shallow-copies the IndexManager's ``_indexes`` dict
    but deep-copies the BTree wrappers themselves so BTree.pager
    pointers and root_page_id stay correct. The page contents are
    NOT snapshotted — the Pager's main file is untouched during the
    txn, so discarding ``_page_buffer`` is sufficient to revert
    page-level state.
    """
    return {
        "catalog": copy.deepcopy(executor.catalog),
        "table_data_pages": copy.deepcopy(executor._table_data_pages),
        "indexes": copy.deepcopy(executor.index_manager._indexes),
    }


def restore_state(executor: "Executor", snap: dict) -> None:
    """Replace the Executor's mutable state with ``snap``.

    Also updates ``Database.catalog`` (if back-referenced) so the
    Database wrapper sees the reverted catalog without a separate
    reopen. The previously-live catalog and indexes are dropped;
    Python GC reclaims them along with the WAL/buffered pages.
    """
    executor.catalog = snap["catalog"]
    executor._table_data_pages = snap["table_data_pages"]
    executor.index_manager._indexes = snap["indexes"]
    db = getattr(executor, "_database_ref", None)
    if db is not None:
        db.catalog = executor.catalog