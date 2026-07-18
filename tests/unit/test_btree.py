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