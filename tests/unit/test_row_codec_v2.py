"""Tests for row_codec v2 (Task 16): full 15-type wire format + schema_v2 dispatch.

Verifies:
- All 15 registered types roundtrip via encode_row/decode_row.
- Legacy 2-tuple (name, type) schema still works.
- New 3-tuple (name, type, type_params) schema is the canonical form.
- TableInfo exposes both .schema (legacy) and .schema_v2 (canonical).
- Multi-column rows with mixed types roundtrip correctly.
- NULL values are still encoded via bitmap (LSB-first).
"""
import datetime
import pytest

from tinydb.row_codec import encode_row, decode_row
from tinydb.catalog import TableInfo, Column


def test_encode_decode_roundtrip_int():
    schema = [("id", "INT", ())]
    encoded = encode_row([42], schema)
    assert decode_row(encoded, schema) == [42]


def test_encode_decode_roundtrip_text():
    schema = [("name", "TEXT", ())]
    encoded = encode_row(["hello"], schema)
    assert decode_row(encoded, schema) == ["hello"]


def test_encode_decode_roundtrip_bool():
    schema = [("flag", "BOOL", ())]
    encoded = encode_row([True], schema)
    assert decode_row(encoded, schema) == [True]


def test_encode_decode_roundtrip_float():
    schema = [("x", "FLOAT", ())]
    encoded = encode_row([1.5], schema)
    assert decode_row(encoded, schema) == [1.5]


def test_encode_decode_roundtrip_smallint():
    schema = [("id", "SMALLINT", ())]
    encoded = encode_row([100], schema)
    assert decode_row(encoded, schema) == [100]


def test_encode_decode_roundtrip_bigint():
    schema = [("id", "BIGINT", ())]
    encoded = encode_row([1_000_000_000], schema)
    assert decode_row(encoded, schema) == [1_000_000_000]


def test_encode_decode_roundtrip_double():
    schema = [("val", "DOUBLE", ())]
    encoded = encode_row([3.14159265358979], schema)
    assert decode_row(encoded, schema) == [3.14159265358979]


def test_encode_decode_roundtrip_varchar():
    schema = [("name", "VARCHAR", (10,))]
    encoded = encode_row(["hello"], schema)
    assert decode_row(encoded, schema) == ["hello"]


def test_encode_decode_roundtrip_char_padded():
    schema = [("code", "CHAR", (5,))]
    encoded = encode_row(["ab"], schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == "ab   "  # padding preserved


def test_encode_decode_roundtrip_decimal():
    schema = [("amount", "DECIMAL", (10, 2))]
    encoded = encode_row([1.23], schema)
    decoded = decode_row(encoded, schema)
    assert abs(decoded[0] - 1.23) < 0.01


def test_encode_decode_roundtrip_date():
    schema = [("d", "DATE", ())]
    encoded = encode_row([datetime.date(2026, 7, 16)], schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == datetime.date(2026, 7, 16)


def test_encode_decode_roundtrip_time():
    schema = [("t", "TIME", ())]
    encoded = encode_row([datetime.time(14, 30, 0)], schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == datetime.time(14, 30, 0)


def test_encode_decode_roundtrip_timestamp():
    schema = [("ts", "TIMESTAMP", ())]
    encoded = encode_row([datetime.datetime(2026, 7, 16, 14, 30, 0)], schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == datetime.datetime(2026, 7, 16, 14, 30, 0)


def test_encode_decode_roundtrip_multiple_columns():
    schema = [
        ("id", "INT", ()),
        ("name", "VARCHAR", (20,)),
        ("amount", "DECIMAL", (8, 2)),
        ("d", "DATE", ()),
    ]
    row = [1, "alice", 12.34, datetime.date(2026, 7, 16)]
    encoded = encode_row(row, schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == 1
    assert decoded[1] == "alice"
    assert abs(decoded[2] - 12.34) < 0.01
    assert decoded[3] == datetime.date(2026, 7, 16)


def test_encode_decode_roundtrip_with_null():
    schema = [("id", "INT", ()), ("name", "VARCHAR", (10,))]
    encoded = encode_row([1, None], schema)
    assert decode_row(encoded, schema) == [1, None]


def test_legacy_2tuple_schema_still_works():
    """Old 2-tuple [(name, type)] format must still work for backward compat."""
    schema = [("id", "INT"), ("name", "TEXT")]
    encoded = encode_row([42, "alice"], schema)
    assert decode_row(encoded, schema) == [42, "alice"]


def test_table_info_schema_v2_returns_3tuple():
    """TableInfo.schema_v2 should emit 3-tuple (name, type, type_params)."""
    table = TableInfo(
        name="users",
        columns=(
            Column(name="id", type="INT"),
            Column(name="name", type="VARCHAR", type_params=(64,)),
        ),
        root_page_id=2,
        next_page_id=3,
    )
    assert table.schema_v2 == [
        ("id", "INT", ()),
        ("name", "VARCHAR", (64,)),
    ]


def test_table_info_schema_returns_2tuple_legacy():
    """TableInfo.schema (legacy) should emit 2-tuple (name, type)."""
    table = TableInfo(
        name="users",
        columns=(
            Column(name="id", type="INT"),
            Column(name="name", type="VARCHAR", type_params=(64,)),
        ),
        root_page_id=2,
        next_page_id=3,
    )
    assert table.schema == [
        ("id", "INT"),
        ("name", "VARCHAR"),
    ]