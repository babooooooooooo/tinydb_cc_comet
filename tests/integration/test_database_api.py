"""Integration tests for the public Database + Row API (Task 20 / spec REQ-API-*).

These tests drive the full pipeline end-to-end: tokenize -> parse -> executor
-> Database wrapper. They live under tests/integration/ because every test
opens a real Pager (file-backed or :memory:) and may hit the filesystem via
tmp_path. Each test carries the `integration` pytest marker plus the
``spec_id`` it locks in (REQ-API-001..006 from the python-api spec).
"""
import pytest

import tinydb
from tinydb import Database, Row, errors


# --- REQ-API-001: package re-exports + version ------------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-001-SCN-01")
def test_import_database_and_row():
    assert tinydb.Database is Database
    assert tinydb.Row is Row


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-001-SCN-02")
def test_version_string():
    assert tinydb.__version__ == "0.1.0"


@pytest.mark.integration
def test_errors_module_re_exported():
    assert tinydb.errors is errors


# --- REQ-API-002: open / :memory: / context manager -------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-002-SCN-01")
def test_open_file_backed_creates_file(tmp_path):
    p = tmp_path / "db.db"
    Database(str(p)).close()
    assert p.exists()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-002-SCN-02")
def test_memory_mode_no_filesystem(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = Database(":memory:")
    db.close()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-002-SCN-03")
def test_context_manager_closes(tmp_path):
    p = tmp_path / "db.db"
    with Database(str(p)) as db:
        db.execute("CREATE TABLE t(id INT)")
    with Database(str(p)) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert rows == []


@pytest.mark.integration
def test_reopen_preserves_schema(tmp_path):
    p = str(tmp_path / "db.db")
    with Database(p) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
    with Database(p) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert rows == [Row(values=(1, "a"), columns=("id", "name"))]


# --- REQ-API-003: execute pipeline ------------------------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-003-SCN-01")
def test_select_returns_list_of_rows(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
        rows = db.execute("SELECT * FROM t")
    assert isinstance(rows, list) and len(rows) == 1
    assert isinstance(rows[0], Row)


@pytest.mark.integration
def test_select_named_projection(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
        rows = db.execute("SELECT name FROM t")
    assert rows == [Row(values=("a",), columns=("name",))]


@pytest.mark.integration
def test_insert_returns_empty_list(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT)")
        result = db.execute("INSERT INTO t(id) VALUES (1)")
    assert result == []


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-003-SCN-04")
def test_ddl_returns_empty_list(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        result = db.execute("CREATE TABLE t(id INT)")
    assert result == []


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-003-SCN-05")
def test_multi_statement_returns_final_select(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        rows = db.execute(
            "CREATE TABLE t(id INT); "
            "INSERT INTO t(id) VALUES (1); "
            "SELECT * FROM t"
        )
    assert len(rows) == 1 and rows[0].id == 1


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-003-SCN-06")
def test_parse_error_propagates(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        with pytest.raises(errors.ParseError):
            db.execute("SELECT FROM")


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-003-SCN-07")
def test_execution_error_on_missing_table(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        with pytest.raises(errors.ExecutionError, match="does not exist"):
            db.execute("SELECT * FROM ghost")


# --- REQ-API-004: Row attribute access / iteration / repr / equality --------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-004-SCN-01")
def test_row_attribute_access(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (7, 'alice')")
        row = db.execute("SELECT * FROM t")[0]
    assert row.id == 7 and row.name == "alice"


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-004-SCN-02")
def test_row_iteration_in_schema_order(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'x')")
        row = db.execute("SELECT * FROM t")[0]
    assert list(row) == [1, "x"]


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-004-SCN-03")
def test_row_repr(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'alice')")
        row = db.execute("SELECT * FROM t")[0]
    text = repr(row)
    assert "id=1" in text and "name='alice'" in text


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-004-SCN-04")
def test_row_equality(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT)")
        db.execute("INSERT INTO t(id) VALUES (1)")
        rows = db.execute("SELECT * FROM t")
    assert rows[0] == rows[0]
    assert rows[0] == Row(values=(1,), columns=("id",))
    assert rows[0] != Row(values=(2,), columns=("id",))
    assert rows[0] != Row(values=(1,), columns=("other",))


@pytest.mark.integration
def test_row_unknown_attribute_raises(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT)")
        db.execute("INSERT INTO t(id) VALUES (1)")
        row = db.execute("SELECT * FROM t")[0]
    with pytest.raises(AttributeError):
        row.nonexistent  # noqa: B018


# --- REQ-API-005: tuple unpacking ------------------------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-005-SCN-01")
def test_tuple_unpack_from_row(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'x')")
        row = db.execute("SELECT * FROM t")[0]
    a, b = row
    assert (a, b) == (1, "x")


# --- REQ-API-006: MVP non-ACID contract ------------------------------------


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-006-SCN-01")
def test_database_docstring_mentions_non_acid():
    assert "non-ACID, no crash safety" in Database.__init__.__doc__


@pytest.mark.integration
@pytest.mark.spec_id("REQ-API-006-SCN-02")
def test_database_has_no_transaction_methods():
    for m in ("begin", "commit", "rollback"):
        assert not hasattr(Database, m), f"Database must not have {m}"


# --- Resource lifecycle / Row invariants (Task 20 code-quality follow-up) ---


@pytest.mark.integration
def test_database_close_is_idempotent(tmp_path):
    """Calling ``close()`` twice must not raise; first call flushes+closes,
    second is a no-op against the already-released Pager."""
    p = tmp_path / "db.db"
    db = Database(str(p))
    db.execute("CREATE TABLE t(id INT)")
    db.close()
    db.close()  # idempotent: Pager.close() guards on ``_mmap is not None``
    with Database(str(p)) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert rows == []


@pytest.mark.integration
def test_context_manager_exception_path(tmp_path):
    """``with``-body exception still triggers ``close()`` (and flush) via __exit__."""
    p = tmp_path / "db.db"
    with pytest.raises(RuntimeError, match="user"):
        with Database(str(p)) as db:
            db.execute("CREATE TABLE t(id INT)")
            db.execute("INSERT INTO t(id) VALUES (1)")
            raise RuntimeError("user error")
    # After exception, reopen and verify schema/row was persisted before close
    with Database(str(p)) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert len(rows) == 1 and rows[0].id == 1


@pytest.mark.integration
def test_row_length_mismatch_raises():
    """Row(values, columns) with unequal lengths must raise ValueError immediately."""
    with pytest.raises(ValueError, match="equal lengths"):
        Row(values=(1, 2), columns=("id",))
    with pytest.raises(ValueError, match="equal lengths"):
        Row(values=(1,), columns=("id", "name"))
