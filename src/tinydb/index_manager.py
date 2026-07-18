"""IndexManager: B+tree index per (table, indexed-column) pair."""
from __future__ import annotations

from tinydb.btree import BTree
from tinydb.type_system import codec_for


class IndexManager:
    def __init__(self, pager):
        self._pager = pager
        self._indexes: dict[tuple[str, str], BTree] = {}

    def indexed_columns(self, ti) -> list:
        """Return columns that should have an index (PK + UNIQUE)."""
        cols = []
        for c in ti.columns:
            if c.primary_key or c.unique:
                cols.append(c)
        return cols

    def key_for(self, col, value):
        """Encode a Python value to B+tree key bytes via codec_for()."""
        codec = codec_for(col.type, col.type_params)
        return codec.encode_py(value)

    def rebuild_for_table(self, ti, rows=None) -> None:
        """Build B+tree indexes for all indexed columns of ti.

        ``rows`` is a list of row tuples (decoded values, length == len(ti.columns)).
        In production this comes from a full table scan.
        """
        for col in self.indexed_columns(ti):
            bt = BTree(pager=self._pager, root_page_id=None)
            if rows is not None:
                col_idx = next(i for i, c in enumerate(ti.columns) if c.name == col.name)
                for slot_id, row in enumerate(rows):
                    value = row[col_idx]
                    if value is None:
                        continue  # NULL not indexed (R9)
                    key = self.key_for(col, value)
                    bt.insert(key, (0, slot_id))  # (page_id=0 placeholder, slot_id)
            self._indexes[(ti.name, col.name)] = bt

    def lookup_key(self, table_name: str, column_name: str, key: bytes) -> tuple[int, int] | None:
        bt = self._indexes.get((table_name, column_name))
        if bt is None:
            return None
        return bt.search(key)

    def insert(self, table_name: str, column_name: str, key: bytes, slot_ref: tuple[int, int]) -> None:
        bt = self._indexes[(table_name, column_name)]
        bt.insert(key, slot_ref)

    def delete(self, table_name: str, column_name: str, key: bytes) -> None:
        bt = self._indexes.get((table_name, column_name))
        if bt is not None:
            bt.delete(key)

    def get_btree(self, table_name: str, column_name: str):
        return self._indexes.get((table_name, column_name))
