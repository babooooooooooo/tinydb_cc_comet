"""Tests for the slotted page layout (Task 9).

Spec: REQ-STORAGE-003
Header (16 bytes) + slot directory (6 bytes/slot) + data area growing from page end.
"""
import pytest

from tinydb.slotted_page import SlottedPage, HEADER_SIZE, NULL_PAGE_ID


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-01")
def test_slotted_page_empty_roundtrip():
    """An empty SlottedPage roundtrips through to_bytes / from_bytes preserving metadata."""
    p = SlottedPage.empty(2)
    raw = p.to_bytes()
    assert len(raw) == 4096
    p2 = SlottedPage.from_bytes(2, raw)
    assert p2.page_id == 2
    assert p2.num_slots == 0
    assert p2.free_offset == HEADER_SIZE
    assert p2.overflow_next == NULL_PAGE_ID


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-02")
def test_insert_first_row_records_slot():
    """Inserting a row returns sid=0, persists a slot, and round-trips data intact."""
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03")
    assert sid == 0
    raw = p.to_bytes()
    p2 = SlottedPage.from_bytes(2, raw)
    assert p2.num_slots == 1
    assert p2.get(0) == b"\x01\x02\x03"
