"""Unit tests for validate_compare_types and infer_literal_type (Task 18).

Design D6: WHERE clause comparisons require column type and literal type to
match exactly (both type name and type_params). The
``validate_compare_types(col_type, col_params, lit_type, lit_params)`` is the
strict-same-type check used by ``eval_expr`` in executor.py.
"""
import datetime

import pytest

from tinydb.type_system import (
    codec_for,
    infer_literal_type,
    validate_compare_types,
)


# --- direct validate_compare_types ----------------------------------------


def test_validate_compare_types_same_int_ok():
    """INT col vs INT lit (no params) — same, no error."""
    validate_compare_types("INT", (), "INT", ())


def test_validate_compare_types_int_vs_smallint_raises():
    """INT col vs SMALLINT lit — different widths, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("INT", (), "SMALLINT", ())


def test_validate_compare_types_int_vs_bigint_raises():
    """INT col vs BIGINT lit — different widths, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("INT", (), "BIGINT", ())


def test_validate_compare_types_int_vs_text_raises():
    """INT col vs TEXT lit — different types, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("INT", (), "TEXT", ())


def test_validate_compare_types_varchar_same_params_ok():
    """VARCHAR(10) col vs VARCHAR(10) lit — same type and params, OK."""
    validate_compare_types("VARCHAR", (10,), "VARCHAR", (10,))


def test_validate_compare_types_varchar_diff_params_raises():
    """VARCHAR(10) col vs VARCHAR(20) lit — different params, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("VARCHAR", (10,), "VARCHAR", (20,))


def test_validate_compare_types_varchar_vs_text_raises():
    """VARCHAR(10) col vs TEXT lit — different types, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("VARCHAR", (10,), "TEXT", ())


def test_validate_compare_types_decimal_same_ok():
    """DECIMAL(10, 2) col vs DECIMAL(10, 2) lit — same, OK."""
    validate_compare_types("DECIMAL", (10, 2), "DECIMAL", (10, 2))


def test_validate_compare_types_decimal_diff_precision_raises():
    """DECIMAL(10, 2) col vs DECIMAL(5, 2) lit — different precision, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("DECIMAL", (10, 2), "DECIMAL", (5, 2))


def test_validate_compare_types_decimal_diff_scale_raises():
    """DECIMAL(10, 2) col vs DECIMAL(10, 4) lit — different scale, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("DECIMAL", (10, 2), "DECIMAL", (10, 4))


def test_validate_compare_types_date_vs_timestamp_raises():
    """DATE col vs TIMESTAMP lit — different types, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("DATE", (), "TIMESTAMP", ())


def test_validate_compare_types_float_vs_double_raises():
    """FLOAT col vs DOUBLE lit — different widths, must raise."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("FLOAT", (), "DOUBLE", ())


def test_validate_compare_types_char_vs_text_raises():
    """CHAR(5) col vs TEXT lit — CHAR and TEXT are different types."""
    with pytest.raises(TypeError, match="type mismatch"):
        validate_compare_types("CHAR", (5,), "TEXT", ())


# --- infer_literal_type ----------------------------------------------------


def test_infer_literal_int_returns_int():
    assert infer_literal_type(1) == ("INT", ())
    assert infer_literal_type(0) == ("INT", ())
    assert infer_literal_type(-100) == ("INT", ())


def test_infer_literal_bool_returns_bool():
    """bool must be checked before int (bool is subclass of int in Python)."""
    assert infer_literal_type(True) == ("BOOL", ())
    assert infer_literal_type(False) == ("BOOL", ())


def test_infer_literal_float_returns_double():
    """Python float is double precision; DEFAULT inferred type is DOUBLE."""
    assert infer_literal_type(1.5) == ("DOUBLE", ())
    assert infer_literal_type(-0.0) == ("DOUBLE", ())


def test_infer_literal_str_returns_text():
    assert infer_literal_type("hello") == ("TEXT", ())
    assert infer_literal_type("") == ("TEXT", ())


def test_infer_literal_date_returns_date():
    d = datetime.date(2026, 7, 17)
    assert infer_literal_type(d) == ("DATE", ())


def test_infer_literal_time_returns_time():
    t = datetime.time(14, 30, 0)
    assert infer_literal_type(t) == ("TIME", ())


def test_infer_literal_datetime_returns_timestamp():
    dt = datetime.datetime(2026, 7, 17, 14, 30, 0)
    assert infer_literal_type(dt) == ("TIMESTAMP", ())


def test_infer_literal_unknown_raises():
    with pytest.raises(TypeError, match="unknown literal type"):
        infer_literal_type([1, 2, 3])
    with pytest.raises(TypeError, match="unknown literal type"):
        infer_literal_type(None)
    with pytest.raises(TypeError, match="unknown literal type"):
        infer_literal_type(b"bytes")
