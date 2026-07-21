"""Catalog persisted as JSON on page 1; INT fields encoded as strings (R8 mitigation).

When the serialized catalog exceeds a single 4KB page, it splits across a
linked overflow chain starting at ``CHAIN_HEAD_PAGE`` (= 1). Each chain
page reserves a 4-byte ``next_page_id`` header at offset 0 followed by 12
bytes of padding (``CHAIN_SEG_HEADER`` = 16 bytes total); the remaining
``PAGE_SIZE - CHAIN_SEG_HEADER`` bytes hold the JSON payload. The final
page's ``next_page_id`` is 0 (sentinel).
"""
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from tinydb.errors import InvalidDatabaseFile
from tinydb.pager import PAGE_SIZE

if TYPE_CHECKING:
    from tinydb.pager import Pager

CATALOG_PAGE_ID = 1
CHAIN_HEAD_PAGE = 1
CHAIN_SEG_HEADER = 16  # u32 next_page_id + 12 bytes padding reserved per chain page
CHAIN_BODY_SIZE = PAGE_SIZE - CHAIN_SEG_HEADER  # 4080 bytes of JSON per chain page
CHAIN_THRESHOLD = CHAIN_BODY_SIZE - 64  # safety margin below CHAIN_BODY_SIZE


@dataclass(frozen=True)
class Column:
    """Column metadata with column-level constraints.

    Persisted as a JSON object produced and consumed by
    ``to_dict``/``from_dict``.
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

    def __post_init__(self) -> None:
        # Validate Column types at the type-system layer so every TableInfo
        # construction site (create_table / from_bytes / _unpack_chain) is
        # protected — not just the create_table call path.
        for c in self.columns:
            if not isinstance(c, Column):
                raise TypeError(
                    f"TableInfo expects Column instances, "
                    f"got {type(c).__name__}: {c!r}"
                )

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
    """Load column from v2 object format produced by Column.to_dict()."""
    if isinstance(item, list):
        raise InvalidDatabaseFile(
            f"unrecognized column entry: {item!r} "
            "(legacy [name, type] arrays are no longer supported — "
            "please migrate to v2 object format)"
        )
    if not isinstance(item, dict):
        raise InvalidDatabaseFile(
            f"unrecognized column entry: {item!r} "
            "(expected Column.to_dict() object form)"
        )
    return Column.from_dict(item)


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
            cols = tuple(_load_column(item) for item in schema_entries)
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
        schema: tuple[Column, ...],
        root_page_id: int,
        next_page_id: int,
    ) -> None:
        if name in self.tables:
            raise ValueError(f"table {name!r} already exists")
        # Column type-check lives in TableInfo.__post_init__ — single source
        # of truth for every TableInfo construction site.
        self.tables[name] = TableInfo(
            name=name,
            columns=tuple(schema),
            root_page_id=root_page_id,
            next_page_id=next_page_id,
        )

    def drop_table(self, name: str) -> None:
        if name not in self.tables:
            raise KeyError(f"no such table: {name}")
        del self.tables[name]

    def get_table(self, name: str) -> Optional[TableInfo]:
        return self.tables.get(name)

    @classmethod
    def load_from_pager(cls, pager: "Pager") -> "Catalog":
        """Load catalog from pager's overflow chain (Task 2 entry point)."""
        return _unpack_chain(pager)


# ---------------------------------------------------------------------------
# Multi-page overflow chain (Task 2 of tinydb-engine-v2)
# ---------------------------------------------------------------------------


def _table_entry_dict(ti: TableInfo) -> dict:
    """Return the JSON-serializable dict for one table entry."""
    return {
        "schema": [c.to_dict() for c in ti.columns],
        "root_page_id": _enc_int(ti.root_page_id),
        "next_page_id": _enc_int(ti.next_page_id),
    }


