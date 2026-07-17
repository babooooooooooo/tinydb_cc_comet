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
