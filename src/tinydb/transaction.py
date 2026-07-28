"""Transaction state machine: ACTIVE → COMMITTED | ROLLED_BACK."""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinydb.pager import Pager


class TxnState(Enum):
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


class InvalidTxnState(Exception):
    """Raised when write_page / commit / rollback called in non-ACTIVE state."""

    def __init__(self, txn_id: int, state: TxnState):
        self.txn_id = txn_id
        self.state = state
        super().__init__(f"transaction {txn_id} is {state.value}, not active")


class Transaction:
    def __init__(self, txn_id: int, pager: "Pager"):
        self.id = txn_id
        self._pager = pager
        self._state: TxnState = TxnState.ACTIVE
        self.pending_writes: dict[int, bytes] = {}

    @property
    def state(self) -> TxnState:
        return self._state

    def write_page(self, page_id: int, data: bytes) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        # WAL-first: the WAL record must be appended BEFORE pending_writes
        # is mutated. A crash between the two operations must not leave a
        # phantom main-page write planned without a recoverable WAL entry.
        self._pager.wal_append_page(self.id, page_id, data)
        self.pending_writes[page_id] = data

    def commit(self) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        try:
            # Write-ahead protocol: COMMIT record + fsync barrier FIRST so
            # a crash that loses the main file still has a recoverable
            # COMMIT in the WAL. Recovery's ``_apply_committed`` will then
            # re-apply the pending page writes idempotently.
            self._pager.wal_append_commit(self.id)
            self._pager.fsync_wal()
            # Now apply the pending writes to the main file.
            for pid, data in self.pending_writes.items():
                self._pager.write_main_page(pid, data)
            self._pager.fsync_main()
            # Truncate with id+1 so the COMMIT record itself survives —
            # a future crash replay will encounter it as a no-op (recovery
            # only applies pages from transactions with status="committed";
            # the reapply of already-applied pages is harmless).
            self._pager.wal_truncate_before(self.id + 1)
            self._state = TxnState.COMMITTED
        except Exception:
            # Mid-commit failure: never leave the transaction ACTIVE. The
            # caller will see the original exception, and any partial
            # main-file writes will be either overwritten by the next
            # transaction or repaired on recovery replay.
            self._state = TxnState.ROLLED_BACK
            raise

    def rollback(self) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        self._pager.wal_append_rollback(self.id)
        # id+1 mirrors commit() — keep the ROLLBACK record itself so
        # recovery can observe the explicit rollback status (skipping
        # any pending page writes for this txn_id).
        self._pager.wal_truncate_before(self.id + 1)
        self._state = TxnState.ROLLED_BACK
