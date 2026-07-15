"""Public API: Database + Row. MVP: non-ACID, no transactions. <= 90 lines (plan §6.1)."""
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from tinydb.catalog import Catalog
from tinydb.executor import Executor
from tinydb.pager import Pager
from tinydb.parser import parse, Select
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
        self.executor = Executor(self.pager, self.catalog)

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
            ti = self.catalog.get_table(last.table)
            if ti is not None:
                cols = tuple(n for n, _ in ti.schema) if last.columns == ["*"] else tuple(last.columns)
                results = [Row(values=tuple(r), columns=cols) for r in results]
        return results

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
