import pytest

from tinydb import Database
from tinydb.errors import ConstraintViolation


@pytest.mark.integration
def test_executor_insert_rejects_null_on_not_null(tmp_path):
    with Database(str(tmp_path / "nn.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL, name TEXT)")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id, name) VALUES (NULL, 'a')")
    assert exc_info.value.kind == "null"
    assert exc_info.value.column == "id"


@pytest.mark.integration
def test_executor_insert_rejects_null_on_pk(tmp_path):
    # MVP legacy compatibility: nullable=True default, but PK must still
    # reject NULL (D5 合并).
    with Database(str(tmp_path / "pk.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY)")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id) VALUES (NULL)")
    assert exc_info.value.kind == "null"
    assert exc_info.value.column == "id"


@pytest.mark.integration
def test_executor_insert_accepts_null_on_nullable_column(tmp_path):
    with Database(str(tmp_path / "ok.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, NULL)")
        rows = db.execute("SELECT * FROM t")
    assert rows[0].name is None


@pytest.mark.integration
def test_executor_insert_failed_null_does_not_write_row(tmp_path):
    with Database(str(tmp_path / "nw.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL)")
        with pytest.raises(ConstraintViolation):
            db.execute("INSERT INTO t(id) VALUES (NULL)")
        rows = db.execute("SELECT * FROM t")
    assert rows == []


@pytest.mark.integration
def test_executor_insert_rejects_duplicate_unique(tmp_path):
    with Database(str(tmp_path / "uq.db")) as db:
        db.execute("CREATE TABLE t(id INT, email TEXT UNIQUE)")
        db.execute("INSERT INTO t(id, email) VALUES (1, 'a@x')")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id, email) VALUES (2, 'a@x')")
    assert exc_info.value.kind == "unique"
    assert exc_info.value.columns == ("email",)


@pytest.mark.integration
def test_executor_insert_rejects_duplicate_pk(tmp_path):
    with Database(str(tmp_path / "dpk.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id, name) VALUES (1, 'b')")
    assert exc_info.value.kind == "duplicate_pk"
    assert exc_info.value.columns == ("id",)


@pytest.mark.integration
def test_executor_insert_unique_with_nulls_all_pass(tmp_path):
    with Database(str(tmp_path / "un.db")) as db:
        db.execute("CREATE TABLE t(id INT, email TEXT UNIQUE)")
        db.execute("INSERT INTO t(id, email) VALUES (1, NULL)")
        db.execute("INSERT INTO t(id, email) VALUES (2, NULL)")
        db.execute("INSERT INTO t(id, email) VALUES (3, NULL)")
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 3


@pytest.mark.integration
def test_executor_insert_same_batch_duplicate_rejected(tmp_path):
    with Database(str(tmp_path / "sb.db")) as db:
        db.execute("CREATE TABLE t(id INT, email TEXT UNIQUE)")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute(
                "INSERT INTO t(id, email) VALUES (1, 'a@x'), (2, 'a@x')"
            )
    assert exc_info.value.kind == "unique"


@pytest.mark.integration
def test_executor_insert_composite_pk_rejected(tmp_path):
    with Database(str(tmp_path / "cpk.db")) as db:
        # Per-column PRIMARY KEY (multi-column composite PK isn't yet
        # supported at the parser level — exercised separately by a
        # parser-level test).
        db.execute("CREATE TABLE t(a INT PRIMARY KEY, b INT)")
        db.execute("INSERT INTO t(a, b) VALUES (1, 1)")
        db.execute("INSERT INTO t(a, b) VALUES (2, 2)")
        # A second row with same 'a' must violate the PK.
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(a, b) VALUES (1, 3)")
    assert exc_info.value.kind == "duplicate_pk"


@pytest.mark.integration
def test_executor_insert_omitted_column_becomes_none(tmp_path):
    with Database(str(tmp_path / "om.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL, name TEXT)")
        db.execute("INSERT INTO t(id) VALUES (1)")
        rows = db.execute("SELECT * FROM t")
    assert rows[0].name is None
    assert rows[0].id == 1


@pytest.mark.integration
def test_executor_insert_unknown_column_rejected(tmp_path):
    with Database(str(tmp_path / "uc.db")) as db:
        db.execute("CREATE TABLE t(id INT)")
        with pytest.raises(Exception) as exc_info:
            db.execute("INSERT INTO t(missing) VALUES (1)")
    # parser also catches this; executor is the second line of defense.
    assert "unknown column" in str(exc_info.value) or "missing" in str(exc_info.value)


@pytest.mark.integration
def test_executor_insert_duplicate_column_rejected(tmp_path):
    with Database(str(tmp_path / "dc.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        with pytest.raises(Exception) as exc_info:
            db.execute("INSERT INTO t(id, id) VALUES (1, 2)")
    assert "duplicate" in str(exc_info.value)


@pytest.mark.integration
def test_executor_insert_multi_row_partial_failure_atomic_no_rows_committed(tmp_path):
    """Atomic semantics: a multi-row INSERT that fails on any row commits nothing.

    Per `tinydb-acid` plan: multi-row INSERT runs under an implicit autocommit
    transaction. A failure on any row (here: PK collision on the second row)
    rolls back the entire statement — the row already written earlier in the
    SAME INSERT (the third SQL tuple) is discarded along with the failing one.
    Rows already committed by PREVIOUS statements (id=1, id=2) are unaffected
    because they ran in their own implicit transactions.
    """
    with Database(str(tmp_path / "mr.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
        # First INSERT runs in its own autocommit txn and commits id=1, id=2.
        db.execute(
            "INSERT INTO t(id, name) VALUES (1, 'a'), (2, 'b')"
        )
        # Second INSERT collides on PK (row id=1 already exists). All three
        # rows in this statement (3, 1, 4) must be discarded atomically.
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute(
                "INSERT INTO t(id, name) VALUES (3, 'c'), (1, 'd'), (4, 'e')"
            )
        assert exc_info.value.kind == "duplicate_pk"
        rows = db.execute("SELECT * FROM t")
    # Only rows from prior committed statements remain — no partial state
    # from the rolled-back INSERT.
    assert sorted(r.id for r in rows) == [1, 2]
