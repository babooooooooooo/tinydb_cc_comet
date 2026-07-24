import pytest
import tinydb


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE u(id INT, name TEXT, dept TEXT)")
    db.execute("CREATE TABLE o(id INT, uid INT, total INT, status TEXT)")
    db.execute("INSERT INTO u(id, name, dept) VALUES (1,'a','eng'),(2,'b','eng'),(3,'c','sales')")
    db.execute(
        "INSERT INTO o(id, uid, total, status) VALUES "
        "(10,1,100,'paid'),(11,1,150,'paid'),(12,2,200,'open'),(13,3,50,'paid')"
    )


@pytest.mark.integration
def test_join_then_where_filters_by_status(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid WHERE o.status = 'paid'"
    )
    # paid: o=10,11,13 → u=1,1,3
    assert {r["u.id"] for r in rows} == {1, 3}
    assert len(rows) == 3


@pytest.mark.integration
def test_join_then_group_by_count(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.dept, COUNT(*) AS n FROM u JOIN o ON u.id = o.uid "
        "GROUP BY u.dept"
    )
    by_dept = {r["dept"]: r["n"] for r in rows}
    assert by_dept == {"eng": 3, "sales": 1}


@pytest.mark.integration
def test_join_then_having(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, COUNT(*) AS n FROM u JOIN o ON u.id = o.uid "
        "GROUP BY u.id HAVING COUNT(*) > 1"
    )
    by_u = {r["u.id"]: r["n"] for r in rows}
    assert by_u == {1: 2}  # u=1 有两单


@pytest.mark.integration
def test_join_then_sum(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, SUM(o.total) AS s FROM u JOIN o ON u.id = o.uid "
        "GROUP BY u.id"
    )
    by_u = {r["u.id"]: r["s"] for r in rows}
    assert by_u == {1: 250, 2: 200, 3: 50}


@pytest.mark.integration
def test_join_then_order_by_and_limit(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.total FROM u JOIN o ON u.id = o.uid "
        "ORDER BY o.total DESC LIMIT 2"
    )
    totals = [r["total"] for r in rows]
    assert totals == [200, 150]


@pytest.mark.integration
def test_join_then_offset(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.total FROM u JOIN o ON u.id = o.uid "
        "ORDER BY o.total DESC LIMIT 2 OFFSET 1"
    )
    totals = [r["total"] for r in rows]
    assert totals == [150, 100]


@pytest.mark.integration
def test_join_then_select_star_uses_qualified_labels(db):
    _setup(db)
    rows = db.execute("SELECT * FROM u JOIN o ON u.id = o.uid WHERE u.id = 1")
    r = rows[0]
    assert "u.id" in r.columns
    assert "o.id" in r.columns
    assert r["u.id"] == 1 and r["o.id"] in (10, 11)


@pytest.mark.integration
def test_join_with_unknown_column_in_where(db):
    _setup(db)
    with pytest.raises(Exception):
        db.execute(
            "SELECT * FROM u JOIN o ON u.id = o.uid WHERE u.missing = 1"
        )
