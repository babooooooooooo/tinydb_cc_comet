"""Unit tests for the pager's free list (v2 header field).

See plan Task 1: free list alloc/free cycle must recycle the same page id.
"""
from tinydb.pager import Pager


def test_alloc_then_free_then_alloc_recycles_same_page(tmp_path):
    db = tmp_path / "test.db"
    p = Pager(str(db))
    pid = p.alloc_page()
    p.flush()
    p.free_page(pid)
    p.flush()
    pid2 = p.alloc_page()
    assert pid2 == pid  # free list returned the same page
    p.close()
