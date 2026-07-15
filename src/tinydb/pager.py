"""Slotted-page single-file storage with 4KB pages.

Page 0 is reserved for the file header (magic + schema version).
Page 1 will be reserved for the catalog (Task 12).
Page 2+ are available for table data.
"""
import mmap
import os

from tinydb.errors import InvalidDatabaseFile, UnsupportedSchemaVersion

MAGIC = b'TINYDB\x00\x01'  # 8 bytes
SCHEMA_VERSION = 0x01  # 1 byte
PAGE_SIZE = 4096
HEADER_RESERVED = PAGE_SIZE - len(MAGIC) - 1  # 4087 bytes of zeros after header


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
        self._next_page_id = 2  # page 0 = header, page 1 = catalog (Task 12)

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
            self._file.seek(0)
            self._file.write(
                MAGIC + bytes([SCHEMA_VERSION]) + b"\x00" * HEADER_RESERVED + b"\x00" * PAGE_SIZE
            )
            self._file.flush()
            size = PAGE_SIZE * 2
        else:
            # Existing file: validate header
            self._file.seek(0)
            header = self._file.read(len(MAGIC) + 1)
            if not header.startswith(MAGIC):
                self._file.close()
                self._file = None
                raise InvalidDatabaseFile(
                    f"not a tinydb file (magic={header[:len(MAGIC)]!r})"
                )
            if header[len(MAGIC)] != SCHEMA_VERSION:
                self._file.close()
                self._file = None
                raise UnsupportedSchemaVersion(
                    f"schema_version={header[len(MAGIC)]} not supported (expected {SCHEMA_VERSION})"
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
        # remaining bytes are 0 by default (bytearray is zero-initialized)

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
        """Allocate a new page; returns its id. Grows the file/:memory: buffer as needed."""
        pid = self._next_page_id
        self._next_page_id += 1
        needed_size = (pid + 1) * PAGE_SIZE
        if self._is_memory:
            # :memory: pages are tracked per-page in _mem_pages (Task 7 structure).
            if pid not in self._mem_pages:
                self._mem_pages[pid] = bytearray(PAGE_SIZE)
        else:
            self._file.seek(0, os.SEEK_END)
            current = self._file.tell()
            if needed_size > current:
                self._file.truncate(needed_size)
                self._file.flush()
                # mmap length is fixed at creation; close + remap at new size.
                if self._mmap is not None:
                    self._mmap.close()
                self._file.seek(0)
                self._mmap = mmap.mmap(
                    self._file.fileno(), needed_size, access=mmap.ACCESS_WRITE
                )
        return pid

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