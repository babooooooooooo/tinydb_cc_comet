"""Unit tests for tinydb._repl_format.format_rows dispatcher (Task 3)."""
import csv
import io
import json

import pytest

from tinydb.database import Row
from tinydb._repl_format import format_rows


pytestmark = pytest.mark.unit


@pytest.fixture
def sample_rows():
    return [
        Row(values=(1, "alice"), columns=("id", "name")),
        Row(values=(2, "bob"), columns=("id", "name")),
    ]


def test_table_format_includes_header_separator_and_rows(sample_rows):
    """table 格式与既有 _format_table 字节兼容."""
    out = format_rows(sample_rows, "table")
    lines = out.split("\n")
    assert lines[0].strip() == "id | name"
    assert "---" in lines[1]
    assert "1  | alice" in lines[2]
    assert "2  | bob"    in lines[3]


def test_csv_format_emits_rfc_4180(sample_rows):
    """csv 格式: header 行 + RFC 4180 quoting."""
    out = format_rows(sample_rows, "csv")
    parsed = list(csv.reader(io.StringIO(out)))
    assert parsed[0] == ["id", "name"]
    assert parsed[1] == ["1", "alice"]
    assert parsed[2] == ["2", "bob"]


def test_csv_quotes_fields_with_commas_or_quotes():
    rows = [Row(values=('hello, world', 'has "quote"'), columns=("a", "b"))]
    out = format_rows(rows, "csv")
    assert '"hello, world"' in out
    assert '"has ""quote"""' in out


def test_json_format_returns_array_of_objects(sample_rows):
    """json 格式: 数组,每个元素 dict[column]=value."""
    out = format_rows(sample_rows, "json")
    parsed = json.loads(out)
    assert parsed == [
        {"id": 1, "name": "alice"},
        {"id": 2, "name": "bob"},
    ]


def test_json_with_non_serializable_falls_back_to_str():
    rows = [Row(values=(object(),), columns=("o",))]
    out = format_rows(rows, "json")
    parsed = json.loads(out)
    assert isinstance(parsed[0]["o"], str)


def test_format_empty_rows_returns_no_rows_token():
    """空 rows 三格式均返回 '(no rows)'."""
    for fmt in ("table", "csv", "json"):
        assert format_rows([], fmt) == "(no rows)"


def test_format_unknown_raises_value_error(sample_rows):
    """format_rows 收到未知 fmt 抛 ValueError."""
    with pytest.raises(ValueError, match="unknown format"):
        format_rows(sample_rows, "markdown")


def test_table_truncates_columns_at_thirty_chars():
    long_val = "x" * 31
    rows = [Row(values=(long_val,), columns=("value",))]
    out = format_rows(rows, "table")
    assert "x" * 29 + "…" in out
    assert "x" * 30 not in out
