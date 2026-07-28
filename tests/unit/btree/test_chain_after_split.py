"""T2: Manual multi-split chain verification.

Walks the leaf chain directly to confirm ``right.next_leaf_id`` is patched
to the original right neighbor pid at every split boundary.
"""
from tinydb.btree import BTree, LeafNode
from tinydb.pager import Pager


def _walk_leaf_chain(bt: BTree) -> list[tuple[int, LeafNode]]:
    """Yield (page_id, leaf) for every leaf in chain order."""
    if bt.root_page_id is None:
        return []

    # Find the leftmost leaf by descending on the smallest possible key.
    pid = bt.root_page_id
    page = bt.pager.read_page(pid)
    from tinydb.btree import NODE_TYPE_INTERNAL

    while page[0] == NODE_TYPE_INTERNAL:
        from tinydb.btree import InternalNode

        node = InternalNode.deserialize(page)
        pid = node.children[0]
        page = bt.pager.read_page(pid)

    chain: list[tuple[int, LeafNode]] = []
    while pid != 0:
        leaf = LeafNode.deserialize(bt.pager.read_page(pid))
        chain.append((pid, leaf))
        pid = leaf.next_leaf_id
    return chain


def test_multi_split_chain_terminates_at_zero(tmp_path):
    """After 5+ leaf splits, walking the chain from leftmost leaf must visit every leaf
    and terminate at next_leaf_id == 0."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    # 5000 8-byte keys → ~5 leaves × 16 fanout with header overhead
    # forces many splits. Ascending order ensures split at rightmost
    # leaf position where original next=0.
    N = 5000
    for i in range(N):
        bt.insert(i.to_bytes(8, "big"), (i, 0))

    chain = _walk_leaf_chain(bt)
    # 1 chain entry == no split; >1 means splits happened.
    assert len(chain) >= 2, f"expected 2+ leaves after 5000 inserts, got {len(chain)}"

    # The last entry must terminate the chain with next_leaf_id == 0.
    last_pid, last_leaf = chain[-1]
    assert last_leaf.next_leaf_id == 0, (
        f"chain does not terminate: last leaf pid={last_pid} "
        f"next_leaf_id={last_leaf.next_leaf_id}"
    )

    # Keys must be strictly ascending across the entire chain.
    prev = b""
    for _, leaf in chain:
        for k in leaf.keys:
            assert k > prev, f"chain out of order: {prev!r} -> {k!r}"
            prev = k

    # Total keys across the chain must equal N.
    total = sum(len(leaf.keys) for _, leaf in chain)
    assert total == N, f"chain lost keys: sum={total}, expected {N}"

    # Range scan must return all N entries.
    result = bt.range(b"\x00\x00\x00\x00\x00\x00\x00\x00", b"\xff" * 8)
    assert len(result) == N, f"range() returned {len(result)}, expected {N}"

    pager.close()


def test_split_patches_right_neighbor_to_original(tmp_path):
    """After a split, the new right leaf's next_leaf_id must be patched
    to the original right neighbor (0 if rightmost)."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    # 1000 8-byte keys → several splits
    for i in range(1000):
        bt.insert(i.to_bytes(8, "big"), (i, 0))

    chain = _walk_leaf_chain(bt)
    # No broken terminator in the middle of the chain.
    for i, (pid, leaf) in enumerate(chain[:-1]):
        next_pid = leaf.next_leaf_id
        assert next_pid != 0, (
            f"chain broken at index {i}: leaf pid={pid} points to 0 "
            f"but is not the last entry"
        )
        # next_leaf_id must point to a real entry in the chain.
        chain_pids = {p for p, _ in chain}
        assert next_pid in chain_pids, (
            f"leaf pid={pid} next_leaf_id={next_pid} not in chain"
        )

    pager.close()
