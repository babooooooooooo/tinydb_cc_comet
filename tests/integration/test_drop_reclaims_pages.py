"""DROP TABLE page reclamation tests (Task 8 of tinydb-engine-v2).

Verifies that ``DROP TABLE`` puts the dropped table's data + index pages
on the pager's free list, that subsequent CREATE/INSERT cycles reuse the
freed pages (no monotonic growth), and that ``IndexManager`` forgets the
table's B+tree indexes.

Note: ``Pager.page_count()`` returns the file-size high-water mark, so we
cannot assert ``after < before`` directly — instead we read the free list
state from the page-0 header and verify reuse across many cycles.
"""
import os
import tempfile

import tinydb


def test_drop_recycles_data_and_index_pages(tmp_path):
    """After DROP, the table's data + index pages must land on the free list."""
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
        for i in range(50):
            db.execute(f"INSERT INTO t(id, name) VALUES ({i}, 'name{i}')")
        # Free list head lives at page 0 bytes 9:13 (after MAGIC + schema_version).
        before_head = int.from_bytes(db.pager.read_page(0)[9:13], "big")
        assert before_head == 0, "no free pages expected before DROP"
        db.execute("DROP TABLE t")
        after_head = int.from_bytes(db.pager.read_page(0)[9:13], "big")
        assert after_head != 0, (
            "DROP TABLE must push at least one page onto the free list; "
            f"free_list_head is still {after_head}"
        )
        # Walk the free list to confirm it has pages.
        cur = after_head
        walked = 0
        for _ in range(100):  # defensive bound (table had <20 pages)
            if cur == 0:
                break
            walked += 1
            cur = int.from_bytes(db.pager.read_page(cur)[0:4], "big")
        assert walked > 0, "free list head set but no pages walked"


def test_drop_then_reuse_pages_via_recreate(tmp_path):
    """After DROP, recreating a table should reuse freed pages (bounded growth).

    With reclamation: ``page_count`` (file-size high-water mark) stays near
    the warmup baseline across many DROP+CREATE cycles — freed pages are
    pulled back from the free list.

    Without reclamation: every CREATE+INSERT allocates fresh pages, so
    ``page_count`` grows by roughly 5 pages per cycle.
    """
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        # Warmup: get to a stable allocation baseline.
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
        for i in range(50):
            db.execute(f"INSERT INTO t(id, name) VALUES ({i}, 'name{i}')")
        db.execute("DROP TABLE t")
        warmup = db.pager.page_count()
        # 20 cycles of CREATE + INSERT + DROP. With reclamation this reuses
        # freed pages and page_count stays bounded; without reclamation it
        # grows by ~5 pages per cycle (~100 over 20 cycles).
        for _ in range(20):
            db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
            for i in range(50):
                db.execute(f"INSERT INTO t(id, name) VALUES ({i}, 'name{i}')")
            db.execute("DROP TABLE t")
        final = db.pager.page_count()
        assert final - warmup < 30, (
            f"page_count grew by {final - warmup} (warmup={warmup}, final={final}); "
            f"expected < 30 — reclamation should bound growth"
        )


def test_drop_removes_from_index_manager(tmp_path):
    """After DROP, IndexManager must forget B+tree indexes for the table."""
    db_path = str(tmp_path / "test.db")
    with tinydb.Database(db_path) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'alice')")
        # Sanity: lookup works before DROP.
        ref = db.index_manager.lookup_key("t", "id", b"\x00\x00\x00\x01")
        assert ref is not None, "index lookup should work pre-DROP"
        assert ("t", "id") in db.index_manager._indexes
        db.execute("DROP TABLE t")
        # After DROP: lookup returns None (no error).
        ref = db.index_manager.lookup_key("t", "id", b"\x00\x00\x00\x01")
        assert ref is None
        assert ("t", "id") not in db.index_manager._indexes