def _serialize_segments(catalog: "Catalog") -> list[bytes]:
    """Serialize ``catalog`` into one or more JSON segments.

    Each segment is the JSON encoding of a partial ``{"tables": {...}}``
    payload that fits inside ``CHAIN_THRESHOLD`` bytes. The greedy split
    packs whole table entries into the current segment until the next
    entry would push it past the threshold; that entry starts the next
    segment. Empty catalogs produce a single empty ``{}`` segment so
    :func:`_unpack_chain` always has at least one page to walk.
    """
    if not catalog.tables:
        return [b"{}"]

    full = json.dumps(
        {"tables": {n: _table_entry_dict(ti) for n, ti in catalog.tables.items()}},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(full) <= CHAIN_THRESHOLD:
        return [full]

    # Greedy split by table entries.
    segments: list[bytes] = []
    cur_tables: dict = {}
    for name, ti in catalog.tables.items():
        cur_tables[name] = _table_entry_dict(ti)
        seg = json.dumps({"tables": cur_tables}, separators=(",", ":")).encode("utf-8")
        if len(seg) > CHAIN_THRESHOLD and len(cur_tables) > 1:
            # Pop the entry that overflowed; it starts the next segment.
            cur_tables.pop(name)
            seg = json.dumps({"tables": cur_tables}, separators=(",", ":")).encode("utf-8")
            segments.append(seg)
            cur_tables = {name: _table_entry_dict(ti)}
    if cur_tables:
        seg = json.dumps({"tables": cur_tables}, separators=(",", ":")).encode("utf-8")
        segments.append(seg)
    return segments


def _pack_chain(catalog: "Catalog") -> list[bytes]:
    """Return the catalog as a list of PAGE_SIZE-sized chain pages.

    Page layout (each entry):

        bytes 0..4   : ``next_page_id`` (u32 big-endian). 0 = tail.
        bytes 4..16  : reserved (zeros).
        bytes 16..   : zero-padded JSON payload (truncated to CHAIN_BODY_SIZE).

    The ``next_page_id`` field is set to 0 for every page; callers that
    allocate chain pages in sequence (e.g. :func:`Pager.write_catalog_chain`)
    are responsible for patching the head and intermediate pages' next_id
    after the chain is written.
    """
    pages: list[bytes] = []
    for seg in _serialize_segments(catalog):
        # Truncate oversize segments defensively; greedy split guarantees
        # each segment <= CHAIN_THRESHOLD < CHAIN_BODY_SIZE, so this is a
        # no-op in practice but prevents silent corruption if a future
        # caller widens CHAIN_THRESHOLD.
        body = seg[:CHAIN_BODY_SIZE]
        payload = b"\x00\x00\x00\x00" + b"\x00" * (CHAIN_SEG_HEADER - 4) + body
        if len(payload) < PAGE_SIZE:
            payload += b"\x00" * (PAGE_SIZE - len(payload))
        pages.append(payload)
    return pages


def _unpack_chain(pager: "Pager") -> "Catalog":
    """Walk the catalog overflow chain starting at ``CHAIN_HEAD_PAGE`` and
    reconstruct a :class:`Catalog`.
    """
    cat = Catalog()
    tables: dict = {}
    pid = CHAIN_HEAD_PAGE
    # Guard against malformed chains: at most ``page_count()`` hops.
    visited = 0
    page_cap = pager.page_count() + 1
    while pid != 0:
        if visited > page_cap:
            raise InvalidDatabaseFile(
                f"catalog chain exceeds page_count ({page_cap}); loop?"
            )
        visited += 1
        page = pager.read_page(pid)
        next_id = int.from_bytes(page[0:4], "big")
        body = page[CHAIN_SEG_HEADER:].rstrip(b"\x00").decode("utf-8")
        if body:
            data = json.loads(body)
            for name, info in data.get("tables", {}).items():
                tables[name] = info
        pid = next_id
    # Materialize TableInfo objects (last-writer-wins on duplicates).
    for name, info in tables.items():
        cols = tuple(_load_column(c_) for c_ in info["schema"])
        cat.tables[name] = TableInfo(
            name=name,
            columns=cols,
            root_page_id=_dec_int(info["root_page_id"]),
            next_page_id=_dec_int(info["next_page_id"]),
        )
    return cat


# Patch Catalog with a classmethod that walks the chain. Done here (after
# _unpack_chain is defined) rather than inside the class body to keep the
# class definition small and avoid forward-reference surprises.
