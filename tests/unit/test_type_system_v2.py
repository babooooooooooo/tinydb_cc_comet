"""Tests for TypeCodec Protocol implementations (Task 2 / Plan 1.3).

These tests exercise the new codec-based dispatch API (lookup, codec_for).
The legacy module-level encode_*/decode_* helpers were removed in
type-codec-and-catalog-cleanup (H6). This file now also exercises the
type-mismatch contract that encode_py must enforce via CodecError.
"""
import datetime

import pytest

from tinydb.type_system import lookup, codec_for, CodecError


def test_int_codec_roundtrip():
    codec = lookup("INT")
    for v in [0, 1, -1, 2**31 - 1, -(2**31)]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_int_codec_overflow_raises():
    codec = lookup("INT")
    with pytest.raises(CodecError):
        codec.encode_py(2**31)
    with pytest.raises(CodecError):
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
    with pytest.raises(CodecError, match="SMALLINT out of range"):
        codec.encode_py(32768)
    with pytest.raises(CodecError, match="SMALLINT out of range"):
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
    with pytest.raises(CodecError, match="BIGINT out of range"):
        codec.encode_py(2**63)
    with pytest.raises(CodecError, match="BIGINT out of range"):
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


# ---------------------------------------------------------------------------
# Task 7: VARCHAR (parametric codec with max_len).
# ---------------------------------------------------------------------------


def test_varchar_codec_roundtrip_within_max():
    codec = codec_for("VARCHAR", (10,))
    for v in ["", "hello", "中文"]:
        encoded = codec.encode_py(v)
        assert codec.decode_bytes(encoded, 0)[0] == v


def test_varchar_codec_rejects_overlong():
    codec = codec_for("VARCHAR", (10,))
    with pytest.raises(CodecError, match="VARCHAR\\(10\\) length 11 exceeds max"):
        codec.encode_py("a" * 11)


def test_varchar_codec_accepts_exact_max():
    codec = codec_for("VARCHAR", (10,))
    encoded = codec.encode_py("a" * 10)
    assert len(encoded) == 2 + 10  # length prefix + UTF-8


def test_varchar_codec_per_call_instance():
    """Different max_len should produce independent codec instances."""
    a = codec_for("VARCHAR", (10,))
    b = codec_for("VARCHAR", (20,))
    assert a is not b


# ---------------------------------------------------------------------------
# Task 8: CHAR (parametric codec with PAD SPACE).
# ---------------------------------------------------------------------------


def test_char_codec_pads_short_string():
    codec = codec_for("CHAR", (5,))
    encoded = codec.encode_py("ab")
    assert len(encoded) == 2 + 5  # length prefix + 5 bytes (padded)
    assert codec.decode_bytes(encoded, 0)[0] == "ab   "  # spaces preserved


def test_char_codec_rejects_overlong():
    codec = codec_for("CHAR", (5,))
    with pytest.raises(CodecError, match="CHAR\\(5\\) length 6 exceeds max"):
        codec.encode_py("abcdef")


def test_char_codec_accepts_exact_length():
    codec = codec_for("CHAR", (5,))
    encoded = codec.encode_py("abcde")
    assert codec.decode_bytes(encoded, 0)[0] == "abcde"


def test_char_codec_no_trim_on_decode():
    """SQL92 PAD SPACE: padding is preserved on read (no RTRIM)."""
    codec = codec_for("CHAR", (5,))
    encoded = codec.encode_py("ab")
    assert codec.decode_bytes(encoded, 0)[0] == "ab   "
    # NOT "ab"


# ---------------------------------------------------------------------------
# Task 9: DECIMAL (scaled int64 with precision/scale).
# ---------------------------------------------------------------------------


def test_decimal_codec_roundtrip_simple():
    codec = codec_for("DECIMAL", (10, 2))
    encoded = codec.encode_py(1.23)
    assert len(encoded) == 8
    assert codec.decode_bytes(encoded, 0)[0] == 1.23


def test_decimal_codec_negative_roundtrip():
    codec = codec_for("DECIMAL", (10, 2))
    encoded = codec.encode_py(-123.45)
    assert codec.decode_bytes(encoded, 0)[0] == -123.45


def test_decimal_codec_zero_scale():
    codec = codec_for("DECIMAL", (10, 0))
    encoded = codec.encode_py(123)
    assert codec.decode_bytes(encoded, 0)[0] == 123


def test_decimal_codec_precision_overflow():
    codec = codec_for("DECIMAL", (5, 2))
    # DECIMAL(5,2): value range is [-999.99, 999.99]
    with pytest.raises(CodecError, match=r"DECIMAL\(5,2\) value .* out of range"):
        codec.encode_py(1000.00)
    with pytest.raises(CodecError, match=r"DECIMAL\(5,2\) value .* out of range"):
        codec.encode_py(-1000.00)


def test_decimal_codec_scaled_overflow():
    codec = codec_for("DECIMAL", (18, 6))
    # DECIMAL(18,6): value range is [-10^12, 10^12 - 10^-6]
    with pytest.raises(CodecError):
        codec.encode_py(1e13)  # exceeds 10^12


