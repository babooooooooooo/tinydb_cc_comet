"""Single slotted-page layout: header + slot directory + data area growing from page end.

Layout (4 KB page):
  [0:HEADER_SIZE]              page metadata
  [HEADER_SIZE:free_offset]    slot directory (6 bytes/slot)
  [free_offset:data_offset]    free space
  [data_offset:PAGE_SIZE-2]    row bytes (grow BACKWARD from page end)
  [PAGE_SIZE-2:PAGE_SIZE]      data_len marker (uint16, big-endian)

Slot ``offset`` is an index into ``self.data`` (NOT an absolute page offset).
``self.data_offset`` tracks the absolute page offset of ``self.data[0]`` for
free-space accounting; ``to_bytes`` writes ``self.data`` contiguously from
``data_offset`` to ``PAGE_SIZE - 2`` and stashes ``len(self.data)`` in the
trailing 2 bytes so ``from_bytes`` can rebuild ``self.data`` exactly.
"""
from dataclasses import dataclass, field
from typing import Optional

from .errors import PageFull

PAGE_SIZE = 4096
HEADER_SIZE = 16
SLOT_SIZE = 6
NULL_PAGE_ID = 0xFFFFFFFF
FLAG_TOMBSTONE = 0x0001
TOMBSTONE_OFFSET = 0xFFFF  # sentinel offset written into deleted slots
MAX_SLOTS = 32
MAX_INLINE_PAYLOAD = PAGE_SIZE - HEADER_SIZE - 2  # 4096 - 16 - 2 = 4078


@dataclass
class Slot:
    """One slot in the directory. ``offset`` is the index into ``self.data``."""
    offset: int
    length: int
    flags: int


