"""Integration tests for catalog multi-page overflow chain.

When a Catalog's JSON serialization exceeds a single 4KB page, the catalog
is split across a linked chain of pages (head = page 1, subsequent pages
allocated via ``Pager.alloc_page``). Each chain page carries a 4-byte
``next_page_id`` header at offset 0; the last page's next_id is 0.
"""
import pytest

from tinydb.catalog import Catalog, Column, _pack_chain, _unpack_chain
from tinydb.pager import PAGE_SIZE


@pytest.mark.integration
@pytest.mark.spec_id("REQ-CATALOG-OVERFLOW-01")
def test_overflow_chain_roundtrip(tmp_path):
    """60-table catalog overflows the head page; roundtrip recovers all 60 tables."""
    from tinydb.pager import Pager

    p = Pager(str(tmp_path / "ovf.db"))
    # Create ~60 tables — should overflow page 1.
    cat = Catalog()
    for i in range(60):
        cat.create_table(
            f"t{i}",
            (Column(name="id", type="INT"), Column(name="name", type="TEXT")),
            root_page_id=10 + i,
            next_page_id=11 + i,
        )
    raw = _pack_chain(cat)
    assert len(raw) > 1, "catalog must spill into multiple pages"
    # Each page payload must be exactly PAGE_SIZE bytes.
    for page in raw:
        assert len(page) == PAGE_SIZE
    # Write the chain starting at page 1 (head), then allocate overflow pages.
    p.write_page(1, raw[0])
    page_ids = [1]
    for page in raw[1:]:
        pid = p.alloc_page()
        p.write_page(pid, page)
        page_ids.append(pid)
    # Patch next_id placeholders (set to 0 by _pack_chain) on non-tail pages.
    for i, pid in enumerate(page_ids[:-1]):
        p._write_chain_next(pid, page_ids[i + 1])
    # Re-open and verify
    cat2 = _unpack_chain(p)
    assert len(cat2.tables) == 60
    # Spot-check a few tables
    for i in (0, 29, 59):
        ti = cat2.get_table(f"t{i}")
        assert ti is not None
        assert ti.root_page_id == 10 + i
        assert ti.next_page_id == 11 + i
        assert ti.columns[0].name == "id"
        assert ti.columns[0].type == "INT"
        assert ti.columns[1].name == "name"
        assert ti.columns[1].type == "TEXT"
    p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-CATALOG-OVERFLOW-02")
def test_single_page_catalog_no_overflow(tmp_path):
    """A small catalog fits on page 1; chain is exactly one page."""
    from tinydb.pager import Pager

    p = Pager(str(tmp_path / "single.db"))
    cat = Catalog()
    cat.create_table(
        "only",
        (Column(name="id", type="INT"),),
        root_page_id=2,
        next_page_id=3,
    )
    raw = _pack_chain(cat)
    assert len(raw) == 1, "small catalog must not overflow"
    p.write_page(1, raw[0])
    cat2 = _unpack_chain(p)
    assert len(cat2.tables) == 1
    assert cat2.get_table("only").root_page_id == 2
    p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-CATALOG-OVERFLOW-03")
def test_write_catalog_chain_persists_across_reopen(tmp_path):
    """``Pager.write_catalog_chain`` persists a multi-page catalog that survives reopen."""
    from tinydb.pager import Pager

    path = str(tmp_path / "persist.db")
    p = Pager(path)
    cat = Catalog()
    for i in range(60):
        cat.create_table(
            f"t{i}",
            (Column(name="id", type="INT"),),
            root_page_id=10 + i,
            next_page_id=11 + i,
        )
    p.write_catalog_chain(cat)
    p.close()

    # Reopen and verify
    p2 = Pager(path)
    cat2 = Catalog.load_from_pager(p2)
    assert len(cat2.tables) == 60
    for i in (0, 29, 59):
        assert cat2.get_table(f"t{i}").root_page_id == 10 + i
    p2.close()