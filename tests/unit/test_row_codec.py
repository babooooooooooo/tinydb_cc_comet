"""Tests for row codec (Task 11): encode_row / decode_row with null bitmap.

Spec: REQ-STORAGE-004
Layout: [null_bitmap (1+ bytes)] [encoded_value_0] [encoded_value_1] ...
Bitmap is LSB-first: column 0 -> bit 0 of byte 0, column 1 -> bit 1, ...
"""
import pytest

from tinydb.row_codec import encode_row, decode_row

SCHEMA = [("id", "INT"), ("name", "TEXT"), ("active", "BOOL")]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-01")
def test_encode_row_no_nulls_bitmap_zero():
    row_bytes = encode_row([42, "alice", True], SCHEMA)
    assert row_bytes[0] == 0x00  # no nulls


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-02")
def test_encode_row_null_in_second_column_bitmap():
    row_bytes = encode_row([42, None, False], SCHEMA)
    # bit 1 (0-indexed) set means name is NULL -> 0b00000010 = 0x02
    assert row_bytes[0] == 0x02


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-03")
def test_decode_row_roundtrip_with_null():
    original = [42, None, False]
    decoded = decode_row(encode_row(original, SCHEMA), SCHEMA)
    assert decoded == original


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-03")
def test_decode_row_roundtrip_all_populated():
    original = [7, "bob", True]
    decoded = decode_row(encode_row(original, SCHEMA), SCHEMA)
    assert decoded == original
