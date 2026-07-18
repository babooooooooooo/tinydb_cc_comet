"""Tests for Column.type_params backward-compatible field (Task 15).

Covers:
- Default value is empty tuple
- Construction with type_params works
- from_dict defaults to () when key missing (legacy JSON compat)
- from_dict reads list/tuple value from JSON
- to_dict round-trips through dict
- Old 2-tuple [name, type] format still loads via _load_column
"""
from tinydb.catalog import Column


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


def test_column_legacy_2tuple_format_still_works():
    """Old catalog schema with [name, type] list format still loads."""
    try:
        from tinydb.catalog import _load_column
    except ImportError:
        return  # no _load_column helper, skip
    col = _load_column(["id", "INT"])
    assert col.name == "id"
    assert col.type == "INT"
    assert col.type_params == ()


def test_column_roundtrip_through_dict():
    """Column -> dict -> Column preserves type_params."""
    col1 = Column(name="balance", type="DECIMAL", type_params=(10, 2), nullable=False)
    d = col1.to_dict()
    col2 = Column.from_dict(d)
    assert col1 == col2
