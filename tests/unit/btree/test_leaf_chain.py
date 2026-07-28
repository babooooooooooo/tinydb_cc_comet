"""T2: B+tree leaf chain integrity — Design Doc §2.3 stress tests.

When a leaf is split, the new right leaf must be chained into the original
right-neighbor slot. Without the patch, range() returns only the first leaf
because the new right leaf's ``next_leaf_id`` stays 0 and the chain terminates
early.
"""
import random

from tinydb.btree import BTree
from tinydb.pager import Pager


def test_split_preserves_chain_to_original_right_neighbor(tmp_path):
    """Random 3000-row insert + 3+ leaf splits: range scan hits every key."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    N = 3000
    # Map each unique key string to a unique (page_id, slot_id) value so we
    # can detect lost entries by comparing value sets.
    key_to_value = {str(i).encode(): (1, i) for i in range(N)}
    keys = list(key_to_value.keys())
    random.shuffle(keys)
    for k in keys:
        bt.insert(k, key_to_value[k])

    found = bt.range(b"\x00", b"\xff")
    seen_payloads = {v for v in found}
    assert len(seen_payloads) == len(key_to_value), (
        f"range() lost keys: inserted {len(key_to_value)}, found {len(seen_payloads)}"
    )
    assert set(seen_payloads) == set(key_to_value.values()), (
        "range() returned unexpected values"
    )
    assert len(found) == len(key_to_value), (
        f"range() returned duplicates or missing: {len(found)} vs {len(key_to_value)}"
    )
    pager.close()


def test_reverse_range_scan_after_multi_split(tmp_path):
    """Descending insert forces multiple leaf splits; forward range scan still returns all 500."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    expected_values = set()
    for i in range(500, 0, -1):
        key = str(i).encode()
        bt.insert(key, (1, i))
        expected_values.add((1, i))

    result = bt.range(b"0", b"999")
    assert len(result) == 500, (
        f"reverse insert lost keys: expected 500, got {len(result)}"
    )
    assert {v for v in result} == expected_values, "range() returned wrong values"
    pager.close()


def test_split_chain_rightmost_leaf_no_regression(tmp_path):
    """Rightmost leaf split (original next=0) must yield chain ending at 0; all 200 keys hit."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    for i in range(200):
        bt.insert(str(i).zfill(4).encode(), (1, i))

    result = bt.range(b"0000", b"9999")
    assert len(result) == 200, f"rightmost split lost keys: expected 200, got {len(result)}"
    assert {v for v in result} == {(1, i) for i in range(200)}
    pager.close()
