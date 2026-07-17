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
