"""Unit tests for catalog overflow chain robustness.

Behavior under test:

* ``_serialize_segments`` must do real greedy splitting so a SINGLE wide
  table whose schema payload exceeds :data:`CHAIN_BODY_SIZE` is split into
  multiple segments without losing columns (Design Doc §T3 item 3).
* ``_pack_chain`` must NOT silently truncate oversize segments — it must
  raise :class:`CatalogCorrupt` instead (defense-in-depth).
* ``_unpack_chain`` must round-trip multi-segment catalogs; in particular,
  a single table whose columns were split across segments must re-emerge
  with all its columns preserved.

These tests live under ``tests/unit/catalog/`` (a new unit subdir for
catalog-specific tests). The pre-existing ``tests/integration/test_catalog_overflow.py``
covers many-tables overflow and remains untouched.
"""
import json

import pytest

from tinydb.catalog import (
    Catalog,
    Column,
    TableInfo,
    _pack_chain,
    _serialize_segments,
    _unpack_chain,
    CHAIN_BODY_SIZE,
    CHAIN_THRESHOLD,
)
from tinydb.pager import Pager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wide_table(num_cols: int, name: str = "wide") -> TableInfo:
    """Build a TableInfo with ``num_cols`` synthetic INT columns."""
    cols = tuple(Column(name=f"col_{i:04d}", type="INT") for i in range(num_cols))
    return TableInfo(
        name=name,
        columns=cols,
        root_page_id=100,
        next_page_id=200,
    )


def _write_chain_to_pager(pager: Pager, raw_pages: list[bytes]) -> None:
    """Write ``_pack_chain`` output to ``pager`` and patch next_page_id.

    Mirrors the integration test pattern from
    ``tests/integration/test_catalog_overflow.py``: head goes to page 1,
    subsequent segments get freshly allocated pages, and intermediate
    next_page_id headers are patched via ``_write_chain_next``.
    """
    pager.write_page(1, raw_pages[0])
    page_ids = [1]
    for page in raw_pages[1:]:
        pid = pager.alloc_page()
        pager.write_page(pid, page)
        page_ids.append(pid)
    for i, pid in enumerate(page_ids[:-1]):
        pager._write_chain_next(pid, page_ids[i + 1])


# ---------------------------------------------------------------------------
# T3 Test Scope §3.3 — three required behaviors
# ---------------------------------------------------------------------------


def test_single_table_overflow_chain_no_loss(tmp_path):
    """Single table with 250 columns (payload > CHAIN_BODY_SIZE) must:

    * cause ``_pack_chain`` to emit MULTIPLE page-sized chain segments, AND
    * round-trip via ``_unpack_chain`` without losing any column.

    This regression-tests the existing silent-truncation bug: today
    ``_pack_chain`` produces ONE page whose body is truncated to
    CHAIN_BODY_SIZE, losing every column past the truncation boundary.
    """
    cat = Catalog()
    cat.tables["wide"] = _make_wide_table(250)

    # Confirm the per-table payload alone exceeds CHAIN_BODY_SIZE so the
    # column-split code path is actually exercised.
    full_payload = json.dumps(
        {"tables": {"wide": cat.tables["wide"].to_dict() if False else None}},
        separators=(",", ":"),
    )
    # JSON shape sanity: count columns as the upper bound on payload.
    assert len(cat.tables["wide"].columns) == 250
    # Roughly: each col encoding costs ~50 bytes; 250 cols × 50 ≈ 12.5 KiB,
    # well above CHAIN_BODY_SIZE (4080 bytes).
    rough_single_table_payload_size = 250 * 50
    assert rough_single_table_payload_size > CHAIN_BODY_SIZE, (
        "test assumption broken: a 250-col wide table should exceed "
        "CHAIN_BODY_SIZE; reduce column count or shorten column names."
    )

    p = Pager(str(tmp_path / "wide.db"))
    try:
        pages = _pack_chain(cat)
        # At least 2 segments: ⌈payload / CHAIN_BODY_SIZE⌉ — for 250 cols
        # on a single table, expect ~3+ pages.
        assert len(pages) >= 2, (
            f"_pack_chain must produce >=2 segments for a wide single "
            f"table; got {len(pages)} page(s)."
        )
        # Each page payload must fit within PAGE_SIZE (defensive invariant).
        from tinydb.pager import PAGE_SIZE
        for page in pages:
            assert len(page) == PAGE_SIZE
        # Every seg must be <= CHAIN_BODY_SIZE — no defensive truncation
        # silently hiding an oversize segment.
        segs = _serialize_segments(cat)
        for seg in segs:
            assert len(seg) <= CHAIN_BODY_SIZE, (
                f"segment {len(seg)} bytes exceeds CHAIN_BODY_SIZE "
                f"({CHAIN_BODY_SIZE}); _serialize_segments must split"
            )

        # Persist the chain to the pager and round-trip.
        _write_chain_to_pager(p, pages)
        cat2 = _unpack_chain(p)
        assert "wide" in cat2.tables, "round-trip lost the wide table entirely"
        wide2 = cat2.tables["wide"]
        assert len(wide2.columns) == 250, (
            f"round-trip lost columns: expected 250, got {len(wide2.columns)}"
        )
        # Spot-check first / last column to confirm order is preserved.
        assert wide2.columns[0].name == "col_0000"
        assert wide2.columns[0].type == "INT"
        assert wide2.columns[249].name == "col_0249"
        assert wide2.columns[249].type == "INT"
        # Metadata preserved (root/next_page_id land on the first segment).
        assert wide2.root_page_id == 100
        assert wide2.next_page_id == 200
    finally:
        p.close()


