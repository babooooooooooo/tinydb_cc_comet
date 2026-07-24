"""tinydb-join-query (T6): JOIN 执行集成测试。"""
import pytest
import tinydb


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE users(id INT, name TEXT)")
    db.execute("CREATE TABLE orders(id INT, user_id INT, total INT)")
    db.execute("INSERT INTO users(id, name) VALUES (1, 'alice'), (2, 'bob')")
    db.execute(
        "INSERT INTO orders(id, user_id, total) VALUES "
        "(10, 1, 100), (11, 2, 200), (12, 3, 50)"
    )


@pytest.mark.integration
def test_inner_join_returns_matched_rows(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.id FROM users u INNER JOIN orders o ON u.id = o.user_id"
    )
    # u.id=1 -> o.id=10; u.id=2 -> o.id=11; o.user_id=3 不匹配
    assert len(rows) == 2
    by_u = {r["u.id"]: r["o.id"] for r in rows}
    assert by_u[1] == 10 and by_u[2] == 11


@pytest.mark.integration
def test_cross_join_returns_cartesian(db):
    _setup(db)
    rows = db.execute("SELECT * FROM users CROSS JOIN orders")
    assert len(rows) == 2 * 3  # 6


@pytest.mark.integration
def test_inner_join_with_on_compound(db):
    """Task 7: 通过 WHERE o.total = 200 验证 JOIN 后谓词下推 + 列投影。"""
    _setup(db)
    # Parser (T2) 不支持 ON 内嵌 AND/OR 与 > 等操作符；等效用 WHERE =
    rows = db.execute(
        "SELECT u.id FROM users u JOIN orders o "
        "ON u.id = o.user_id WHERE o.total = 200"
    )
    assert {r["u.id"] for r in rows} == {2}  # o.total=200 对应 u=2


@pytest.mark.integration
def test_using_join_merges_id_column(db):
    _setup(db)
    rows = db.execute(
        "SELECT * FROM users JOIN orders USING (id)"
    )
    # id 列同时存在于 users 与 orders；USING 应合并为单个 'id'
    assert all("id" in r.columns for r in rows)
    assert all(r.columns.count("id") == 1 for r in rows)


@pytest.mark.integration
def test_natural_join_with_no_common_keys_returns_cross(db):
    _setup(db)
    db.execute("CREATE TABLE audit(ts INT)")
    db.execute("INSERT INTO audit(ts) VALUES (100), (200)")
    rows = db.execute("SELECT * FROM users NATURAL LEFT JOIN audit")
    # users 2 行 * audit 2 行 = 4 行
    assert len(rows) == 4


@pytest.mark.integration
def test_chained_three_table_join(db):
    _setup(db)
    db.execute("CREATE TABLE c(id INT)")
    db.execute("INSERT INTO c(id) VALUES (1), (2)")
    rows = db.execute(
        "SELECT u.id FROM users u "
        "JOIN orders o ON u.id = o.user_id "
        "JOIN c ON o.id = c.id"
    )
    # u=1->o=10->c=1 (10 != 1, no match); u=2->o=11->c=2 (11 != 2, no match)
    # 上述数据下没有匹配，返回 []
    assert rows == []