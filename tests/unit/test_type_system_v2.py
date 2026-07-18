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
