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
