"""Tests for the slotted page layout (Task 9 + Task 10).

Spec: REQ-STORAGE-003
Header (16 bytes) + slot directory (6 bytes/slot) + data area growing from page end.
"""
import pytest

from tinydb.slotted_page import (
    SlottedPage,
    HEADER_SIZE,
    SLOT_SIZE,
    MAX_SLOTS,
    MAX_INLINE_PAYLOAD,
    NULL_PAGE_ID,
    TOMBSTONE_OFFSET,
    FLAG_TOMBSTONE,
)
from tinydb.errors import PageFull


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


# ---------------------------------------------------------------------------
# Task 10: insert/delete/update/get full semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-04")
def test_insert_into_full_page_raises_page_full():
    p = SlottedPage.empty(2)
    for i in range(MAX_SLOTS):
        p.insert(b"\xab" * 100)
    with pytest.raises(PageFull):
        p.insert(b"x")


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-05")
def test_update_in_place_same_length():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03\x04")
    p.update(sid, b"\xff\xee\xdd\xcc")
    assert p.get(sid) == b"\xff\xee\xdd\xcc"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-05")
def test_update_longer_raises():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02")
    with pytest.raises(Exception):  # PageFull or ValueError acceptable
        p.update(sid, b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a")


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-06")
def test_delete_marks_tombstone():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03")
    p.delete(sid)
    assert p.get(sid) is None
    raw = p.to_bytes()
    base = HEADER_SIZE + sid * SLOT_SIZE
    offset = int.from_bytes(raw[base:base + 2], "big")
    assert offset == TOMBSTONE_OFFSET


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-07")
def test_reuse_tombstoned_slot_on_insert():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03\x04")
    p.delete(sid)
    new_sid = p.insert(b"\xaa\xbb\xcc\xdd")
    assert new_sid == sid
    assert p.get(new_sid) == b"\xaa\xbb\xcc\xdd"


# ---------------------------------------------------------------------------
# Task 10 review follow-up: multi-row, out-of-range, payload constant
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-08")
def test_multi_row_insert_and_get():
    p = SlottedPage.empty(2)
    s0 = p.insert(b"row0")
    s1 = p.insert(b"row1")
    s2 = p.insert(b"row2")
    assert s0 == 0 and s1 == 1 and s2 == 2
    assert p.get(0) == b"row0"
    assert p.get(1) == b"row1"
    assert p.get(2) == b"row2"
    # roundtrip preserves all rows
    p2 = SlottedPage.from_bytes(2, p.to_bytes())
    assert p2.get(0) == b"row0"
    assert p2.get(1) == b"row1"
    assert p2.get(2) == b"row2"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-09")
def test_get_out_of_range_returns_none():
    p = SlottedPage.empty(2)
    assert p.get(0) is None  # empty page, no slots
    p.insert(b"x")
    assert p.get(1) is None  # out of range
    assert p.get(-1) is None  # negative


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-10")
def test_max_inline_payload_constant():
    """MAX_INLINE_PAYLOAD = 4096 - 16 (header) - 2 (data_len marker) = 4078."""
    assert MAX_INLINE_PAYLOAD == 4078
