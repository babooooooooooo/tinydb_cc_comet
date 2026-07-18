"""Tests for TypeCodec Protocol implementations (Task 2 / Plan 1.3).

These tests exercise the new codec-based dispatch API (lookup, codec_for).
The legacy module-level encode_*/decode_* helpers are preserved for
backward compatibility per Design Doc §F2 and are NOT exercised here.
"""
import pytest

from tinydb.type_system import lookup, codec_for


def test_int_codec_roundtrip():
    codec = lookup("INT")
    for v in [0, 1, -1, 2**31 - 1, -(2**31)]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_int_codec_overflow_raises():
    codec = lookup("INT")
    with pytest.raises(OverflowError):
        codec.encode_py(2**31)
    with pytest.raises(OverflowError):
        codec.encode_py(-(2**31) - 1)


def test_text_codec_roundtrip():
    codec = lookup("TEXT")
    for v in ["", "hello", "中文", "with 'apostrophe'"]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_bool_codec_roundtrip():
    codec = lookup("BOOL")
    for v in [True, False]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_float_codec_4byte_single_precision():
    """FLOAT uses 4-byte single precision (per design D3)."""
    codec = lookup("FLOAT")
    encoded = codec.encode_py(1.5)
    assert len(encoded) == 4  # single precision
    assert codec.decode_bytes(encoded, 0)[0] == 1.5


def test_float_codec_rejects_inf():
    codec = lookup("FLOAT")
    with pytest.raises(ValueError, match="inf/NaN not allowed"):
        codec.encode_py(float("inf"))


def test_float_codec_rejects_nan():
    codec = lookup("FLOAT")
    with pytest.raises(ValueError, match="inf/NaN not allowed"):
        codec.encode_py(float("nan"))


def test_bool_alias_lookup():
    """BOOLEAN should resolve to the BOOL codec (aliases populated via lookup)."""
    codec = lookup("BOOLEAN")
    assert codec is lookup("BOOL")


def test_real_alias_lookup():
    """REAL should resolve to the FLOAT codec."""
    codec = lookup("REAL")
    assert codec is lookup("FLOAT")


def test_codec_for_returns_singleton_for_mvp_types():
    """MVP types have no params; codec_for should return the registry singleton."""
    for name in ("INT", "TEXT", "BOOL", "FLOAT"):
        assert codec_for(name) is lookup(name)


def test_parse_int_literal_codec_matches_legacy():
    codec = lookup("INT")
    assert codec.parse_literal("42", ()) == 42
    assert codec.parse_literal("-7", ()) == -7


def test_parse_text_literal_codec_strips_quotes():
    codec = lookup("TEXT")
    assert codec.parse_literal("'hello world'", ()) == "hello world"


def test_parse_bool_literal_codec_case_insensitive():
    codec = lookup("BOOL")
    assert codec.parse_literal("TRUE", ()) is True
    assert codec.parse_literal("true", ()) is True
    assert codec.parse_literal("FALSE", ()) is False


def test_parse_float_literal_codec_rejects_nan():
    codec = lookup("FLOAT")
    with pytest.raises(ValueError, match="inf/NaN not allowed"):
        codec.parse_literal("NaN", ())


def test_validate_int_rejects_bool_subclass():
    """bool is a subclass of int — Python's isinstance(True, int) is True.
    The INT codec's validate() must reject bool to keep semantics strict.
    """
    codec = lookup("INT")
    with pytest.raises(TypeError):
        codec.validate(True)


def test_validate_int_rejects_out_of_range():
    codec = lookup("INT")
    with pytest.raises(OverflowError):
        codec.validate(2**31)


def test_validate_text_accepts_str():
    codec = lookup("TEXT")
    # Should not raise
    codec.validate("hello")


def test_validate_text_rejects_int():
    codec = lookup("TEXT")
    with pytest.raises(TypeError):
        codec.validate(123)


def test_validate_float_rejects_inf():
    codec = lookup("FLOAT")
    with pytest.raises(ValueError, match="inf/NaN not allowed"):
        codec.validate(float("inf"))


# ---------------------------------------------------------------------------
# Task 3: SMALLINT (IntCodec with width=2).
# ---------------------------------------------------------------------------


def test_smallint_codec_roundtrip():
    codec = codec_for("SMALLINT")
    for v in [-32768, -1, 0, 1, 32767]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_smallint_codec_2byte_size():
    codec = codec_for("SMALLINT")
    assert len(codec.encode_py(0)) == 2


def test_smallint_codec_overflow_raises():
    codec = codec_for("SMALLINT")
    with pytest.raises(OverflowError, match="SMALLINT out of range"):
        codec.encode_py(32768)
    with pytest.raises(OverflowError, match="SMALLINT out of range"):
        codec.encode_py(-32769)


# ---------------------------------------------------------------------------
# Task 4: BIGINT (IntCodec with width=8) + INTEGER alias.
# ---------------------------------------------------------------------------


def test_bigint_codec_roundtrip():
    codec = codec_for("BIGINT")
    for v in [-(2**63), -1, 0, 1, 2**63 - 1]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_bigint_codec_8byte_size():
    codec = codec_for("BIGINT")
    assert len(codec.encode_py(0)) == 8


def test_bigint_codec_overflow_raises():
    codec = codec_for("BIGINT")
    with pytest.raises(OverflowError, match="BIGINT out of range"):
        codec.encode_py(2**63)
    with pytest.raises(OverflowError, match="BIGINT out of range"):
        codec.encode_py(-(2**63) - 1)


def test_int_alias_integer():
    """INTEGER alias resolves to INT (width=4)."""
    codec = lookup("INTEGER")
    assert codec.name == "INT"
    assert codec.width == 4


# ---------------------------------------------------------------------------
# Task 5: DOUBLE (FloatCodec with width=8) + DOUBLE PRECISION alias.
# ---------------------------------------------------------------------------


def test_double_codec_8byte():
    codec = codec_for("DOUBLE")
    assert len(codec.encode_py(1.5)) == 8


def test_double_codec_roundtrip():
    codec = codec_for("DOUBLE")
    # high-precision value that requires double precision
    v = 3.14159265358979
    assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_double_codec_rejects_inf_nan():
    codec = codec_for("DOUBLE")
    with pytest.raises(ValueError, match="DOUBLE inf/NaN not allowed"):
        codec.encode_py(float("inf"))
    with pytest.raises(ValueError, match="DOUBLE inf/NaN not allowed"):
        codec.encode_py(float("nan"))


def test_double_precision_alias():
    codec = lookup("DOUBLE PRECISION")
    assert codec.name == "DOUBLE"
    assert codec.width == 8


def test_real_alias_resolves_to_float_4byte():
    """REAL alias = FLOAT (4-byte single per design D3)."""
    codec = lookup("REAL")
    assert codec.name == "FLOAT"
    assert codec.width == 4


def test_boolean_alias_resolves_to_bool():
    codec = lookup("BOOLEAN")
    assert codec.name == "BOOL"
    # verify it encodes/decodes correctly
    assert codec.decode_bytes(codec.encode_py(True), 0)[0] is True
    assert codec.decode_bytes(codec.encode_py(False), 0)[0] is False
