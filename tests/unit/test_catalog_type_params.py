"""Tests for Column.type_params (Task 15).

Covers:
- Default value is empty tuple
- Construction with type_params works
- from_dict defaults to () when key missing
- from_dict reads list/tuple value from JSON
- to_dict round-trips through dict
- Column -> dict -> Column preserves type_params
"""
import pytest

from tinydb.catalog import Column, _load_column
from tinydb.errors import InvalidDatabaseFile


def test_column_default_type_params_empty():
    col = Column(name="id", type="INT")
    assert col.type_params == ()


def test_column_with_type_params():
    col = Column(name="name", type="VARCHAR", type_params=(64,))
    assert col.type_params == (64,)


def test_column_from_dict_legacy_no_type_params():
    """Old JSON without type_params key must default to ()."""
    legacy = {
        "name": "id",
        "type": "INT",
        "nullable": False,
        "unique": False,
        "primary_key": True,
    }
    col = Column.from_dict(legacy)
    assert col.type_params == ()


def test_column_from_dict_with_type_params():
    new = {
        "name": "name",
        "type": "VARCHAR",
        "type_params": [64],
        "nullable": True,
        "unique": False,
        "primary_key": False,
    }
    col = Column.from_dict(new)
    assert col.type_params == (64,)


def test_column_to_dict_includes_type_params():
    col = Column(name="amount", type="DECIMAL", type_params=(10, 2))
    d = col.to_dict()
    assert d["type_params"] == [10, 2]


def test_column_to_dict_empty_type_params():
    col = Column(name="id", type="INT")
    d = col.to_dict()
    assert d["type_params"] == []


def test_column_roundtrip_through_dict():
    """Column -> dict -> Column preserves type_params."""
    col1 = Column(name="balance", type="DECIMAL", type_params=(10, 2), nullable=False)
    d = col1.to_dict()
    col2 = Column.from_dict(d)
    assert col1 == col2


def test_load_column_rejects_legacy_list_form_with_helpful_message():
    """SUGGESTION-2: when a v1 [name, type] array is encountered, the error must
    explicitly name the legacy form so users hitting it on upgrade have a clear
    migration hint.
    """
    with pytest.raises(InvalidDatabaseFile, match="legacy \\[name, type\\] arrays"):
        _load_column(["id", "INT"])


def test_load_column_rejects_non_dict_non_list_with_generic_message():
    """F4: Non-list, non-dict inputs should get a generic 'expected Column.to_dict()
    object form' message, NOT the misleading legacy-form message.

    The legacy [name, type] hint must be reserved for actual list inputs.
    """
    with pytest.raises(InvalidDatabaseFile) as excinfo:
        _load_column(42)
    msg = str(excinfo.value)
    assert "expected Column.to_dict" in msg
    assert "legacy [name, type] arrays" not in msg

    with pytest.raises(InvalidDatabaseFile) as excinfo:
        _load_column(None)
    msg = str(excinfo.value)
    assert "expected Column.to_dict" in msg
    assert "legacy [name, type] arrays" not in msg
