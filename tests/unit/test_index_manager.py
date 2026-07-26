"""Tests for IndexManager: B+tree index per (table, indexed-column) pair."""
from __future__ import annotations

from tinydb.catalog import Column, TableInfo
from tinydb.index_manager import IndexManager
from tinydb.pager import Pager
from tinydb.type_system import codec_for


def test_index_manager_rebuild_for_table_with_pk():
    p = Pager(":memory:")
    cols = (
        Column(name="id", type="INT", primary_key=True),
        Column(name="name", type="TEXT"),
    )
    ti = TableInfo(columns=cols, root_page_id=0, next_page_id=0, name="t")
    im = IndexManager(pager=p)
    im.rebuild_for_table(ti, [(1, "alice"), (2, "bob")])
    key_codec = codec_for("INT", ())
    ref = im.lookup_key("t", "id", key_codec.encode_py(1))
    assert ref is not None
    ref2 = im.lookup_key("t", "id", key_codec.encode_py(999))
    assert ref2 is None
    p.close()


def test_index_manager_all_indexes_yields_triples():
    from tinydb.database import Database
    from tinydb.index_manager import IndexManager
    from tinydb.pager import Pager
    with Database(":memory:") as db:
        db.execute("CREATE TABLE u(id INT PRIMARY KEY)")
        idx = db.index_manager
        triples = list(idx.all_indexes())
        assert len(triples) == 1
        table, column, btree = triples[0]
        assert table == "u"
        assert column == "id"
        assert btree is not None
