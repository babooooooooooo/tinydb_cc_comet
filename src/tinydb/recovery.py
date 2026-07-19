"""Crash recovery: replay committed transactions from WAL into main db file."""
from __future__ import annotations

import os

from tinydb.wal import Wal, WalCorruption

# Re-entry guard: ``Pager.__init__`` calls ``_init_wal`` which invokes
# ``Recovery.replay`` whenever a WAL file is present. Without this guard,
# ``_apply_committed`` (which constructs a Pager) would trigger an infinite
# re-replay against the same WAL.
_REPLAY_IN_PROGRESS: bool = False


class Recovery:
    @staticmethod
    def replay(main_path: str, wal: Wal) -> None:
        """Scan WAL, apply committed txns to main file, discard incomplete.

        On WalCorruption: applies all valid records before the corrupt one,
        truncates WAL to the corrupt boundary, then re-raises the exception.
        """
        global _REPLAY_IN_PROGRESS
        if _REPLAY_IN_PROGRESS:
            # Re-entry from Pager.__init__ inside our own _apply_committed.
            return
        _REPLAY_IN_PROGRESS = True
        try:
            pending: dict[int, dict[int, bytes]] = {}
            status: dict[int, str] = {}

            try:
                for txn_id, kind, page_id, data in wal.iter_records():
                    if kind == 0:  # BEGIN
                        status[txn_id] = "active"
                        pending.setdefault(txn_id, {})
                    elif kind == 1:  # PAGE_WRITE
                        pending.setdefault(txn_id, {})[page_id] = data
                    elif kind == 2:  # COMMIT
                        status[txn_id] = "committed"
                    elif kind == 3:  # ROLLBACK
                        status[txn_id] = "rolled_back"
                    # kind == 4 (CHECKPOINT) — skip
            except WalCorruption as e:
                offset = getattr(e, "offset", 0)
                _truncate_wal_to(main_path + ".wal", offset)
                _apply_committed(main_path, pending, status)
                raise

            _apply_committed(main_path, pending, status)
            # Truncate entire WAL — all records processed
            wal.truncate_before(_max_txn_id(pending) + 1 if pending else 1)
        finally:
            _REPLAY_IN_PROGRESS = False


def _max_txn_id(pending: dict[int, dict[int, bytes]]) -> int:
    return max(pending.keys()) if pending else 0


def _truncate_wal_to(wal_path: str, offset: int) -> None:
    if not os.path.exists(wal_path):
        return
    keep = max(16, offset)  # always keep header
    with open(wal_path, "r+b") as f:
        f.truncate(keep)


def _apply_committed(main_path: str, pending: dict[int, dict[int, bytes]], status: dict[int, str]) -> None:
    from tinydb.pager import Pager
    p = Pager(main_path)
    try:
        for txn_id in sorted(pending.keys()):
            if status.get(txn_id) != "committed":
                continue
            for page_id, data in pending[txn_id].items():
                p.write_main_page(page_id, data)
        p.fsync_main()
    finally:
        p.close()
