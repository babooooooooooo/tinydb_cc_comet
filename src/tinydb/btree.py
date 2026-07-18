"""B+tree index for tinydb. Page-aligned (4KB nodes), fanout=16, tombstone on delete."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from tinydb.pager import PAGE_SIZE

NODE_TYPE_LEAF = 1
NODE_TYPE_INTERNAL = 2

# Page layout:
#   byte 0:      node_type (1 = leaf, 2 = internal)
#   byte 1:      reserved
#   bytes 2-3:   key_count (u16)
#   bytes 4-5:   reserved
#   bytes 6-9:   next_leaf_id (u32; leaf only)
#   bytes 10-4095: payload (4086 bytes)
HEADER_SIZE = 10
PAYLOAD_SIZE = PAGE_SIZE - HEADER_SIZE  # 4086

FANOUT = 16  # max keys per leaf/internal node
# Tombstone flag bit packed into entry metadata byte (1 byte per entry, MSB=tombstone)
TOMBSTONE_FLAG = 0x80


@dataclass
class LeafNode:
    keys: list[bytes]   # variable-width keys; each entry has 1 metadata byte + key bytes
    values: list[tuple[int, int]]  # (page_id, slot_id)
    next_leaf_id: int = 0
    tombstones: list[bool] = field(default_factory=list)

    def key_count(self) -> int:
        return len(self.keys)

    def serialize(self) -> bytes:
        page = bytearray(PAGE_SIZE)
        page[0] = NODE_TYPE_LEAF
        page[1] = 0
        page[2:4] = struct.pack(">H", self.key_count())
        page[4:6] = b"\x00\x00"
        page[6:10] = struct.pack(">I", self.next_leaf_id)
        off = HEADER_SIZE
        for i, key in enumerate(self.keys):
            tomb = TOMBSTONE_FLAG if (i < len(self.tombstones) and self.tombstones[i]) else 0
            meta = bytes([tomb])
            key_len = len(key)
            if off + 1 + 2 + key_len + 8 > PAGE_SIZE:
                raise ValueError(f"leaf overflow at entry {i}")
            page[off] = tomb
            page[off + 1:off + 3] = struct.pack(">H", key_len)
            page[off + 3:off + 3 + key_len] = key
            page[off + 3 + key_len:off + 3 + key_len + 8] = struct.pack(
                ">II", self.values[i][0], self.values[i][1]
            )
            off += 3 + key_len + 8
        return bytes(page)

    @classmethod
    def deserialize(cls, page: bytes) -> "LeafNode":
        assert page[0] == NODE_TYPE_LEAF
        key_count = struct.unpack(">H", page[2:4])[0]
        next_leaf_id = struct.unpack(">I", page[6:10])[0]
        keys: list[bytes] = []
        values: list[tuple[int, int]] = []
        tombstones: list[bool] = []
        off = HEADER_SIZE
        for _ in range(key_count):
            tomb = page[off]
            key_len = struct.unpack(">H", page[off + 1:off + 3])[0]
            key = bytes(page[off + 3:off + 3 + key_len])
            pid, sid = struct.unpack(">II", page[off + 3 + key_len:off + 3 + key_len + 8])
            keys.append(key)
            values.append((pid, sid))
            tombstones.append(bool(tomb & TOMBSTONE_FLAG))
            off += 3 + key_len + 8
        return cls(keys=keys, values=values, next_leaf_id=next_leaf_id, tombstones=tombstones)


@dataclass
class InternalNode:
    keys: list[bytes]
    children: list[int]  # page ids; len = len(keys) + 1

    def key_count(self) -> int:
        return len(self.keys)

    def serialize(self) -> bytes:
        page = bytearray(PAGE_SIZE)
        page[0] = NODE_TYPE_INTERNAL
        page[1] = 0
        page[2:4] = struct.pack(">H", self.key_count())
        page[4:10] = b"\x00\x00\x00\x00\x00\x00"
        off = HEADER_SIZE
        for i, key in enumerate(self.keys):
            key_len = len(key)
            if off + 4 + 2 + key_len > PAGE_SIZE:
                raise ValueError(f"internal overflow at key {i}")
            page[off:off + 4] = struct.pack(">I", self.children[i])
            page[off + 4:off + 6] = struct.pack(">H", key_len)
            page[off + 6:off + 6 + key_len] = key
            off += 6 + key_len
        # Final child pointer
        page[off:off + 4] = struct.pack(">I", self.children[-1])
        return bytes(page)

    @classmethod
    def deserialize(cls, page: bytes) -> "InternalNode":
        assert page[0] == NODE_TYPE_INTERNAL
        key_count = struct.unpack(">H", page[2:4])[0]
        keys: list[bytes] = []
        children: list[int] = []
        off = HEADER_SIZE
        for _ in range(key_count):
            child = struct.unpack(">I", page[off:off + 4])[0]
            key_len = struct.unpack(">H", page[off + 4:off + 6])[0]
            key = bytes(page[off + 6:off + 6 + key_len])
            children.append(child)
            keys.append(key)
            off += 6 + key_len
        children.append(struct.unpack(">I", page[off:off + 4])[0])
        return cls(keys=keys, children=children)


@dataclass
class BTree:
    pager: object  # Pager
    root_page_id: int | None

    def _descend_to_leaf(self, key: bytes) -> tuple[int, "LeafNode"]:
        pid = self.root_page_id
        page = self.pager.read_page(pid)
        while page[0] == NODE_TYPE_INTERNAL:
            node = InternalNode.deserialize(page)
            # Convention: node.keys[i] = smallest key in children[i+1].
            # bisect_right gives the child index that contains K (or would,
            # for K equal to a separator, the right subtree).
            i = self._bisect_right(node.keys, key)
            pid = node.children[i]
            page = self.pager.read_page(pid)
        return pid, LeafNode.deserialize(page)

    def insert(self, key: bytes, value: tuple[int, int]) -> None:
        """Insert (key, value). Same-key replaces (upsert); tombstones cleared on insert."""
        if self.root_page_id is None:
            pid = self.pager.alloc_page()
            leaf = LeafNode(keys=[key], values=[value], next_leaf_id=0, tombstones=[False])
            self.pager.write_page(pid, leaf.serialize())
            self.root_page_id = pid
            return

        leaf_pid, leaf = self._descend_to_leaf(key)
        i = self._bisect_left(leaf.keys, key)
        if i < len(leaf.keys) and leaf.keys[i] == key:
            leaf.keys[i] = key
            leaf.values[i] = value
            if i < len(leaf.tombstones):
                leaf.tombstones[i] = False
            else:
                leaf.tombstones.append(False)
        else:
            leaf.keys.insert(i, key)
            leaf.values.insert(i, value)
            leaf.tombstones.insert(i, False)

        # Try to serialize; if it overflows the page, split.
        try:
            payload = leaf.serialize()
        except ValueError:
            # Split leaf at median.
            mid = len(leaf.keys) // 2
            left = LeafNode(
                keys=leaf.keys[:mid],
                values=leaf.values[:mid],
                next_leaf_id=0,  # patched below
                tombstones=leaf.tombstones[:mid],
            )
            right = LeafNode(
                keys=leaf.keys[mid:],
                values=leaf.values[mid:],
                next_leaf_id=0,
                tombstones=leaf.tombstones[mid:],
            )
            # Allocate new right page; left stays at leaf_pid.
            right_pid = self.pager.alloc_page()
            # Chain left -> right.
            left.next_leaf_id = right_pid
            self.pager.write_page(leaf_pid, left.serialize())
            self.pager.write_page(right_pid, right.serialize())
            promoted_key = right.keys[0]
            # Insert separator into parent.
            self._insert_into_parent(leaf_pid, promoted_key, right_pid)
            return

        self.pager.write_page(leaf_pid, payload)

    def _insert_into_parent(
        self, left_pid: int, key: bytes, right_pid: int
    ) -> None:
        """Insert separator key+right_pid into parent internal node, splitting if needed."""
        if self.root_page_id is None:
            raise RuntimeError("root unexpectedly None")
        # If the split leaf was the root, create a new root.
        if self.root_page_id == left_pid:
            new_root_pid = self.pager.alloc_page()
            internal = InternalNode(keys=[key], children=[left_pid, right_pid])
            self.pager.write_page(new_root_pid, internal.serialize())
            self.root_page_id = new_root_pid
            return
        # Walk down from root tracking parent path until we find left_pid as a child.
        parent_pid = self._find_parent_pid(self.root_page_id, left_pid, key)
        parent_page = self.pager.read_page(parent_pid)
        parent = InternalNode.deserialize(parent_page)
        i = self._bisect_left(parent.keys, key)
        parent.keys.insert(i, key)
        parent.children.insert(i + 1, right_pid)
        try:
            payload = parent.serialize()
        except ValueError:
            # Split internal node at median. Promote the median key.
            mid = len(parent.keys) // 2
            promoted_key = parent.keys[mid]
            left_int = InternalNode(
                keys=parent.keys[:mid], children=parent.children[: mid + 1]
            )
            right_int = InternalNode(
                keys=parent.keys[mid + 1 :], children=parent.children[mid + 1 :]
            )
            self.pager.write_page(parent_pid, left_int.serialize())
            right_pid_int = self.pager.alloc_page()
            self.pager.write_page(right_pid_int, right_int.serialize())
            # Recurse: parent_pid now holds the left half.
            self._insert_into_parent(parent_pid, promoted_key, right_pid_int)
            return
        self.pager.write_page(parent_pid, payload)

    def _find_parent_pid(self, root_pid: int, target_pid: int, key: bytes) -> int:
        """Walk from root until target_pid is the next child to descend into; return parent page id."""
        pid = root_pid
        page = self.pager.read_page(pid)
        while page[0] == NODE_TYPE_INTERNAL:
            node = InternalNode.deserialize(page)
            # Same convention as _descend_to_leaf: keys[i] = smallest key
            # in children[i+1]; bisect_right selects the right subtree.
            i = self._bisect_right(node.keys, key)
            child_pid = node.children[i]
            if child_pid == target_pid:
                return pid
            pid = child_pid
            page = self.pager.read_page(pid)
        raise RuntimeError(
            f"target_pid {target_pid} not found in path from root {root_pid}"
        )

    def search(self, key: bytes) -> tuple[int, int] | None:
        if self.root_page_id is None:
            return None
        _, leaf = self._descend_to_leaf(key)
        i = self._bisect_left(leaf.keys, key)
        if i < len(leaf.keys) and leaf.keys[i] == key and not leaf.tombstones[i]:
            return leaf.values[i]
        return None

    @staticmethod
    def _bisect_left(keys: list[bytes], key: bytes) -> int:
        lo, hi = 0, len(keys)
        while lo < hi:
            mid = (lo + hi) // 2
            if keys[mid] < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    @staticmethod
    def _bisect_right(keys: list[bytes], key: bytes) -> int:
        lo, hi = 0, len(keys)
        while lo < hi:
            mid = (lo + hi) // 2
            if keys[mid] <= key:
                lo = mid + 1
            else:
                hi = mid
        return lo

