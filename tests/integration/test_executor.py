"""Integration tests for tinydb.executor — DDL CREATE/DROP TABLE execution.

These tests touch the filesystem (real I/O via tmp_path) so they live under
tests/integration/ and carry the `integration` pytest marker. They drive the
Executor end-to-end: parse SQL → execute AST → catalog persisted to page 1.
"""
import pytest

from tinydb.pager import Pager
from tinydb.catalog import Catalog
from tinydb.executor import Executor
from tinydb.errors import ExecutionError
from tinydb.parser import parse
from tinydb.tokenizer import tokenize


def _exec(pager, sql):
    """Drive a SQL string through parse → Executor.

    The Executor persists the catalog to page 1 and flushes itself, so the
    helper does NOT re-persist — that way the test genuinely exercises the
    Executor's persistence path (SCN-04/05) rather than masking it.
    """
    cat = Catalog.from_bytes(pager.read_page(1))
    ex = Executor(pager, cat)
    stmts = parse(tokenize(sql)).statements
    for s in stmts:
        ex.execute(s)


def _select(pager, sql):
    """Drive SELECT through parse → Executor; return list[list] of decoded rows.

    SELECT is read-only, so the helper does NOT re-persist the catalog.
    Task 20 will wrap the result list in Row objects; until then the raw
    decoded-value lists are what callers see.
    """
    cat = Catalog.from_bytes(pager.read_page(1))
    ex = Executor(pager, cat)
    stmt = parse(tokenize(sql)).statements[0]
    return ex.execute(stmt)


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-04")
def test_create_table_persists_to_catalog(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE users(id INT, name TEXT)")
        cat = Catalog.from_bytes(p.read_page(1))
        assert "users" in cat.tables
        assert cat.get_table("users").schema == [
            ("id", "INT"),
            ("name", "TEXT"),
        ]
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-05")
def test_drop_table_removes_from_catalog(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE users(id INT)")
        _exec(p, "DROP TABLE users")
        cat = Catalog.from_bytes(p.read_page(1))
        assert "users" not in cat.tables
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-04")
def test_create_duplicate_table_raises(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE users(id INT)")
        with pytest.raises(ExecutionError, match="already exists"):
            _exec(p, "CREATE TABLE users(id INT)")
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-005-SCN-05")
def test_drop_missing_table_raises(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        with pytest.raises(ExecutionError, match="does not exist"):
            _exec(p, "DROP TABLE ghost")
    finally:
        p.close()


# --- Task 18: INSERT + linear scan helper ---------------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-03")
def test_insert_allocates_new_page_when_full(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE t(v INT)")
        # MAX_SLOTS=32 caps a single page; 35 inserts force the executor
        # to allocate a second data page (overflow chain).
        for i in range(35):
            _exec(p, f"INSERT INTO t(v) VALUES ({i})")
        cat = Catalog.from_bytes(p.read_page(1))
        ti = cat.get_table("t")
        assert ti.next_page_id > ti.root_page_id
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-01")
def test_scan_returns_all_inserted_rows(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE t(id INT, name TEXT)")
        _exec(p, "INSERT INTO t(id, name) VALUES (1, 'alice')")
        _exec(p, "INSERT INTO t(id, name) VALUES (2, 'bob')")
        cat = Catalog.from_bytes(p.read_page(1))
        ti = cat.get_table("t")
        ex = Executor(p, cat)
        rows = ex._scan_table(ti)
        # rows is list of (slot_id, decoded_values, page_id)
        decoded = [r[1] for r in rows]
        assert [1, "alice"] in decoded
        assert [2, "bob"] in decoded
        assert len(rows) == 2
    finally:
        p.close()

# --- Task 19: SELECT projection + WHERE + DELETE -------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-02")
def test_select_where_equality(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE t(id INT, name TEXT)")
        _exec(p, "INSERT INTO t(id, name) VALUES (1, 'a')")
        _exec(p, "INSERT INTO t(id, name) VALUES (2, 'b')")
        rows = _select(p, "SELECT * FROM t WHERE id = 2")
        assert rows == [[2, "b"]]
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-02")
def test_select_where_type_mismatch_raises(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE t(id INT)")
        _exec(p, "INSERT INTO t(id) VALUES (1)")
        with pytest.raises(TypeError, match="INT vs TEXT"):
            _select(p, "SELECT * FROM t WHERE id = '1'")
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-01")
def test_delete_marks_tombstones(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE t(id INT)")
        for i in range(5):
            _exec(p, f"INSERT INTO t(id) VALUES ({i})")
        _exec(p, "DELETE FROM t WHERE id = 2")
        rows = _select(p, "SELECT * FROM t")
        assert sorted(r[0] for r in rows) == [0, 1, 3, 4]
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-02")
def test_select_empty_table_returns_empty_list(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    try:
        _exec(p, "CREATE TABLE t(id INT, name TEXT)")
        assert _select(p, "SELECT * FROM t") == []
        assert _select(p, "SELECT id FROM t") == []
    finally:
        p.close()


@pytest.mark.integration
def test_create_table_with_not_null_persists_constraint(tmp_path):
    from tinydb import Database
    with Database(str(tmp_path / "nn.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL, name TEXT)")
        ti = db.catalog.get_table("t")
    assert ti.columns[0].nullable is False
    assert ti.columns[1].nullable is True


@pytest.mark.integration
def test_create_table_with_unique_persists_constraint(tmp_path):
    from tinydb import Database
    with Database(str(tmp_path / "uq.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, email TEXT UNIQUE)")
        ti = db.catalog.get_table("t")
    assert ti.columns[0].primary_key is True
    assert ti.columns[1].unique is True
