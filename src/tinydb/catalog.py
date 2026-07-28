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

from tinydb.errors import CatalogCorrupt, InvalidDatabaseFile
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

    The contract on every returned segment: ``len(seg) <= CHAIN_BODY_SIZE``.
    Three cases:

    1. Empty catalog → single ``b"{}"`` sentinel segment.
    2. Whole-catalog payload fits in :data:`CHAIN_THRESHOLD` → single segment.
    3. Otherwise, greedy pack whole tables into segments, capped at
       ``CHAIN_THRESHOLD`` per segment. If a SINGLE table's serialized
       payload exceeds ``CHAIN_BODY_SIZE`` (so it can never fit in one
       segment regardless of neighbors), that table is split by columns
       via :func:`_split_single_table`; its metadata (``root_page_id`` /
       ``next_page_id``) lives on the FIRST sub-segment so the unpacker
       can recover the full schema on first sight and append columns
       from continuation segments.

    Greedy + column-split together guarantee the body-budget invariant
    that :func:`_pack_chain` relies on (no silent truncation required).
    """
    if not catalog.tables:
        return [b"{}"]

    full = json.dumps(
        {"tables": {n: _table_entry_dict(ti) for n, ti in catalog.tables.items()}},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(full) <= CHAIN_THRESHOLD:
        return [full]

    segments: list[bytes] = []
    cur_tables: dict = {}

    def _flush_cur() -> None:
        """Append the current ``cur_tables`` (if non-empty) as a segment.

        Raises :class:`CatalogCorrupt` if the size budget is violated —
        indicates a bug in this algorithm, since greedy packing should
        always keep us under ``CHAIN_BODY_SIZE``.
        """
        if not cur_tables:
            return
        seg = json.dumps({"tables": cur_tables}, separators=(",", ":")).encode("utf-8")
        if len(seg) > CHAIN_BODY_SIZE:
            raise CatalogCorrupt(
                f"greedy-pack produced {len(seg)}-byte segment "
                f"(> CHAIN_BODY_SIZE {CHAIN_BODY_SIZE})"
            )
        segments.append(seg)
        cur_tables.clear()

    for name, ti in catalog.tables.items():
        entry = _table_entry_dict(ti)
        # Check whether this table alone fits in one segment. If not, we
        # must split-by-columns: flush current greedy buffer, then emit
        # the sub-segments.
        single_seg_payload = json.dumps(
            {"tables": {name: entry}}, separators=(",", ":")
        ).encode("utf-8")
        if len(single_seg_payload) > CHAIN_BODY_SIZE:
            _flush_cur()
            for sub in _split_single_table(name, entry):
                if len(sub) > CHAIN_BODY_SIZE:
                    raise CatalogCorrupt(
                        f"_split_single_table emitted {len(sub)}-byte "
                        f"sub-segment (> CHAIN_BODY_SIZE {CHAIN_BODY_SIZE})"
                    )
                segments.append(sub)
            continue

        # Normal greedy-add path: include this table in the running buffer;
        # if the buffer would exceed CHAIN_THRESHOLD, flush and restart with
        # just this table in the buffer.
        candidate = dict(cur_tables)
        candidate[name] = entry
        candidate_seg = json.dumps(
            {"tables": candidate}, separators=(",", ":")
        ).encode("utf-8")
        if len(candidate_seg) <= CHAIN_THRESHOLD:
            cur_tables[name] = entry
        else:
            # Flush current buffer (without this entry) and start a new one
            # holding this entry alone.
            _flush_cur()
            cur_tables[name] = entry

    # Final flush of any trailing buffer.
    _flush_cur()
    return segments


def _split_single_table(name: str, entry: dict) -> list[bytes]:
    """Split ONE oversized table entry into multiple column-chunk segments.

    Each returned segment is a self-contained JSON document of the form
    ``{"tables": {<name>: {"schema": [...], "root_page_id": ..., "next_page_id": ...}}}``
    where the schema list holds a disjoint subset of the original columns
    in original order. The FIRST segment carries the table's
    ``root_page_id`` and ``next_page_id``; continuation segments carry
    only their column slice so :func:`_unpack_chain` can recover the
    metadata on first sight and append columns on each subsequent sight.

    Raises :class:`CatalogCorrupt` if a SINGLE column exceeds
    ``CHAIN_BODY_SIZE`` (no further splitting is possible). That case is
    a known project limitation, documented alongside MVP_LIMITATIONS.
    """
    columns = entry.get("schema", [])
    if not columns:
        raise CatalogCorrupt(
            f"cannot split single-table entry {name!r}: no columns"
        )

    # Pre-flight: can a single column ever fit in a segment?
    sample = json.dumps(
        {"tables": {name: {"schema": [columns[0]],
                            "root_page_id": entry["root_page_id"],
                            "next_page_id": entry["next_page_id"]}}},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(sample) > CHAIN_BODY_SIZE:
        raise CatalogCorrupt(
            f"single column {columns[0]['name']!r} of table {name!r} "
            f"serializes to {len(sample)} bytes (> CHAIN_BODY_SIZE "
            f"{CHAIN_BODY_SIZE}); cannot split by columns"
        )

    segments: list[bytes] = []
    chunk: list = []

    def _emit() -> None:
        """Append ``chunk`` as a segment."""
        if not chunk:
            return
        # Metadata lives on the FIRST segment only so the unpacker can
        # initialize the TableInfo record; continuation segments carry
        # just the column slice (recovered via the merge logic).
        if not segments:
            seg_entry = {
                "schema": list(chunk),
                "root_page_id": entry["root_page_id"],
                "next_page_id": entry["next_page_id"],
            }
        else:
            seg_entry = {"schema": list(chunk)}
        seg = json.dumps(
            {"tables": {name: seg_entry}}, separators=(",", ":")
        ).encode("utf-8")
        segments.append(seg)
        chunk.clear()

    for col in columns:
        trial = list(chunk) + [col]
        trial_seg = json.dumps(
            {"tables": {name: (
                {"schema": trial,
                 "root_page_id": entry["root_page_id"],
                 "next_page_id": entry["next_page_id"]}
                if not segments
                else {"schema": trial}
            )}},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(trial_seg) <= CHAIN_BODY_SIZE:
            chunk.append(col)
        else:
            _emit()
            chunk.append(col)

    _emit()
    return segments


def _pack_chain(catalog: "Catalog") -> list[bytes]:
    """Return the catalog as a list of PAGE_SIZE-sized chain pages.

    Page layout (each entry):

        bytes 0..4   : ``next_page_id`` (u32 big-endian). 0 = tail.
        bytes 4..16  : reserved (zeros).
        bytes 16..   : zero-padded JSON payload (exactly the segment bytes).

    The ``next_page_id`` field is set to 0 for every page; callers that
    allocate chain pages in sequence (e.g. :func:`Pager.write_catalog_chain`)
    are responsible for patching the head and intermediate pages' next_id
    after the chain is written.

    Raises :class:`CatalogCorrupt` if :func:`_serialize_segments` ever
    emits a segment larger than :data:`CHAIN_BODY_SIZE` — the previous
    silent truncation in this function masked a real data-loss bug and
    is intentionally removed. ``_serialize_segments`` is responsible for
    honouring the body budget via greedy pack + column-split.
    """
    pages: list[bytes] = []
    for seg in _serialize_segments(catalog):
        if len(seg) > CHAIN_BODY_SIZE:
            raise CatalogCorrupt(
                f"_serialize_segments produced {len(seg)}-byte segment "
                f"(limit {CHAIN_BODY_SIZE}); chain integrity broken"
            )
        body = seg
        payload = b"\x00\x00\x00\x00" + b"\x00" * (CHAIN_SEG_HEADER - 4) + body
        if len(payload) < PAGE_SIZE:
            payload += b"\x00" * (PAGE_SIZE - len(payload))
        pages.append(payload)
    return pages


def _unpack_chain(pager: "Pager") -> "Catalog":
    """Walk the catalog overflow chain starting at ``CHAIN_HEAD_PAGE`` and
    reconstruct a :class:`Catalog`.

    Segment-to-table merge semantics: when the same table name appears
    across multiple chain segments (column-split for very wide tables —
    see :func:`_split_single_table`), the ``schema`` lists are
    concatenated in chain-order so that all columns are preserved. The
    ``root_page_id`` / ``next_page_id`` of the FIRST segment carrying
    that table name is used (continuation segments do not repeat these
    fields, by design). Tables that appear in only one segment round
    trip unchanged.
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
                _merge_table_entry(tables, name, info)
        pid = next_id
    # Materialize TableInfo objects.
    for name, info in tables.items():
        cols = tuple(_load_column(c_) for c_ in info["schema"])
        cat.tables[name] = TableInfo(
            name=name,
            columns=cols,
            root_page_id=_dec_int(info["root_page_id"]),
            next_page_id=_dec_int(info["next_page_id"]),
        )
    return cat


