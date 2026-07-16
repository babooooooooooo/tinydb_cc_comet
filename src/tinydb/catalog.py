"""Catalog persisted as JSON on page 1; INT fields encoded as strings (R8 mitigation)."""
import json
from dataclasses import dataclass, field
from typing import Optional

from tinydb.pager import PAGE_SIZE

CATALOG_PAGE_ID = 1


@dataclass
class TableInfo:
    schema: list[tuple[str, str]]
    root_page_id: int
    next_page_id: int


def _enc_int(v: int) -> str:
    return str(v)


def _dec_int(v) -> int:
    if isinstance(v, str):
        return int(v)
    return int(v)


class Catalog:
    def __init__(self):
        self.tables: dict[str, TableInfo] = {}

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Catalog":
        text = raw.rstrip(b"\x00").decode("utf-8")
        if not text:
            return cls()
        data = json.loads(text)
        c = cls()
        for name, info in data.get("tables", {}).items():
            c.tables[name] = TableInfo(
                schema=[(c_, t_) for c_, t_ in info["schema"]],
                root_page_id=_dec_int(info["root_page_id"]),
                next_page_id=_dec_int(info["next_page_id"]),
            )
        return c

    def to_bytes(self) -> bytes:
        data = {
            "tables": {
                name: {
                    "schema": [[c, t] for c, t in ti.schema],
                    "root_page_id": _enc_int(ti.root_page_id),
                    "next_page_id": _enc_int(ti.next_page_id),
                }
                for name, ti in self.tables.items()
            }
        }
        text = json.dumps(data, separators=(",", ":")).encode("utf-8")
        if len(text) > PAGE_SIZE:
            raise ValueError("catalog page overflow")
        return text + b"\x00" * (PAGE_SIZE - len(text))

    def create_table(
        self,
        name: str,
        schema: list[tuple[str, str]],
        root_page_id: int,
        next_page_id: int,
    ) -> None:
        if name in self.tables:
            raise ValueError(f"table {name!r} already exists")
        self.tables[name] = TableInfo(
            schema=schema,
            root_page_id=root_page_id,
            next_page_id=next_page_id,
        )

    def drop_table(self, name: str) -> None:
        if name not in self.tables:
            raise KeyError(f"no such table: {name}")
        del self.tables[name]

    def get_table(self, name: str) -> Optional[TableInfo]:
        return self.tables.get(name)
