import json
from pathlib import Path

import pytest

from tinydb.catalog import Catalog, Column

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load_fixture(name: str) -> bytes:
    """Read a JSON fixture and pad to PAGE_SIZE so Catalog.from_bytes can decode it."""
    from tinydb.pager import PAGE_SIZE
    raw_text = (FIXTURES / name).read_bytes()
    # Pad with nulls so the buffer is exactly one page; mirror the on-disk format.
    return raw_text + b"\x00" * (PAGE_SIZE - len(raw_text))


@pytest.mark.integration
def test_catalog_loads_new_format_roundtrip():
    raw = _load_fixture("new_constraints_schema.json")
    cat = Catalog.from_bytes(raw)
    ti = cat.get_table("users")
    assert ti is not None
    assert [c.name for c in ti.columns] == ["id", "email", "name"]
    assert ti.columns[0].primary_key is True
    assert ti.columns[1].unique is True
    assert ti.columns[1].nullable is False
    assert ti.columns[2].nullable is True


@pytest.mark.integration
def test_catalog_to_bytes_uses_new_format():
    cat = Catalog()
    cat.create_table(
        "u",
        (Column(name="id", type="INT", nullable=False, unique=False, primary_key=True),),
        root_page_id=2,
        next_page_id=2,
    )
    raw = cat.to_bytes()
    text = raw.rstrip(b"\x00").decode("utf-8")
    parsed = json.loads(text)
    assert isinstance(parsed["tables"]["u"]["schema"][0], dict)
    assert parsed["tables"]["u"]["schema"][0]["primary_key"] is True


@pytest.mark.integration
def test_catalog_constraints_persist_across_reopen(tmp_path):
    from tinydb.pager import Pager

    p = Pager(str(tmp_path / "ct.db"))
    cat = Catalog.from_bytes(p.read_page(1))
    cat.create_table(
        "u",
        (Column(name="id", type="INT", nullable=False, unique=False, primary_key=True),),
        root_page_id=2,
        next_page_id=2,
    )
    p.write_page(1, cat.to_bytes())
    p.flush()
    p.close()

    p2 = Pager(str(tmp_path / "ct.db"))
    cat2 = Catalog.from_bytes(p2.read_page(1))
    ti = cat2.get_table("u")
    assert ti.columns[0].primary_key is True
    assert ti.columns[0].nullable is False
    p2.close()


@pytest.mark.integration
def test_executor_legacy_table_insert_with_no_value_still_accepted(tmp_path):
    """Tables with nullable default columns accept inserts omitting them."""
    from tinydb import Database

    with Database(str(tmp_path / "legacy.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        # No value for 'name' — should default to None.
        db.execute("INSERT INTO t(id) VALUES (1)")
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].name is None
