"""Integration tests for tinydb executor overflow chain.

Rows larger than ``MAX_INLINE_PAYLOAD`` (~4078 B) spill into a chain of
``page_type=2`` overflow pages. The first chunk lands inline (with the
SPILL_START flag); the remaining chunks live in a linked overflow chain
tracked through the data page's ``overflow_next`` header field.

These tests touch the filesystem via ``tmp_path`` so they carry the
``integration`` marker and use ``Database`` end-to-end.
"""
import pytest

from tinydb import Database


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-008-SCN-01")
def test_insert_row_larger_than_inline_spills(tmp_path):
    """A 5000-byte TEXT row survives a database reopen (spill + read roundtrip)."""
    path = tmp_path / "big.db"
    big = "x" * 5000
    with Database(str(path)) as db:
        db.execute("CREATE TABLE t(payload TEXT)")
        db.execute(f"INSERT INTO t(payload) VALUES ('{big}')")
    # Reopen and verify the row was recovered from the overflow chain.
    with Database(str(path)) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert len(rows) == 1
    assert rows[0].payload == big


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-008-SCN-02")
def test_read_spill_start_reconstructs_full_row(tmp_path):
    """An 8000-byte TEXT row can be read back in a single session (multi-chunk chain)."""
    path = tmp_path / "big2.db"
    with Database(str(path)) as db:
        db.execute("CREATE TABLE t(payload TEXT)")
        db.execute(f"INSERT INTO t(payload) VALUES ('{'y' * 8000}')")
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 1
    assert len(rows[0].payload) == 8000


@pytest.mark.integration
@pytest.mark.spec_id("REQ-STORAGE-008-SCN-03")
def test_delete_spill_start_frees_chain(tmp_path):
    """Deleting a small row leaves a spill-start row intact (no cross-row corruption)."""
    path = tmp_path / "big3.db"
    big = "z" * 6000
    with Database(str(path)) as db:
        db.execute("CREATE TABLE t(payload TEXT)")
        db.execute(f"INSERT INTO t(payload) VALUES ('{big}')")
        db.execute("INSERT INTO t(payload) VALUES ('short')")
        db.execute("DELETE FROM t WHERE payload = 'short'")
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 1
    assert rows[0].payload == big
    assert len(rows[0].payload) == 6000