def test_pack_chain_raises_on_oversize_segment(monkeypatch):
    """``_pack_chain`` must raise :class:`CatalogCorrupt` when fed an
    oversize segment (defense-in-depth). This guards against a future
    regression where ``_serialize_segments`` is loosened and the silent
    truncate is relied upon.
    """
    from tinydb import catalog as catalog_mod
    from tinydb.catalog import CatalogCorrupt  # raised by _pack_chain

    cat = Catalog()
    cat.tables["t"] = _make_wide_table(10)

    # Inject a malformed serializer that produces an oversize segment.
    def malicious(_catalog: Catalog) -> list[bytes]:
        return [b"x" * (CHAIN_BODY_SIZE + 1)]

    monkeypatch.setattr(catalog_mod, "_serialize_segments", malicious)
    with pytest.raises(CatalogCorrupt):
        _pack_chain(cat)


def test_round_trip_with_mixed_wide_and_narrow(tmp_path):
    """Mixed catalog (1 wide table + 20 narrow tables) must round-trip
    every table and every column through the chain.

    This validates that the greedy split still packs narrow tables
    efficiently while the wide table is split by columns, and that
    intermediate metadata (root_page_id / next_page_id) survives the
    round-trip for every entry.
    """
    cat = Catalog()
    cat.tables["wide"] = _make_wide_table(200, name="wide")
    # Mix 20 narrow tables in BEFORE the wide one (Dict preserves
    # insertion order in CPython 3.7+).
    for i in range(20):
        cat.tables[f"narrow_{i:02d}"] = TableInfo(
            name=f"narrow_{i:02d}",
            columns=(Column(name="x", type="INT"),),
            root_page_id=300 + i,
            next_page_id=400 + i,
        )

    p = Pager(str(tmp_path / "mixed.db"))
    try:
        pages = _pack_chain(cat)
        # All segments must respect CHAIN_BODY_SIZE.
        segs = _serialize_segments(cat)
        for seg in segs:
            assert len(seg) <= CHAIN_BODY_SIZE

        _write_chain_to_pager(p, pages)
        cat2 = _unpack_chain(p)

        # All 21 tables present.
        assert len(cat2.tables) == 21, (
            f"round-trip lost tables: expected 21, got {len(cat2.tables)}"
        )
        # Wide table still has all 200 columns.
        assert len(cat2.tables["wide"].columns) == 200
        # Wide table column order preserved.
        assert cat2.tables["wide"].columns[0].name == "col_0000"
        assert cat2.tables["wide"].columns[199].name == "col_0199"
        # Wide table metadata preserved.
        assert cat2.tables["wide"].root_page_id == 100
        assert cat2.tables["wide"].next_page_id == 200
        # All 20 narrow tables present with correct metadata.
        for i in range(20):
            name = f"narrow_{i:02d}"
            assert name in cat2.tables, f"missing narrow table {name!r}"
            nt = cat2.tables[name]
            assert len(nt.columns) == 1
            assert nt.columns[0].name == "x"
            assert nt.root_page_id == 300 + i
            assert nt.next_page_id == 400 + i
    finally:
        p.close()


# ---------------------------------------------------------------------------
# Additional assertions anchoring the contract (Design Doc §T3 Acceptance)
# ---------------------------------------------------------------------------


def test_serialize_segments_guarantees_each_segment_fits():
    """Hard invariant: every segment emitted by ``_serialize_segments``
    fits within :data:`CHAIN_BODY_SIZE`. This is the precondition that
    lets ``_pack_chain`` drop its silent truncate.
    """
    cat = Catalog()
    # Heavy mix: a wide table + a handful of narrow tables — ensure the
    # algorithm handles heterogeneous catalogs.
    cat.tables["wide"] = _make_wide_table(180)
    for i in range(5):
        cat.tables[f"n_{i}"] = TableInfo(
            name=f"n_{i}",
            columns=(Column(name="x", type="INT"),),
            root_page_id=10 + i,
            next_page_id=20 + i,
        )

    segs = _serialize_segments(cat)
    assert len(segs) >= 1
    for seg in segs:
        assert len(seg) <= CHAIN_BODY_SIZE, (
            f"_serialize_segments produced {len(seg)}-byte segment "
            f"> CHAIN_BODY_SIZE ({CHAIN_BODY_SIZE})"
        )


def test_serialize_segments_wide_table_produces_multiple_segments():
    """A single 250-column table must produce >= 2 segments (it's the
    regression we're guarding against). Without the column-split fix
    this would return a single ~25 KiB segment that overflows the
    chain-page body budget.
    """
    cat = Catalog()
    cat.tables["wide"] = _make_wide_table(250)
    segs = _serialize_segments(cat)
    assert len(segs) >= 2, (
        f"wide single table must split into >=2 segments; got {len(segs)}"
    )
    for seg in segs:
        assert len(seg) <= CHAIN_BODY_SIZE
