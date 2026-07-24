"""Public API: Database + Row. MVP: non-ACID, no transactions. <= 90 lines (plan §6.1)."""
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple, Union

from tinydb.catalog import Catalog
from tinydb.executor import Executor
from tinydb.index_manager import IndexManager
from tinydb.pager import Pager
from tinydb.parser import parse, Select
from tinydb.plan import LogicalPlan  # noqa: F401  # explain_plan return-type annotation only
from tinydb.tokenizer import tokenize


@dataclass(frozen=True)
class Row:
    """Immutable row: aligned (values, columns) pair. ``__getattr__`` maps column name -> value."""
    values: tuple[Any, ...]
    columns: tuple[str, ...]

    def __post_init__(self) -> None:
        n_v, n_c = len(self.values), len(self.columns)
        if n_v != n_c:
            raise ValueError(f"Row length mismatch: values ({n_v}) and columns ({n_c}) must have equal lengths")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name not in self.columns:
            raise AttributeError(name)
        return self.values[self.columns.index(name)]

    def __getitem__(self, key):
        """支持 ``r["u.id"]`` 这类含点列名的下标访问（tinydb-join-query T6）。
        非 str key 走默认 tuple-like 索引（兼容 ``r[0]``）。"""
        if isinstance(key, str):
            if key not in self.columns:
                raise KeyError(key)
            return self.values[self.columns.index(key)]
        return self.values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self.values)

    def __repr__(self) -> str:
        return f"Row({', '.join(f'{c}={v!r}' for c, v in zip(self.columns, self.values))})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Row):
            return NotImplemented
        return self.columns == other.columns and self.values == other.values


class Database:
    """Public entry point. Use as context manager or call ``close()``."""

    def __init__(self, path: Union[str, Path] = ":memory:") -> None:
        """Open tinydb at ``path`` (file or ``":memory:"``).

        MVP: non-ACID, no crash safety. No ``begin``/``commit``/``rollback``;
        transaction support lives in tinydb-acid.
        """
        self.pager = Pager(path)
        self.catalog = Catalog.from_bytes(self.pager.read_page(1))
        # IndexManager: B+tree indexes per (table, col). For pre-existing
        # tables (post-reopen) rebuild via full scan so lookups reflect
        # on-disk data; for fresh tables INSERTs populate incrementally.
        self.index_manager = IndexManager(self.pager)
        self.executor = Executor(self.pager, self.catalog, self.index_manager)
        self.executor._database_ref = self  # mid-session CREATE TABLE hooks
        self._index_pagers: Dict[Tuple[str, str], Any] = {}
        # Existing tables: rebuild indexes, install _IndexPager wrappers,
        # and re-discover each table's data-page chain (extension pages
        # past the root aren't persisted in the in-memory list).
        for ti in self.catalog.tables.values():
            self.index_manager.rebuild_for_table(ti)
            self._install_index_pagers(ti.name)
            self.executor._table_data_pages[ti.name] = (
                self.executor._rebuild_data_pages_from_chain(ti)
            )
        # New tables mid-session install wrappers via _exec_create_table.

    def execute(self, sql: str) -> list[Row]:
        """Run one statement or ``;``-separated script; return final result.

        SELECT returns ``list[Row]``; DDL/INSERT/DELETE returns ``[]``.
        Raises ``ParseError``/``TokenError`` or ``ExecutionError``; no remapping.
        """
        tokens = tokenize(sql)
        stmts = parse(tokens)

        results: list[Row] = []
        for s in stmts.statements:
            out = self.executor.execute(s)
            if isinstance(out, list):
                results = out

        last = stmts.statements[-1] if stmts.statements else None
        if isinstance(last, Select) and results:
            # T6: JOIN path already returns list[Row]; skip re-wrap.
            if last.joins:
                if results and isinstance(results[0], Row):
                    return results
            ti = self.catalog.get_table(last.table)
            if ti is not None:
                cols = tuple(n for n, _ in ti.schema) if last.columns == ("*",) else tuple(last.columns)
                results = [Row(values=tuple(r), columns=cols) for r in results]
        return results

    def explain_plan(self, sql: str) -> "LogicalPlan":
        """Build a LogicalPlan from a SELECT without executing it.

        Read-only: tokenizes + parses + builds the immutable plan tree.
        Never calls the executor, scans tables or touches Pager/WAL.
        Final statement must be ``SELECT``; non-SELECT raises
        ``ExecutionError`` (parse errors propagate as ``ParseError``).
        """
        from tinydb.errors import ExecutionError as _EE
        from tinydb.plan import build_plan as _bp
        stmts = parse(tokenize(sql))
        last = stmts.statements[-1] if stmts.statements else None
        if last is None:
            raise _EE("explain_plan: empty SQL")
        if not isinstance(last, Select):
            raise _EE("explain_plan: only SELECT is supported")
        return _bp(last, self.catalog)

    def close(self) -> None:
        """Flush + close the Pager. Idempotent; ``close()`` runs even if ``flush()`` raises."""
        try:
            self.pager.flush()
        finally:
            self.pager.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _install_index_pagers(self, table_name: str) -> None:
        """Install _IndexPager wrappers on every B+tree of ``table_name``.

        Called from Database.__init__ for pre-existing tables and from
        Executor._exec_create_table for tables created mid-session. Each
        wrapper replaces ``bt.pager`` so every B+tree allocation (root +
        leaves from splits) flows through the tracker; the Executor then
        consults :meth:`_index_pages` to keep the data-page chain off
        B+tree pages.
        """
        for (tname, cname), bt in self.index_manager._indexes.items():
            if tname != table_name or bt.pager.__class__.__name__ == "_IndexPager":
                continue
            wrapper = self.executor._make_index_pager(self.pager)
            bt.pager = wrapper
            if bt.root_page_id is not None:
                wrapper._allocated.add(bt.root_page_id)
            self._index_pagers[(tname, cname)] = wrapper
