"""Public API: Database + Row. MVP: non-ACID, no transactions. <= 90 lines (plan §6.1)."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from tinydb.catalog import Catalog
from tinydb.executor import Executor
from tinydb.pager import Pager
from tinydb.parser import parse, Select
from tinydb.tokenizer import tokenize


@dataclass
class Row:
    """One SELECT row. ``values`` aligned with ``columns`` order."""
    values: list
    columns: list

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name in {"values", "columns"}:
            raise AttributeError(name)
        try:
            return self.values[self.columns.index(name)]
        except ValueError:
            raise AttributeError(name)

    def __iter__(self):
        return iter(self.values)

    def __repr__(self) -> str:
        return f"Row({', '.join(f'{c}={v!r}' for c, v in zip(self.columns, self.values))})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Row):
            return NotImplemented
        return self.columns == other.columns and self.values == other.values


class Database:
    """Public entry point. Use as context manager or call ``close()``."""

    def __init__(self, path: Union[str, Path] = ":memory:"):
        """Open tinydb at ``path`` (filesystem path or ``":memory:"``).

        MVP: non-ACID, no crash safety. No ``begin``/``commit``/``rollback``
        (transaction support lives in tinydb-acid).
        """
        self.pager = Pager(path)
        self.catalog = Catalog.from_bytes(self.pager.read_page(1))
        self.executor = Executor(self.pager, self.catalog)

    def execute(self, sql: str) -> list:
        """Run one statement or ``;``-separated script; return final result.

        SELECT returns ``list[Row]``; DDL/INSERT/DELETE returns ``[]``.
        Raises ``ParseError``/``TokenError`` (parser) or ``ExecutionError``
        (executor); no remapping.
        """
        tokens = tokenize(sql)
        stmts = parse(tokens)

        results: list = []
        for s in stmts.statements:
            out = self.executor.execute(s)
            if isinstance(out, list):
                results = out

        last = stmts.statements[-1] if stmts.statements else None
        if isinstance(last, Select) and results:
            ti = self.catalog.get_table(last.table)
            if ti is not None:
                cols = (
                    [n for n, _ in ti.schema]
                    if last.columns == ["*"]
                    else list(last.columns)
                )
                results = [Row(values=list(r), columns=cols) for r in results]
        return results

    def close(self) -> None:
        """Flush + close the Pager. Idempotent."""
        self.pager.flush()
        self.pager.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()