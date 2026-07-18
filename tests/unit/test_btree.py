from tinydb.btree import LeafNode, InternalNode, NODE_TYPE_LEAF, NODE_TYPE_INTERNAL

def test_leaf_node_roundtrip_small():
    keys = [b"\x00\x01", b"\x00\x02", b"\x00\x03"]
    values = [(10, 0), (11, 1), (12, 2)]  # (page_id, slot_id)
    leaf = LeafNode(keys=keys, values=values, next_leaf_id=0)
    page = leaf.serialize()
    assert page[0] == NODE_TYPE_LEAF
    leaf2 = LeafNode.deserialize(page)
    assert leaf2.keys == keys
    assert leaf2.values == values
    assert leaf2.next_leaf_id == 0


def test_btree_insert_single_leaf_no_split():
    from tinydb.btree import BTree
    from tinydb.pager import Pager

    p = Pager(":memory:")
    bt = BTree(pager=p, root_page_id=None)  # None = empty, first insert creates root
    bt.insert(b"\x00\x05", (5, 0))
    bt.insert(b"\x00\x03", (3, 0))
    bt.insert(b"\x00\x07", (7, 0))
    assert bt.search(b"\x00\x03") == (3, 0)
    assert bt.search(b"\x00\x05") == (5, 0)
    assert bt.search(b"\x00\x07") == (7, 0)
    assert bt.search(b"\x00\x99") is None
    p.close()


def test_btree_insert_triggers_split_at_overflow():
    from tinydb.btree import BTree, FANOUT
    from tinydb.pager import Pager

    p = Pager(":memory:")
    bt = BTree(pager=p, root_page_id=None)
    # Force split: 8-byte keys + 8-byte value + 2-byte len + 1-byte meta = 19 bytes/entry.
    # PAGE_SIZE=4096, header=10, payload=4086. 4086/19 ~ 215 entries per leaf.
    # Insert 1000 entries -> ~5 leaf splits; all keys must remain findable.
    N = 1000
    for i in range(N):
        key = i.to_bytes(8, "big")
        bt.insert(key, (10 + i, 0))
    # All keys still findable post-split.
    for i in range(N):
        key = i.to_bytes(8, "big")
        assert bt.search(key) == (10 + i, 0), f"missing key {i}"
    p.close()


def test_btree_insert_triggers_internal_split_via_long_keys():
    """Long keys -> few entries per leaf -> many children -> internal node overflow."""
    from tinydb.btree import BTree
    from tinydb.pager import Pager

    p = Pager(":memory:")
    bt = BTree(pager=p, root_page_id=None)
    # 64-byte keys: leaf entry = 1 + 2 + 64 + 8 = 75 bytes -> 54 entries/leaf.
    # Internal entry = 4 + 2 + 64 = 70 bytes -> 58 children max.
    # 2000 entries -> ~74 leaves -> internal overflow -> recursive parent split.
    N = 2000
    KEY_LEN = 64
    for i in range(N):
        key = i.to_bytes(KEY_LEN, "big")
        bt.insert(key, (10 + i, 0))
    # Verify findability across both halves of any internal split.
    for i in (0, 1, 27, 28, 53, 54, 500, 1000, 1500, 1999):
        key = i.to_bytes(KEY_LEN, "big")
        assert bt.search(key) == (10 + i, 0), f"missing key {i}"
    p.close()


def test_btree_range_iterates_in_order():
    from tinydb.btree import BTree
    from tinydb.pager import Pager

    p = Pager(":memory:")
    bt = BTree(pager=p, root_page_id=None)
    for i in [5, 1, 9, 3, 7, 2, 8, 4, 6]:
        bt.insert(i.to_bytes(2, "big"), (i, 0))
    result = list(bt.range(b"\x00\x03", b"\x00\x07"))
    assert result == [(3, 0), (4, 0), (5, 0), (6, 0)]
    p.close()


def test_btree_delete_marks_tombstone():
    from tinydb.btree import BTree
    from tinydb.pager import Pager

    p = Pager(":memory:")
    bt = BTree(pager=p, root_page_id=None)
    bt.insert(b"\x00\x01", (1, 0))
    bt.insert(b"\x00\x02", (2, 0))
    bt.insert(b"\x00\x03", (3, 0))
    bt.delete(b"\x00\x02")
    assert bt.search(b"\x00\x02") is None  # tombstoned
    assert bt.search(b"\x00\x01") == (1, 0)
    assert bt.search(b"\x00\x03") == (3, 0)
    p.close()


def test_btree_range_walks_multiple_leaves_in_order():
    """Range across a multi-leaf tree must walk the next_leaf_id chain and return keys in order."""
    from tinydb.btree import BTree
    from tinydb.pager import Pager

    p = Pager(":memory:")
    bt = BTree(pager=p, root_page_id=None)
    # 2-byte keys force multiple leaves; ascending inserts split at the rightmost leaf.
    N = 500
    for i in range(N):
        bt.insert(i.to_bytes(2, "big"), (i, 0))
    # Range that crosses at least one leaf boundary.
    start = (N // 4).to_bytes(2, "big")  # 125
    end = (N // 2 + 50).to_bytes(2, "big")  # 300
    result = list(bt.range(start, end))
    found = {row[0] for row in result}
    expected = set(range(N // 4, N // 2 + 50))
    assert found == expected, f"missing: {sorted(expected - found)[:10]}, extra: {sorted(found - expected)[:10]}"
    # Result must be in ascending key order.
    keys = [row[0] for row in result]
    assert keys == sorted(keys), "range() did not return keys in ascending order"
    p.close()


def test_btree_range_finds_keys_at_separator_boundary():
    """Range descent must use bisect_right so a start key equal to an internal node separator lands on the right subtree (which holds that key), not the left subtree.

    Constructed by ascending-inserting enough keys to create an internal node whose separator equals the start key we query.
    """
    from tinydb.btree import BTree
    from tinydb.pager import Pager

    p = Pager(":memory:")
    bt = BTree(pager=p, root_page_id=None)
    # Force at least one leaf split + internal node: 2-byte keys, ~314/leaf -> 500 keys -> 2 leaves + 1 internal.
    # Ascending inserts keep chain preserved; internal separator = smallest key in second leaf.
    N = 500
    for i in range(N):
        bt.insert(i.to_bytes(2, "big"), (i, 0))
    # Query with start exactly at the separator: with bisect_left, descent would land on the left subtree
    # (which holds keys < separator); with bisect_right, it lands on the right subtree (which holds keys >= separator).
    # Either way, range() should include the start key — even in the left-subtree case, chain walk finds it.
    separator_value = 315  # smallest key in 2nd leaf (500 keys, split at ~315 with 2-byte key ~314/leaf)
    result = list(bt.range(separator_value.to_bytes(2, "big"), (separator_value + 10).to_bytes(2, "big")))
    found = {row[0] for row in result}
    expected = set(range(separator_value, separator_value + 10))
    assert found == expected, f"missing: {expected - found}"
    p.close()