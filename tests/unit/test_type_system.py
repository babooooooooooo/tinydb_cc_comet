import math
import struct as _st

import pytest
from tinydb.type_system import encode_int, decode_int
from tinydb.type_system import encode_text, decode_text
from tinydb.type_system import encode_bool, decode_bool
from tinydb.type_system import encode_float, decode_float
from tinydb.type_system import parse_int_literal, parse_float_literal, parse_text_literal, parse_bool_literal

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


@pytest.mark.spec_id("REQ-TYPE-001-SCN-10")
def test_text_encode_alice_length_prefixed():
    assert encode_text("alice") == b"\x00\x05alice"


@pytest.mark.spec_id("REQ-TYPE-001-SCN-11")
def test_text_encode_rejects_invalid_surrogate():
    with pytest.raises(UnicodeEncodeError):
        encode_text("\udcff")


@pytest.mark.spec_id("REQ-TYPE-001-SCN-15")
def test_text_decode_roundtrips_alice():
    val, off = decode_text(b"\x00\x05alice", 0)
    assert val == "alice" and off == 7


@pytest.mark.spec_id("REQ-TYPE-001-SCN-15")
def test_text_decode_utf8_multibyte():
    encoded = encode_text("你好")
    val, off = decode_text(encoded, 0)
    assert val == "你好"


@pytest.mark.spec_id("REQ-TYPE-001-SCN-17")
def test_text_decode_truncated_length_raises():
    with pytest.raises(ValueError):
        decode_text(b"\x00\x05abc", 0)  # length says 5, only 3 bytes follow


@pytest.mark.spec_id("REQ-TYPE-001-SCN-12")
def test_bool_encode_true_false_single_byte():
    assert encode_bool(True) == b"\x01"
    assert encode_bool(False) == b"\x00"


@pytest.mark.spec_id("REQ-TYPE-001-SCN-16")
def test_bool_decode_roundtrips():
    assert decode_bool(b"\x01", 0) == (True, 1)
    assert decode_bool(b"\x00", 0) == (False, 1)


@pytest.mark.spec_id("REQ-TYPE-001-SCN-13")
def test_float_encode_3_14_ieee754_be():
    assert encode_float(3.14) == _st.pack(">d", 3.14)


@pytest.mark.spec_id("REQ-TYPE-001-SCN-13")
def test_float_roundtrip_negative_zero():
    val, off = decode_float(encode_float(-0.0), 0)
    assert val == -0.0 and math.copysign(1.0, val) == -1.0


@pytest.mark.spec_id("REQ-TYPE-001-SCN-01")
def test_parse_int_literal_positive():
    assert parse_int_literal("42") == 42


@pytest.mark.spec_id("REQ-TYPE-001-SCN-02")
def test_parse_int_literal_negative():
    assert parse_int_literal("-7") == -7


@pytest.mark.spec_id("REQ-TYPE-001-SCN-03")
def test_parse_float_literal_decimal():
    assert parse_float_literal("3.14") == 3.14


@pytest.mark.spec_id("REQ-TYPE-001-SCN-04")
def test_parse_text_literal_strips_quotes():
    assert parse_text_literal("'hello world'") == "hello world"


@pytest.mark.spec_id("REQ-TYPE-001-SCN-05")
def test_parse_bool_literal_true():
    assert parse_bool_literal("TRUE") is True
    assert parse_bool_literal("true") is True


@pytest.mark.spec_id("REQ-TYPE-001-SCN-06")
def test_parse_bool_literal_false():
    assert parse_bool_literal("false") is False


@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_parse_float_literal_rejects_NaN():
    with pytest.raises(ValueError, match="NaN not allowed"):
        parse_float_literal("NaN")


@pytest.mark.spec_id("REQ-TYPE-001-SCN-08")
def test_parse_float_literal_rejects_Infinity():
    with pytest.raises(ValueError):
        parse_float_literal("Infinity")
    with pytest.raises(ValueError):
        parse_float_literal("inf")
