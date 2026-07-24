import pytest
import tinydb


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE u(id INT, name TEXT)")
    db.execute("CREATE TABLE o(id INT, uid INT)")
    db.execute("INSERT INTO u(id, name) VALUES (1,'a'),(2,'b')")
    db.execute("INSERT INTO o(id, uid) VALUES (10,1),(11,2)")


@pytest.mark.integration
def test_row_getitem_by_qualified_label(db):
    _setup(db)
    rows = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    r = rows[0]
    assert r["u.id"] == 1
    assert r["o.id"] == 10


@pytest.mark.integration
def test_row_getitem_by_merged_using_key(db):
    _setup(db)
    db.execute("INSERT INTO o(id, uid) VALUES (1, 1)")  # 共享 id=1
    rows = db.execute("SELECT * FROM u JOIN o USING (id)")
    r = rows[0]
    # 合并键 'id' 只出现一次
    assert r.columns.count("id") == 1
    assert r["id"] == 1


@pytest.mark.integration
def test_row_attr_access_still_works_for_safe_identifier(db):
    _setup(db)
    rows = db.execute("SELECT u.id FROM u JOIN o ON u.id = o.uid")
    r = rows[0]
    # 'u.id' 不是合法 Python 标识符，属性访问失败；映射访问可用
    assert r["u.id"] == 1
    with pytest.raises(AttributeError):
        _ = r.u.id  # type: ignore


@pytest.mark.integration
def test_row_iteration_and_repr(db):
    _setup(db)
    rows = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    r = rows[0]
    assert list(r) == [1, 10]
    text = repr(r)
    assert "u.id=" in text
    assert "o.id=" in text


@pytest.mark.integration
def test_row_equality(db):
    _setup(db)
    rows1 = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    rows2 = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    assert rows1[0] == rows2[0]


@pytest.mark.integration
def test_single_table_row_keeps_bare_columns(db):
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    db.execute("INSERT INTO t(id, name) VALUES (1,'a')")
    rows = db.execute("SELECT * FROM t")
    r = rows[0]
    assert r.id == 1
    assert r["id"] == 1
    assert r["name"] == "a"
