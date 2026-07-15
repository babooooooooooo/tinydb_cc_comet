import pytest
from tinydb.type_system import encode_int, decode_int

@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_int_encode_42_big_endian():
    assert encode_int(42) == b"\x00\x00\x00\x00\x00\x00\x00\x2a"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-08")
def test_int_encode_overflow_2_63_raises():
    with pytest.raises(OverflowError):
        encode_int(2**63)

@pytest.mark.spec_id("REQ-TYPE-001-SCN-14")
def test_int_decode_roundtrips_42():
    val, off = decode_int(b"\x00\x00\x00\x00\x00\x00\x00\x2a", 0)
    assert val == 42
    assert off == 8

@pytest.mark.spec_id("REQ-TYPE-001-SCN-17")
def test_int_decode_truncated_buffer_raises():
    with pytest.raises(ValueError):
        decode_int(b"\x00\x00\x00", 0)

@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_int_roundtrip_negative():
    val, off = decode_int(encode_int(-1), 0)
    assert val == -1 and off == 8
