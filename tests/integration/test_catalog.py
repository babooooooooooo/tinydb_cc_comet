import json

import pytest

from tinydb.catalog import Catalog, Column, TableInfo
from tinydb.pager import PAGE_SIZE


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-01")
def test_catalog_empty_roundtrip():
    c = Catalog()
    raw = c.to_bytes()
    assert len(raw) == PAGE_SIZE
    c2 = Catalog.from_bytes(raw)
    assert c2.tables == {}


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-02")
def test_catalog_register_table():
    c = Catalog()
    schema = (Column(name="id", type="INT"), Column(name="name", type="TEXT"))
    c.create_table("users", schema, root_page_id=2, next_page_id=2)
    assert "users" in c.tables
    ti = c.get_table("users")
    assert ti.schema == [("id", "INT"), ("name", "TEXT")]
    assert ti.root_page_id == 2


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-03")
def test_catalog_persisted_across_reopen(tmp_path):
    from tinydb.pager import Pager

    p = Pager(str(tmp_path / "cat.db"))
    c = Catalog.from_bytes(p.read_page(1))
    c.create_table("t", (Column(name="x", type="INT"),), root_page_id=2, next_page_id=2)
    p.write_page(1, c.to_bytes())
    p.flush()
    p.close()
    p2 = Pager(str(tmp_path / "cat.db"))
    c2 = Catalog.from_bytes(p2.read_page(1))
    assert "t" in c2.tables
    p2.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-006-SCN-01")
def test_catalog_encodes_int_fields_as_json_strings():
    c = Catalog()
    # simulate large int root_page_id > 2^53
    huge = 2**60
    c.create_table("big", (Column(name="v", type="INT"),), root_page_id=huge, next_page_id=huge)
    raw = c.to_bytes()
    text = raw.rstrip(b"\x00").decode("utf-8")
    parsed = json.loads(text)
    assert parsed["tables"]["big"]["root_page_id"] == str(huge)
    c2 = Catalog.from_bytes(raw)
    assert c2.get_table("big").root_page_id == huge


@pytest.mark.integration
def test_column_dataclass_roundtrip():
    col = Column(name="id", type="INT", nullable=False, unique=False, primary_key=True)
    d = col.to_dict()
    assert d == {"name": "id", "type": "INT", "type_params": [], "nullable": False, "unique": False, "primary_key": True}
    col2 = Column.from_dict(d)
    assert col2 == col


@pytest.mark.integration
def test_column_defaults():
    # SQL92 default: nullable=True; unique and primary_key are False.
    col = Column(name="x", type="TEXT")
    assert col.nullable is True
    assert col.unique is False
    assert col.primary_key is False


@pytest.mark.integration
def test_table_info_schema_projection_preserves_order():
    ti = TableInfo(
        name="u",
        columns=(
            Column(name="id", type="INT", nullable=False, unique=False, primary_key=True),
            Column(name="name", type="TEXT", nullable=True, unique=False, primary_key=False),
        ),
        root_page_id=2,
        next_page_id=2,
    )
    assert ti.schema == [("id", "INT"), ("name", "TEXT")]


@pytest.mark.integration
def test_table_info_columns_is_tuple_not_list():
    ti = TableInfo(
        name="u",
        columns=(Column(name="x", type="INT"),),
        root_page_id=2,
        next_page_id=2,
    )
    assert isinstance(ti.columns, tuple)
