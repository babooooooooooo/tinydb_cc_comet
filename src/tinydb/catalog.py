"""Catalog persisted as JSON on page 1; INT fields encoded as strings (R8 mitigation)."""
import json
from dataclasses import dataclass
from typing import Optional

from tinydb.errors import InvalidDatabaseFile
from tinydb.pager import PAGE_SIZE

CATALOG_PAGE_ID = 1


@dataclass(frozen=True)
class Column:
    """Column metadata with column-level constraints.

    Persisted as a JSON object (see ``to_dict``/``from_dict``). Legacy
    catalogs that stored schema as ``[[name, type], ...]`` are loaded
    with the SQL92 defaults: ``nullable=True``, ``unique=False``,
    ``primary_key=False`` (D3 裁决).
    """

    name: str
    type: str
    type_params: tuple = ()
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "type_params": list(self.type_params),
            "nullable": self.nullable,
            "unique": self.unique,
            "primary_key": self.primary_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Column":
        return cls(
            name=d["name"],
            type=d["type"],
            type_params=tuple(d.get("type_params", ())),
            nullable=d.get("nullable", True),
            unique=d.get("unique", False),
            primary_key=d.get("primary_key", False),
        )


@dataclass
class TableInfo:
    columns: tuple[Column, ...]
    root_page_id: int
    next_page_id: int
    name: str = ""

    @property
    def schema(self) -> list[tuple[str, str]]:
        """Read-only ``[(name, type)]`` projection for row_codec and other
        legacy consumers (database.Row, REPL ``.schema``). New code should
        read ``self.columns`` directly."""
        return [(c.name, c.type) for c in self.columns]

    @property
    def schema_v2(self) -> list[tuple[str, str, tuple]]:
        """Canonical ``[(name, type, type_params)]`` projection for row_codec
        v2 and other code paths that need parametric type info (VARCHAR(N),
        CHAR(N), DECIMAL(p, s))."""
        return [(c.name, c.type, c.type_params) for c in self.columns]


def _enc_int(v: int) -> str:
    return str(v)


def _dec_int(v) -> int:
    if isinstance(v, str):
        return int(v)
    return int(v)


def _load_column(item) -> Column:
    """Dual-format loader: accepts legacy ``[name, type]`` arrays and new
    ``{name, type, nullable, unique, primary_key}`` objects. Mixed forms
    inside a single table are not allowed (R1 mitigation)."""
    if isinstance(item, list):
        if len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str):
            return Column(name=item[0], type=item[1])
        raise InvalidDatabaseFile(f"unrecognized column entry: {item!r}")
    if isinstance(item, dict):
        return Column.from_dict(item)
    raise InvalidDatabaseFile(f"unrecognized column entry: {item!r}")


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
            schema_entries = info["schema"]
            # R1 mitigation (D3): a single table's schema must be uniformly
            # legacy-list or new-object format. Mixing is a serialization bug.
            kinds = {type(item).__name__ for item in schema_entries}
            if len(kinds) > 1:
                raise InvalidDatabaseFile(
                    f"table {name!r}: mixed legacy/new column formats not allowed"
                )
            cols = tuple(_load_column(c_) for c_ in schema_entries)
            c.tables[name] = TableInfo(
                name=name,
                columns=cols,
                root_page_id=_dec_int(info["root_page_id"]),
                next_page_id=_dec_int(info["next_page_id"]),
            )
        return c

    def to_bytes(self) -> bytes:
        data = {
            "tables": {
                name: {
                    "schema": [c.to_dict() for c in ti.columns],
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
        schema,  # tuple[Column, ...] or list[tuple[str, str]] (legacy)
        root_page_id: int,
        next_page_id: int,
    ) -> None:
        if name in self.tables:
            raise ValueError(f"table {name!r} already exists")
        # Accept both Column tuples and legacy ``[(name, type), ...]`` so
        # existing callers keep working during migration.
        if schema and isinstance(schema[0], Column):
            cols: tuple[Column, ...] = tuple(schema)
        else:
            cols = tuple(Column(name=n, type=t) for n, t in schema)
        self.tables[name] = TableInfo(
            name=name,
            columns=cols,
            root_page_id=root_page_id,
            next_page_id=next_page_id,
        )

    def drop_table(self, name: str) -> None:
        if name not in self.tables:
            raise KeyError(f"no such table: {name}")
        del self.tables[name]

    def get_table(self, name: str) -> Optional[TableInfo]:
        return self.tables.get(name)
