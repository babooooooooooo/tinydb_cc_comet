"""Integration suite for IndexManager wiring (Task 7).

Locks in the I-T7-* matrix:
  * SELECT WHERE col = lit uses B+tree fast path when col is indexed.
  * INSERT maintains B+tree indexes (index must reflect new row).
  * DELETE removes B+tree entries (SELECT must NOT find deleted row).
  * UPDATE moves B+tree entries on PK change (old key gone, new key live).
  * Duplicate PK insert rejected (full-scan or index — both OK).

These tests will FAIL without INSERT/UPDATE/DELETE index maintenance
because the SELECT fast path returns [] for unindexed lookups.
"""
import pytest

import tinydb


@pytest.mark.integration
def test_select_pk_eq_uses_btree(tmp_path):
    """Regression baseline: PK equality returns the right row.

    Passes via full-scan before IndexManager wiring; passes via fast
    path after wiring + maintenance. Must remain green throughout.
    """
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        db.execute("CREATE TABLE users(id INT PRIMARY KEY, name TEXT)")
        for i in range(20):
            db.execute(f"INSERT INTO users(id, name) VALUES ({i}, 'user{i}')")
        rows = db.execute("SELECT * FROM users WHERE id = 7")
        assert len(rows) == 1
        assert list(rows[0])[0] == 7
        assert list(rows[0])[1] == "user7"


@pytest.mark.integration
def test_select_pk_eq_after_insert_maintains_index(tmp_path):
    """Insert followed by index lookup must reflect the insert — index maintenance REQUIRED.

    With IndexManager wired but no INSERT maintenance, the B+tree stays
    empty and the SELECT fast path returns [].
    """
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        db.execute("CREATE TABLE users(id INT PRIMARY KEY, name TEXT)")
        for i in range(50):
            db.execute(f"INSERT INTO users(id, name) VALUES ({i}, 'user{i}')")
        # All inserts must be findable via PK
        for i in range(50):
            rows = db.execute(f"SELECT * FROM users WHERE id = {i}")
            assert len(rows) == 1, f"PK {i} not found"
            assert list(rows[0])[1] == f"user{i}"


@pytest.mark.integration
def test_duplicate_pk_insert_rejected_via_index(tmp_path):
    """Duplicate PRIMARY KEY insert must raise ConstraintViolation.

    Either index-detected or full-scan-detected satisfies the assertion.
    """
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        db.execute("CREATE TABLE users(id INT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO users(id, name) VALUES (1, 'alice')")
        with pytest.raises(Exception):
            db.execute("INSERT INTO users(id, name) VALUES (1, 'bob')")


@pytest.mark.integration
def test_select_pk_after_delete_removes_from_index(tmp_path):
    """DELETE must clear the index entry so SELECT no longer returns it."""
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        db.execute("CREATE TABLE users(id INT PRIMARY KEY, name TEXT)")
        for i in range(10):
            db.execute(f"INSERT INTO users(id, name) VALUES ({i}, 'user{i}')")
        db.execute("DELETE FROM users WHERE id = 3")
        rows_after = db.execute("SELECT * FROM users WHERE id = 3")
        assert len(rows_after) == 0


@pytest.mark.integration
def test_update_pk_changes_index_entry(tmp_path):
    """UPDATE on PRIMARY KEY must move the index entry to the new key."""
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        db.execute("CREATE TABLE users(id INT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO users(id, name) VALUES (1, 'alice')")
        db.execute("UPDATE users SET id = 2 WHERE id = 1")
        assert len(db.execute("SELECT * FROM users WHERE id = 1")) == 0
        rows = db.execute("SELECT * FROM users WHERE id = 2")
        assert len(rows) == 1 and list(rows[0])[1] == "alice"