def _merge_table_entry(tables: dict, name: str, info: dict) -> None:
    """Accumulate a table entry read from one chain segment.

    First sighting initializes the entry (with whatever fields are
    present). Subsequent sightings (continuation segments produced by
    :func:`_split_single_table`) append their columns to the existing
    schema list and update metadata fields if present.
    """
    if name not in tables:
        # First sighting: copy fields verbatim. Continuation segments
        # may omit metadata entirely (only carry schema), so tolerate
        # missing keys here.
        tables[name] = {
            "schema": list(info.get("schema", [])),
            "root_page_id": info.get("root_page_id"),
            "next_page_id": info.get("next_page_id"),
        }
        return
    existing = tables[name]
    # Append this segment's columns to the running schema list. Order
    # is preserved by iterating segments in chain order (head → tail).
    new_schema = info.get("schema", [])
    if new_schema:
        existing.setdefault("schema", []).extend(new_schema)
    # Continuation segments may carry refreshed metadata; if so,
    # prefer the latest (defensive — by construction the FIRST sighting
    # holds the metadata and continuations do not override it, but the
    # rule "first writes win for metadata, latest writes win for schema"
    # is robust to either ordering).
    if "root_page_id" in info and info["root_page_id"] is not None:
        existing["root_page_id"] = info["root_page_id"]
    if "next_page_id" in info and info["next_page_id"] is not None:
        existing["next_page_id"] = info["next_page_id"]


# Patch Catalog with a classmethod that walks the chain. Done here (after
# _unpack_chain is defined) rather than inside the class body to keep the
# class definition small and avoid forward-reference surprises.
