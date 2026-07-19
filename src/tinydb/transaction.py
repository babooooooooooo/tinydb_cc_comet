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
        self.pending_writes[page_id] = data
        self._pager.wal_append_page(self.id, page_id, data)

    def commit(self) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        for pid, data in self.pending_writes.items():
            self._pager.write_main_page(pid, data)
        self._pager.wal_append_commit(self.id)
        self._pager.fsync_main()
        self._pager.wal_truncate_before(self.id)
        self._state = TxnState.COMMITTED

    def rollback(self) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        self._pager.wal_append_rollback(self.id)
        self._pager.wal_truncate_before(self.id)
        self._state = TxnState.ROLLED_BACK
