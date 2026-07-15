"""Single slotted-page layout: header + slot directory + data area growing from page end.

Layout (4 KB page):
  [0:HEADER_SIZE]            page metadata
  [HEADER_SIZE:free_offset]  slot directory (each slot = 6 bytes)
  [free_offset:data_start]   free space
  [data_start:data_end]      row bytes (grow BACKWARD from page end)
  [data_end:PAGE_SIZE - 2]   unused slack
  [PAGE_SIZE - 2:PAGE_SIZE]  data_len marker (uint16, big-endian) for roundtrip

Slot offsets are stored as indices relative to ``self.data`` (not absolute page
offsets). This keeps ``insert``/``get`` simple; ``to_bytes`` writes the full
data buffer contiguously from the page-end inward and stashes ``len(data)``
in the trailing 2 bytes so ``from_bytes`` can rebuild ``self.data`` exactly.
"""
from dataclasses import dataclass, field
from typing import Optional

PAGE_SIZE = 4096
HEADER_SIZE = 16
SLOT_SIZE = 6
NULL_PAGE_ID = 0xFFFFFFFF
FLAG_TOMBSTONE = 0x0001
MAX_SLOTS = 32


@dataclass
class Slot:
    """One slot in the directory. ``offset`` is index into ``self.data``."""
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

    @classmethod
    def empty(cls, page_id: int) -> "SlottedPage":
        """Return a fresh empty page with no slots and data area empty."""
        return cls(
            page_id=page_id,
            num_slots=0,
            free_offset=HEADER_SIZE,
            overflow_next=NULL_PAGE_ID,
            slots=[],
            data=bytearray(),
        )

    def to_bytes(self) -> bytes:
        """Serialize this page to exactly ``PAGE_SIZE`` bytes."""
        buf = bytearray(PAGE_SIZE)
        # Header (16 bytes, big-endian):
        #   [0]    page_type = 1 (data page)
        #   [1]    num_slots (uint8)
        #   [2:4]  free_offset (uint16)
        #   [4:8]  overflow_next (uint32, NULL_PAGE_ID if none)
        #   [8:16] reserved (zeros)
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
        # Data area grows from end of page; store data_len in trailing 2 bytes
        # so ``from_bytes`` can rebuild ``self.data`` without ambiguity.
        data_len = len(self.data)
        if data_len > 0:
            buf[PAGE_SIZE - 2 - data_len:PAGE_SIZE - 2] = self.data
        buf[PAGE_SIZE - 2:PAGE_SIZE] = data_len.to_bytes(2, "big")
        return bytes(buf)

    @classmethod
    def from_bytes(cls, page_id: int, raw: bytes) -> "SlottedPage":
        """Deserialize a 4 KB page image back into a ``SlottedPage``."""
        if len(raw) != PAGE_SIZE:
            raise ValueError(
                f"page must be {PAGE_SIZE} bytes, got {len(raw)}"
            )
        num_slots = raw[1]
        free_offset = int.from_bytes(raw[2:4], "big")
        overflow_next = int.from_bytes(raw[4:8], "big")
        slots: list[Slot] = []
        for i in range(num_slots):
            base = HEADER_SIZE + i * SLOT_SIZE
            offset = int.from_bytes(raw[base:base + 2], "big")
            length = int.from_bytes(raw[base + 2:base + 4], "big")
            flags = int.from_bytes(raw[base + 4:base + 6], "big")
            slots.append(Slot(offset=offset, length=length, flags=flags))
        data_len = int.from_bytes(raw[PAGE_SIZE - 2:PAGE_SIZE], "big")
        data = bytearray(raw[PAGE_SIZE - 2 - data_len:PAGE_SIZE - 2])
        return cls(
            page_id=page_id,
            num_slots=num_slots,
            free_offset=free_offset,
            overflow_next=overflow_next,
            slots=slots,
            data=data,
        )

    def insert(self, row_bytes: bytes) -> int:
        """Append ``row_bytes`` to the data area and return its slot id.

        Task 9 scope: enough to make ``insert/get`` round-trip through
        ``to_bytes``/``from_bytes``. Fragmentation, spillover, and ``PageFull``
        handling arrive in Task 10.
        """
        sid = self.num_slots
        relative_offset = len(self.data)
        self.slots.append(Slot(offset=relative_offset, length=len(row_bytes), flags=0))
        self.data.extend(row_bytes)
        self.num_slots += 1
        self.free_offset = HEADER_SIZE + self.num_slots * SLOT_SIZE
        return sid

    def get(self, slot_id: int) -> Optional[bytes]:
        """Return row bytes for ``slot_id``, or ``None`` if absent/tombstoned."""
        if slot_id < 0 or slot_id >= self.num_slots:
            return None
        s = self.slots[slot_id]
        if s.flags & FLAG_TOMBSTONE:
            return None
        return bytes(self.data[s.offset:s.offset + s.length])
