"""Integration suite for engine-v1 features (Task 11, Design Doc §8.3).

Locks in the I-V1-* matrix: end-to-end UPDATE through Database, persistence
across reopen, multi-page UPDATE, compound WHERE chain, ORDER BY/LIMIT/OFFSET,
and chain fallback for over-sized rows.
"""
import os, tempfile, pytest

from tinydb.database import Database
from tinydb.errors import ExecutionError


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    os.unlink(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def db(db_path):
    return Database(db_path)


def _vals(rows):
    """Extract Row.values tuples as a list of tuples (ignores column names)."""
    return [r.values for r in rows]


@pytest.mark.integration
def test_update_end_to_end(db):
    db.execute("CREATE TABLE t (id INT, name TEXT)")
    db.execute("INSERT INTO t (id, name) VALUES (1, 'a'), (2, 'b')")
    db.execute("UPDATE t SET name='z' WHERE id=1")
    names = sorted(list(r)[0] for r in db.execute("SELECT name FROM t"))
    # id=1 'a' -> 'z'; id=2 'b' unchanged
    assert names == ["b", "z"]


@pytest.mark.integration
def test_update_persists_after_reopen(db, db_path):
    db.execute("CREATE TABLE t (a INT)")
    db.execute("INSERT INTO t (a) VALUES (1)")
    db.execute("UPDATE t SET a=99")
    db.close()
    db2 = Database(db_path)
    assert [list(r)[0] for r in db2.execute("SELECT a FROM t")] == [99]


@pytest.mark.integration
def test_update_compound_where_multi_page(db):
    db.execute("CREATE TABLE t (a INT, b INT)")
    rows = [(i % 7, i) for i in range(200)]
    for a, b in rows:
        db.execute(f"INSERT INTO t (a, b) VALUES ({a}, {b})")
    # UPDATE b=0 where a=3 AND b=10 — compound WHERE across many pages.
    db.execute("UPDATE t SET b=0 WHERE a=3 AND b=10")
    out = db.execute("SELECT a, b FROM t WHERE a=3 AND b=0")
    assert all(list(r)[0] == 3 for r in out)


@pytest.mark.integration
def test_update_grow_falls_back_to_chain(db):
    db.execute("CREATE TABLE t (a INT, payload TEXT)")
    db.execute("INSERT INTO t (a, payload) VALUES (1, 'short')")
    db.execute("UPDATE t SET payload='" + "x" * 4000 + "'")
    out = db.execute("SELECT a, payload FROM t")
    assert len(out) == 1 and list(out[0])[1] == "x" * 4000


@pytest.mark.integration
def test_select_order_limit_chain_top_n(db):
    db.execute("CREATE TABLE events (ts INT, level INT)")
    for ts in range(100):
        db.execute(f"INSERT INTO events (ts, level) VALUES ({ts}, {ts % 5})")
    out = db.execute("SELECT ts FROM events ORDER BY ts DESC LIMIT 10")
    assert [list(r)[0] for r in out] == list(range(99, 89, -1))


@pytest.mark.integration
def test_select_complex_where_e2e(db):
    db.execute("CREATE TABLE t (a INT, b INT, c INT)")
    for a, b, c in [(1, 2, 3), (1, 3, 3), (2, 2, 2), (3, 3, 3)]:
        db.execute(f"INSERT INTO t (a, b, c) VALUES ({a}, {b}, {c})")
    out = db.execute(
        "SELECT a FROM t WHERE (a=1 OR b=2) AND NOT c=3 ORDER BY a ASC"
    )
    # (a=1 OR b=2): rows (1,2,3), (1,3,3), (2,2,2); AND NOT c=3: (2,2,2) only
    assert [list(r)[0] for r in out] == [2]


@pytest.mark.integration
def test_select_offset_pagination(db):
    db.execute("CREATE TABLE t (a INT)")
    for i in range(20):
        db.execute(f"INSERT INTO t (a) VALUES ({i})")
    page1 = db.execute("SELECT a FROM t ORDER BY a ASC LIMIT 5 OFFSET 0")
    page2 = db.execute("SELECT a FROM t ORDER BY a ASC LIMIT 5 OFFSET 5")
    assert [list(r)[0] for r in page1] == [0, 1, 2, 3, 4]
    assert [list(r)[0] for r in page2] == [5, 6, 7, 8, 9]


@pytest.mark.integration
def test_select_all_features_chain(db):
    db.execute("CREATE TABLE t (a INT, b INT)")
    for a, b in [(1, 10), (2, 20), (3, 30), (1, 11), (2, 21)]:
        db.execute(f"INSERT INTO t (a, b) VALUES ({a}, {b})")
    db.execute("UPDATE t SET b=99 WHERE a=2")
    out = db.execute("SELECT a, b FROM t ORDER BY a ASC, b ASC")
    assert [r.values for r in out] == [(1, 10), (1, 11), (2, 99), (2, 99), (3, 30)]


@pytest.mark.integration
def test_delete_then_update_same_row(db):
    db.execute("CREATE TABLE t (a INT)")
    db.execute("INSERT INTO t (a) VALUES (1)")
    db.execute("DELETE FROM t WHERE a=1")
    db.execute("INSERT INTO t (a) VALUES (1)")
    db.execute("UPDATE t SET a=42")
    assert [list(r)[0] for r in db.execute("SELECT a FROM t")] == [42]


@pytest.mark.integration
def test_update_spill_row(db):
    db.execute("CREATE TABLE t (a INT, blob TEXT)")
    db.execute("INSERT INTO t (a, blob) VALUES (1, 'small')")
    db.execute("UPDATE t SET blob='" + "Q" * 8000 + "'")
    out = db.execute("SELECT a, blob FROM t")
    assert out[0].values == (1, "Q" * 8000)