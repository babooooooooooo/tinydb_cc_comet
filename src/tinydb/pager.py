"""Slotted-page single-file storage with 4KB pages.

Page 0 is reserved for the file header (magic + schema version).
Page 1 will be reserved for the catalog (Task 12).
Page 2+ are available for table data.
"""
import mmap
import os

from tinydb.errors import InvalidDatabaseFile, UnsupportedSchemaVersion

MAGIC = b'TINYDB\x00\x02'  # 8 bytes; version byte at offset 7
MAGIC_PREFIX = b'TINYDB\x00'  # first 7 bytes shared across v1/v2 magic
SCHEMA_VERSION = 0x02  # 1 byte
PAGE_SIZE = 4096
FREE_LIST_HEAD_OFFSET = 9  # u32 at bytes 9-12
HEADER_RESERVED = PAGE_SIZE - len(MAGIC) - 1 - 4  # 4083 bytes of zeros after free_list_head


class Pager:
    """File-backed or :memory: page manager.

    Task 7 implements file header (page 0: magic + schema version).
    Page alloc/read/write/close arrive in Task 8.
    """

    def __init__(self, path: str):
        self._path = path
        self._is_memory = path == ":memory:"
        self._file = None
        self._mmap = None
        self._mem_pages: dict[int, bytearray] = {}
        self._next_page_id = 2
        self._free_list_head: int = 0  # page id of free list head, 0 = empty

        if self._is_memory:
            page = self._alloc_page(0)
            self._init_page0(page)
        else:
            self._open_file()

    def _open_file(self) -> None:
        """Open or create the database file; validate header on existing files."""
        is_new = not os.path.exists(self._path)
        self._file = open(self._path, "a+b")  # create if not exist
        self._file.seek(0, os.SEEK_END)
        size = self._file.tell()

        if is_new or size == 0:
            # Fresh file: write header (page 0) + zero-filled catalog slot (page 1).
            # Page 1 is reserved for the catalog (Task 12). Pre-allocating it now
            # guarantees read_page(1) returns 4KB zero bytes on a fresh file
            # without bumping against mmap boundary.
            # Header layout: MAGIC (8) + schema_version (1) + free_list_head u32 (4) + reserved (4083) = PAGE_SIZE
            self._file.seek(0)
            self._file.write(
                MAGIC
                + bytes([SCHEMA_VERSION])
                + b"\x00\x00\x00\x00"  # free_list_head = 0
                + b"\x00" * HEADER_RESERVED
                + b"\x00" * PAGE_SIZE
            )
            self._file.flush()
            size = PAGE_SIZE * 2
        else:
            # Existing file: validate header. The first 7 bytes (MAGIC_PREFIX)
            # are shared across versions; the version byte at offset 7 picks
            # the format. We accept v1 and transparently upgrade to v2.
            self._file.seek(0)
            header = self._file.read(len(MAGIC) + 1)
            if not header.startswith(MAGIC_PREFIX):
                self._file.close()
                self._file = None
                raise InvalidDatabaseFile(
                    f"not a tinydb file (magic={header[:len(MAGIC)]!r})"
                )
            version_byte = header[len(MAGIC_PREFIX)]
            if version_byte == 0x01:
                # Auto-upgrade v1 -> v2: rewrite magic version byte + schema_version +
                # free_list_head in place. Note: the file was opened in "a+b" mode
                # above, which always appends on existing files — so we close and
                # reopen in "r+b" to allow seek+write at arbitrary offsets.
                self._file.close()
                self._file = open(self._path, "r+b")
                self._file.seek(len(MAGIC_PREFIX))
                self._file.write(bytes([0x02]))  # magic version byte
                self._file.write(bytes([SCHEMA_VERSION]))
                self._file.write(b"\x00\x00\x00\x00")  # free_list_head = 0
                self._file.flush()
            elif version_byte != 0x02:
                self._file.close()
                self._file = None
                raise UnsupportedSchemaVersion(
                    f"schema_version={version_byte} not supported (expected {SCHEMA_VERSION})"
                )
            else:
                # v2 file: validate schema_version
                schema_version = header[len(MAGIC_PREFIX) + 1]
                if schema_version != SCHEMA_VERSION:
                    self._file.close()
                    self._file = None
                    raise UnsupportedSchemaVersion(
                        f"schema_version={schema_version} not supported (expected {SCHEMA_VERSION})"
                    )

        # mmap the file for read/write
        self._file.seek(0)
        self._mmap = mmap.mmap(self._file.fileno(), size, access=mmap.ACCESS_WRITE)

        # Reseed next_page_id from current file size: file has page 0 (header) +
        # page 1 (catalog slot) + N data pages. Without this reseed, reopening
        # a file that previously allocated data pages would re-allocate the
        # same page ids and silently overwrite persisted page contents.
        self._next_page_id = max(self._next_page_id, self._mmap.size() // PAGE_SIZE)

    def _alloc_page(self, page_id: int) -> bytearray:
        """Allocate a new page in :memory: mode (Task 8 will generalize)."""
        page = bytearray(PAGE_SIZE)
        self._mem_pages[page_id] = page
        return page

    def _init_page0(self, page: bytearray) -> None:
        """Write the file header to page 0."""
        page[0:len(MAGIC)] = MAGIC
        page[len(MAGIC)] = SCHEMA_VERSION
        page[9:13] = (0).to_bytes(4, "big")  # free_list_head = 0

    def _read_free_list_head(self) -> int:
        """Read free_list_head u32 from page 0."""
        if self._is_memory:
            page = self._mem_pages[0]
            return int.from_bytes(page[9:13], "big")
        return int.from_bytes(self._mmap[9:13], "big")

    def _write_free_list_head(self, head: int) -> None:
        """Write free_list_head u32 to page 0."""
        data = head.to_bytes(4, "big")
        if self._is_memory:
            page = self._mem_pages[0]
            page[9:13] = data
        else:
            self._mmap[9:13] = data

    def page_count(self) -> int:
        """Return the number of pages currently tracked."""
        if self._is_memory:
            return max(len(self._mem_pages), 1)
        if self._mmap is not None:
            return max(self._mmap.size() // PAGE_SIZE, 1)
        return 1

    def close(self) -> None:
        """Release mmap and file handle."""
        if self._mmap is not None:
            try:
                self._mmap.close()
            finally:
                self._mmap = None
        if self._file is not None and not self._file.closed:
            self._file.close()
            self._file = None

    def flush(self) -> None:
        """Flush mmap + file buffers to disk."""
        if self._mmap is not None:
            self._mmap.flush()
        if self._file is not None and not self._file.closed:
            self._file.flush()

    def alloc_page(self) -> int:
        """Allocate a page. Consults free list first; extends file only if list is empty."""
        head = self._read_free_list_head()
        if head != 0:
            # Pop from free list
            page = self.read_page(head)
            next_free = int.from_bytes(page[0:4], "big")
            self._write_free_list_head(next_free)
            # Zero the popped page so stale data doesn't leak.
            if self._is_memory:
                self._mem_pages[head][:] = b"\x00" * PAGE_SIZE
            else:
                zero = b"\x00" * PAGE_SIZE
                off = head * PAGE_SIZE
                self._mmap[off:off + PAGE_SIZE] = zero
            return head

        # No free page; append.
        pid = self._next_page_id
        self._next_page_id += 1
        needed_size = (pid + 1) * PAGE_SIZE
        if self._is_memory:
            if pid not in self._mem_pages:
                self._mem_pages[pid] = bytearray(PAGE_SIZE)
        else:
            self._file.seek(0, os.SEEK_END)
            current = self._file.tell()
            if needed_size > current:
                self._file.truncate(needed_size)
                self._file.flush()
                if self._mmap is not None:
                    self._mmap.close()
                self._file.seek(0)
                self._mmap = mmap.mmap(
                    self._file.fileno(), needed_size, access=mmap.ACCESS_WRITE
                )
        return pid

    def free_page(self, page_id: int) -> None:
        """Return a page to the free list. The page's first 4 bytes are overwritten with next_free pointer."""
        if page_id < 1:
            raise ValueError(f"page_id must be >= 1, got {page_id}")
        head = self._read_free_list_head()
        # Write next_free into the freed page's first 4 bytes
        data = head.to_bytes(4, "big")
        if self._is_memory:
            if page_id not in self._mem_pages:
                self._mem_pages[page_id] = bytearray(PAGE_SIZE)
            self._mem_pages[page_id][0:4] = data
        else:
            off = page_id * PAGE_SIZE
            self._mmap[off:off + 4] = data
        self._write_free_list_head(page_id)

    def read_page(self, page_id: int) -> bytes:
        """Return a copy of the 4KB page contents at the given page id."""
        if page_id < 0:
            raise ValueError(f"page_id must be non-negative, got {page_id}")
        off = page_id * PAGE_SIZE
        if self._is_memory:
            page = self._mem_pages.get(page_id)
            if page is None:
                return b"\x00" * PAGE_SIZE
            return bytes(page)
        return bytes(self._mmap[off:off + PAGE_SIZE])

    def write_page(self, page_id: int, data: bytes) -> None:
        """Write exactly PAGE_SIZE bytes to the given page id."""
        if page_id < 0:
            raise ValueError(f"page_id must be non-negative, got {page_id}")
        if len(data) != PAGE_SIZE:
            raise ValueError(f"page data must be {PAGE_SIZE} bytes, got {len(data)}")
        off = page_id * PAGE_SIZE
        if self._is_memory:
            if page_id not in self._mem_pages:
                self._mem_pages[page_id] = bytearray(PAGE_SIZE)
            self._mem_pages[page_id][:] = data
        else:
            self._mmap[off:off + PAGE_SIZE] = data

    def write_catalog_chain(self, catalog) -> None:
        """Write ``catalog`` as a chain of pages, reclaiming any old chain pages.

        Page 1 is reused as the chain head (never freed — it's a structural
        slot). Any old overflow pages are walked from the previous chain
        and returned to the free list before the new chain is written.

        Implementation note: ``_pack_chain`` emits every page with
        ``next_page_id = 0`` placeholder. We allocate the chain pages in
        order, write each payload, then patch the next_id field on every
        non-tail page to point to the next chain page.
        """
        # Imported here to avoid a circular import (catalog.py imports
        # PAGE_SIZE from pager).
        from tinydb.catalog import CHAIN_HEAD_PAGE, _pack_chain

        # 1. Reclaim any old overflow pages (everything after page 1 in
        #    the previous chain). Page 1 stays — we'll overwrite it below.
        pid = self.read_page(CHAIN_HEAD_PAGE)
        next_id = int.from_bytes(pid[0:4], "big")
        # Defensive loop guard: bound by current page count.
        visited = 0
        page_cap = self.page_count() + 1
        while next_id != 0:
            if visited > page_cap:
                from tinydb.errors import InvalidDatabaseFile
                raise InvalidDatabaseFile(
                    f"catalog chain reclamation exceeds page_count ({page_cap})"
                )
            visited += 1
            cur = next_id
            cur_page = self.read_page(cur)
            next_id = int.from_bytes(cur_page[0:4], "big")
            self.free_page(cur)
        # Reset head's next_id to 0 so a partially-reclaimed state can't
        # mislead a reader that opens between reclaim and rewrite.
        self._write_chain_next(CHAIN_HEAD_PAGE, 0)

        # 2. Pack and write the new chain.
        pages = _pack_chain(catalog)
        # Track the page id of each segment so we can patch next_id later.
        segment_ids: list[int] = []
        for i, payload in enumerate(pages):
            if i == 0:
                target = CHAIN_HEAD_PAGE
            else:
                target = self.alloc_page()
            self.write_page(target, payload)
            segment_ids.append(target)

        # 3. Patch next_id on every non-tail page.
        for i, target in enumerate(segment_ids):
            nxt = segment_ids[i + 1] if i + 1 < len(segment_ids) else 0
            self._write_chain_next(target, nxt)

    def _write_chain_next(self, page_id: int, next_page_id: int) -> None:
        """Write the 4-byte ``next_page_id`` field at offset 0 of ``page_id``."""
        data = next_page_id.to_bytes(4, "big")
        if self._is_memory:
            if page_id not in self._mem_pages:
                self._mem_pages[page_id] = bytearray(PAGE_SIZE)
            self._mem_pages[page_id][0:4] = data
        else:
            off = page_id * PAGE_SIZE
            self._mmap[off:off + 4] = data