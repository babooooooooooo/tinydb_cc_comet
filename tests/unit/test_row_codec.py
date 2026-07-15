"""Tests for row codec (Task 11): encode_row / decode_row with null bitmap.

Spec: REQ-STORAGE-004
Layout: [null_bitmap (1+ bytes)] [encoded_value_0] [encoded_value_1] ...
Bitmap is LSB-first: column 0 -> bit 0 of byte 0, column 1 -> bit 1, ...
"""
import pytest

from tinydb.row_codec import encode_row, decode_row

SCHEMA = [("id", "INT"), ("name", "TEXT"), ("active", "BOOL")]
SCHEMA_FLOAT = [("a", "INT"), ("b", "FLOAT"), ("c", "BOOL")]


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
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-04")
def test_decode_row_roundtrip_all_populated():
    original = [7, "bob", True]
    decoded = decode_row(encode_row(original, SCHEMA), SCHEMA)
    assert decoded == original


# --- Boundary tests (Task 11 fix: I-2 from thorough review) ---


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-05")
def test_encode_row_null_first_column_bitmap():
    # column 0 NULL -> bit 0 of byte 0 -> 0b00000001 = 0x01
    row_bytes = encode_row([None, "alice", True], SCHEMA)
    assert row_bytes[0] == 0x01


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-06")
def test_encode_row_null_last_column_bitmap():
    # column 2 NULL -> bit 2 of byte 0 -> 0b00000100 = 0x04
    row_bytes = encode_row([42, "alice", None], SCHEMA)
    assert row_bytes[0] == 0x04


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-07")
def test_encode_row_all_nulls_3cols():
    # bits 0,1,2 all set -> 0b00000111 = 0x07; only bitmap, no value bytes
    row_bytes = encode_row([None, None, None], SCHEMA)
    assert len(row_bytes) == 1
    assert row_bytes[0] == 0x07


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-08")
def test_encode_row_9cols_bitmap_2_bytes():
    # 9 columns -> 2-byte bitmap; column 8 (index 8) NULL -> byte 1 bit 0 = 0x01
    schema9 = [(f"c{i}", "INT") for i in range(9)]
    values = [0, 1, 2, 3, 4, 5, 6, 7, None]
    row_bytes = encode_row(values, schema9)
    assert row_bytes[1] == 0x01
    # roundtrip preserves NULL at column 8 and values elsewhere
    assert decode_row(row_bytes, schema9) == values


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-09")
def test_encode_row_float_null():
    # FLOAT column NULL: bit 1 of byte 0 = 0x02
    row_bytes = encode_row([7, None, True], SCHEMA_FLOAT)
    assert row_bytes[0] == 0x02
    assert decode_row(row_bytes, SCHEMA_FLOAT) == [7, None, True]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-10")
def test_encode_row_text_with_nul_byte():
    # TEXT containing NUL byte must roundtrip (length-prefixed encoding)
    original = [1, "a\x00b", True]
    decoded = decode_row(encode_row(original, SCHEMA), SCHEMA)
    assert decoded == original


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-11")
def test_encode_row_length_mismatch_raises_value_error():
    # values count != schema columns
    with pytest.raises(ValueError, match="values count"):
        encode_row([1], [("a", "INT"), ("b", "TEXT")])


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-12")
def test_decode_row_truncated_buffer_raises_value_error():
    # buf shorter than bitmap (3-col schema needs 1 byte bitmap) -> explicit message
    with pytest.raises(ValueError, match="too short for bitmap"):
        decode_row(b"", SCHEMA)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-STORAGE-004-SCN-13")
def test_encode_row_col_count_zero():
    # degenerate case: 0 columns -> empty row, empty bitmap, empty values
    assert encode_row([], []) == b""
    assert decode_row(b"", []) == []