@dataclass
class SlottedPage:
    """In-memory representation of a single 4 KB slotted page."""
    page_id: int
    num_slots: int
    free_offset: int
    overflow_next: int
    slots: list[Slot] = field(default_factory=list)
    data: bytearray = field(default_factory=bytearray)
    # Absolute page offset of ``self.data[0]``. Defaults to ``PAGE_SIZE - 2``
    # for an empty page (data area starts immediately before the 2-byte
    # data_len marker).
    data_offset: int = PAGE_SIZE - 2

    @classmethod
    def empty(cls, page_id: int) -> "SlottedPage":
        """Return a fresh empty page with no slots and an empty data area."""
        return cls(
            page_id=page_id,
            num_slots=0,
            free_offset=HEADER_SIZE,
            overflow_next=NULL_PAGE_ID,
            slots=[],
            data=bytearray(),
            data_offset=PAGE_SIZE - 2,
        )

    # ---- helpers ----

    def _data_start(self) -> int:
        """Absolute page offset where ``self.data[0]`` lives."""
        return self.data_offset

    def _free_space(self) -> int:
        """Bytes between the end of the slot directory and the data area."""
        slot_dir_end = HEADER_SIZE + self.num_slots * SLOT_SIZE
        return self.data_offset - slot_dir_end

    # ---- serde ----

    def to_bytes(self) -> bytes:
        """Serialize this page to exactly ``PAGE_SIZE`` bytes."""
        buf = bytearray(PAGE_SIZE)
        # Header: page_type(1) | num_slots(1) | free_offset(2) | overflow_next(4) | reserved(8)
        buf[0] = 1
        buf[1] = self.num_slots
        buf[2:4] = self.free_offset.to_bytes(2, "big")
        buf[4:8] = self.overflow_next.to_bytes(4, "big")
        # Slot directory: 6 bytes per slot (offset, length, flags).
        for i in range(self.num_slots):
            s = self.slots[i]
            base = HEADER_SIZE + i * SLOT_SIZE
            buf[base:base + 2] = s.offset.to_bytes(2, "big")
            buf[base + 2:base + 4] = s.length.to_bytes(2, "big")
            buf[base + 4:base + 6] = s.flags.to_bytes(2, "big")
        # Data area + trailing data_len marker for roundtrip.
        data_len = len(self.data)
        if data_len > 0:
            buf[PAGE_SIZE - 2 - data_len:PAGE_SIZE - 2] = self.data
        buf[PAGE_SIZE - 2:PAGE_SIZE] = data_len.to_bytes(2, "big")
        return bytes(buf)

    @classmethod
    def from_bytes(cls, page_id: int, raw: bytes) -> "SlottedPage":
        """Deserialize a 4 KB page image back into a ``SlottedPage``."""
        if len(raw) != PAGE_SIZE:
            raise ValueError(f"page must be {PAGE_SIZE} bytes, got {len(raw)}")
        num_slots = raw[1]
        free_offset = int.from_bytes(raw[2:4], "big")
        overflow_next = int.from_bytes(raw[4:8], "big")
        slots: list[Slot] = []
        for i in range(num_slots):
            base = HEADER_SIZE + i * SLOT_SIZE
            slots.append(Slot(
                offset=int.from_bytes(raw[base:base + 2], "big"),
                length=int.from_bytes(raw[base + 2:base + 4], "big"),
                flags=int.from_bytes(raw[base + 4:base + 6], "big"),
            ))
        data_len = int.from_bytes(raw[PAGE_SIZE - 2:PAGE_SIZE], "big")
        data = bytearray(raw[PAGE_SIZE - 2 - data_len:PAGE_SIZE - 2])
        return cls(
            page_id=page_id,
            num_slots=num_slots,
            free_offset=free_offset,
            overflow_next=overflow_next,
            slots=slots,
            data=data,
            data_offset=PAGE_SIZE - 2 - len(data),
        )

    # ---- ops ----

    def insert(self, row_bytes: bytes) -> int:
        """Append ``row_bytes`` and return its slot id.

        Reuses a tombstoned slot if one fits; otherwise appends a fresh slot.
        Raises :class:`PageFull` when neither is possible. The previous row
        bytes from a reused tombstoned slot are leaked in the data area.
        """
        if len(row_bytes) > MAX_INLINE_PAYLOAD:
            raise PageFull(
                f"row {len(row_bytes)} bytes exceeds MAX_INLINE_PAYLOAD "
                f"{MAX_INLINE_PAYLOAD}"
            )
        # Try to reuse a tombstoned slot first (before MAX_SLOTS check).
        for i, s in enumerate(self.slots[:self.num_slots]):
            if (s.flags & FLAG_TOMBSTONE) and len(row_bytes) <= s.length:
                self._append_into_data(row_bytes, s)
                return i
        if self.num_slots >= MAX_SLOTS or self._free_space() < len(row_bytes):
            raise PageFull(
                f"page {self.page_id} full: num_slots={self.num_slots}, "
                f"free_space={self._free_space()}"
            )
        s = Slot(offset=len(self.data), length=len(row_bytes), flags=0)
        self._append_into_data(row_bytes, s)
        self.slots.append(s)
        self.num_slots += 1
        self.free_offset = HEADER_SIZE + self.num_slots * SLOT_SIZE
        return self.num_slots - 1

    def _append_into_data(self, row_bytes: bytes, s: "Slot") -> None:
        """Append row bytes to the data area and repoint ``s`` to the new tail."""
        s.offset = len(self.data)
        s.length = len(row_bytes)
        s.flags = 0
        self.data.extend(row_bytes)
        self.data_offset -= len(row_bytes)

    def delete(self, slot_id: int) -> None:
        """Mark ``slot_id`` as a tombstone (offset = TOMBSTONE_OFFSET)."""
        if slot_id < 0 or slot_id >= self.num_slots:
            raise ValueError(f"slot_id {slot_id} out of range [0, {self.num_slots})")
        s = self.slots[slot_id]
        s.offset = TOMBSTONE_OFFSET
        s.flags = FLAG_TOMBSTONE

    def update(self, slot_id: int, row_bytes: bytes) -> None:
        """Append ``row_bytes`` to the data area and repoint ``slot_id`` to it.

        A longer row raises :class:`PageFull`. The previous row bytes are
        leaked in the data area (compaction is out of scope for MVP).
        """
        if slot_id < 0 or slot_id >= self.num_slots:
            raise ValueError(f"slot_id {slot_id} out of range [0, {self.num_slots})")
        s = self.slots[slot_id]
        if s.flags & FLAG_TOMBSTONE:
            raise ValueError(f"cannot update tombstoned slot {slot_id}")
        if len(row_bytes) > s.length:
            raise PageFull(f"update {len(row_bytes)} > slot capacity {s.length}")
        self._append_into_data(row_bytes, s)

    def get(self, slot_id: int) -> Optional[bytes]:
        """Return row bytes for ``slot_id``, or ``None`` if absent/tombstoned.

        Raises :class:`ValueError` if the slot's offset/length point outside
        the data area (corrupt page).
        """
        if slot_id < 0 or slot_id >= self.num_slots:
            return None
        s = self.slots[slot_id]
        if s.flags & FLAG_TOMBSTONE:
            return None
        if s.offset < 0 or s.offset + s.length > len(self.data):
            raise ValueError(
                f"corrupt slot {slot_id}: offset {s.offset} length "
                f"{s.length} outside data area [0, {len(self.data)})"
            )
        return bytes(self.data[s.offset:s.offset + s.length])