def test_decimal_codec_rejects_p_lt_s():
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (2, 5))


def test_decimal_codec_rejects_p_too_large():
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (19, 0))


# ---------------------------------------------------------------------------
# Task 10: DATE / TIME / TIMESTAMP (UTC unified).
# ---------------------------------------------------------------------------


def test_date_codec_roundtrip():
    codec = lookup("DATE")
    d = datetime.date(2026, 7, 16)
    encoded = codec.encode_py(d)
    assert len(encoded) == 4  # 4-byte days since epoch
    decoded, _ = codec.decode_bytes(encoded, 0)
    assert decoded == d


def test_date_codec_parse_iso_literal():
    codec = lookup("DATE")
    parsed = codec.parse_literal("2026-07-16", ())
    assert parsed == datetime.date(2026, 7, 16)


def test_date_codec_rejects_bad_format():
    codec = lookup("DATE")
    with pytest.raises(ValueError):
        codec.parse_literal("2026/07/16", ())
    with pytest.raises(ValueError):
        codec.parse_literal("not-a-date", ())


def test_time_codec_roundtrip():
    codec = lookup("TIME")
    t = datetime.time(14, 30, 0)
    encoded = codec.encode_py(t)
    assert len(encoded) == 4
    decoded, _ = codec.decode_bytes(encoded, 0)
    assert decoded == t


def test_time_codec_parse_iso_literal():
    codec = lookup("TIME")
    parsed = codec.parse_literal("14:30:00", ())
    assert parsed == datetime.time(14, 30, 0)


def test_time_codec_rejects_out_of_range():
    codec = lookup("TIME")
    with pytest.raises(ValueError):
        codec.encode_py(datetime.time(25, 0, 0))


def test_timestamp_codec_roundtrip():
    codec = lookup("TIMESTAMP")
    ts = datetime.datetime(2026, 7, 16, 14, 30, 0)
    encoded = codec.encode_py(ts)
    assert len(encoded) == 8
    decoded, _ = codec.decode_bytes(encoded, 0)
    assert decoded == ts


def test_timestamp_codec_parse_iso_literal():
    codec = lookup("TIMESTAMP")
    parsed = codec.parse_literal("2026-07-16 14:30:00", ())
    assert parsed == datetime.datetime(2026, 7, 16, 14, 30, 0)


# --- Codec encode_py type-safety contract (type-codec-and-catalog-cleanup H6) ---


def test_int_codec_encode_py_rejects_float_with_codec_error():
    """_IntCodec.encode_py MUST raise CodecError (not struct.error) when given a float.

    Spec scenario: 'Convert Python float to INT rejected via codec registry'.
    Without isinstance check, struct.pack raises struct.error which is NOT a CodecError.
    """
    codec = lookup("INT")
    with pytest.raises(CodecError, match="expected int for INT"):
        codec.encode_py(2.5)


def test_float_codec_encode_py_nan_raises_codec_error():
    """_FloatCodec.encode_py MUST raise CodecError (not plain ValueError) for NaN.

    Spec scenario: 'Convert Python float NaN rejected via codec registry' asserts
    the raised exception IS CodecError (not just ValueError). CodecError IS-A
    ValueError but isinstance(e, CodecError) must be True.
    """
    codec = lookup("FLOAT")
    with pytest.raises(CodecError, match="NaN not allowed"):
        codec.encode_py(float("nan"))


def test_float_codec_encode_py_inf_raises_codec_error():
    """Same contract for +Inf: must be CodecError, not plain ValueError."""
    codec = lookup("FLOAT")
    with pytest.raises(CodecError, match="inf/NaN not allowed"):
        codec.encode_py(float("inf"))


def test_legacy_validate_compare_removed_from_type_system():
    """The legacy module-level validate_compare function must no longer be importable.

    Spec should cover this removal alongside py_to_db / db_to_py.
    """
    with pytest.raises(ImportError):
        from tinydb.type_system import validate_compare  # noqa: F401


def test_int_codec_encode_py_overflow_raises_codec_error():
    """After F3+F6 refactor, encode_py should raise CodecError (not OverflowError)
    for out-of-range ints, matching _IntCodec.validate's contract.
    """
    codec = lookup("INT")
    with pytest.raises(CodecError, match="INT out of range"):
        codec.encode_py(2**31)


def test_varchar_codec_overflow_raises_codec_error():
    """After F2 fix, length-exceeded should raise CodecError (not TypeError)."""
    codec = codec_for("VARCHAR", (10,))
    with pytest.raises(CodecError, match="length 11 exceeds max"):
        codec.encode_py("x" * 11)


def test_char_codec_overflow_raises_codec_error():
    """After F2 fix, CHAR overlong should raise CodecError (not TypeError)."""
    codec = codec_for("CHAR", (5,))
    with pytest.raises(CodecError, match="length 6 exceeds max"):
        codec.encode_py("x" * 6)
