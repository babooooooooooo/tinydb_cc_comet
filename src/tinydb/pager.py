"""Slotted-page single-file storage with 4KB pages + WAL integration.

Page 0 is reserved for the file header (magic + schema version).
Page 1 will be reserved for the catalog (Task 12).
Page 2+ are available for table data.

Schema v3 (Task 2 of tinydb-acid): adds inline WAL integration. The schema
byte at offset 8 is now 0x03; on first open of a v2 file (byte 8 = 0x02)
with no WAL residue, the header is bumped in place. v2 files with WAL
residue require an explicit migration path and raise SchemaMismatch.
"""
import mmap
import os

from tinydb.errors import InvalidDatabaseFile, SchemaMismatch, UnsupportedSchemaVersion
from tinydb.wal import Wal

MAGIC = b'TINYDB\x00\x03'  # 8 bytes; version byte at offset 7
MAGIC_PREFIX = b'TINYDB\x00'  # first 7 bytes shared across v1/v2/v3 magic
SCHEMA_VERSION = 0x03  # 1 byte
PAGE_SIZE = 4096
FREE_LIST_HEAD_OFFSET = 9  # u32 at bytes 9-12
HEADER_RESERVED = PAGE_SIZE - len(MAGIC) - 1 - 4  # 4083 bytes of zeros after free_list_head


class Pager:
    """File-backed or :memory: page manager with WAL integration (schema v3).

    Task 7 implements file header (page 0: magic + schema version).
    Page alloc/read/write/close arrive in Task 8.
    Task 2 of tinydb-acid: bumps schema_version to 0x03 and exposes WAL
    integration methods (``wal_append_*``, ``write_main_page``, ``fsync_main``).
    """

    def __init__(self, path: str):
        self._path = path
        self._is_memory = path == ":memory:"
        self._file = None
        self._mmap = None
        self._mem_pages: dict[int, bytearray] = {}
        self._next_page_id = 2
        self._free_list_head: int = 0  # page id of free list head, 0 = empty
        # WAL integration (Task 2). Lazy-initialized via ``_get_or_open_wal``.
        self._wal: "Wal | None" = None
        self._wal_path: str | None = None

        if self._is_memory:
            page = self._alloc_page(0)
            self._init_page0(page)
        else:
            self._open_file()
            self._init_wal()

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
            # the format. Auto-upgrade v1 -> v3 and v2 (no-WAL) -> v3 in place.
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
                # Auto-upgrade v1 -> v3: rewrite magic version byte + schema_version +
                # free_list_head in place. Note: the file was opened in "a+b" mode
                # above, which always appends on existing files — so we close and
                # reopen in "r+b" to allow seek+write at arbitrary offsets.
                self._file.close()
                self._file = open(self._path, "r+b")
                self._file.seek(len(MAGIC_PREFIX))
                self._file.write(bytes([0x03]))  # magic version byte -> v3
                self._file.write(bytes([SCHEMA_VERSION]))  # schema_version = 0x03
                self._file.write(b"\x00\x00\x00\x00")  # free_list_head = 0
                self._file.flush()
            elif version_byte == 0x02:
                # v2 file: check schema byte. If still 0x02, either auto-upgrade
                # (no WAL residue) or raise SchemaMismatch (WAL residue present).
                schema_version = header[len(MAGIC_PREFIX) + 1]
                if schema_version == SCHEMA_VERSION:
                    pass  # Already v3 — fall through.
                elif schema_version == 0x02:
                    wal_path = self._path + ".wal"
                    if os.path.exists(wal_path):
                        self._file.close()
                        self._file = None
                        raise SchemaMismatch(
                            f"db file {self._path!r} is schema 0x02 with WAL residue; "
                            f"call migrate_v2_to_v3(path) before opening"
                        )
                    # No WAL residue: safe to bump header byte 8 to 0x03 in place.
                    self._file.close()
                    self._file = open(self._path, "r+b")
                    self._file.seek(len(MAGIC_PREFIX) + 1)
                    self._file.write(bytes([SCHEMA_VERSION]))  # 0x02 -> 0x03
                    self._file.flush()
                else:
                    self._file.close()
                    self._file = None
                    raise UnsupportedSchemaVersion(
                        f"schema_version={schema_version} not supported (expected {SCHEMA_VERSION})"
                    )
            elif version_byte == 0x03:
                schema_version = header[len(MAGIC_PREFIX) + 1]
                if schema_version != SCHEMA_VERSION:
                    self._file.close()
                    self._file = None
                    raise UnsupportedSchemaVersion(
                        f"schema_version={schema_version} not supported (expected {SCHEMA_VERSION})"
                    )
            else:
                self._file.close()
                self._file = None
                raise UnsupportedSchemaVersion(
                    f"magic version={version_byte} not supported (expected 0x03)"
                )

        # The file was opened in "a+b" (so creation on first open works), but
        # POSIX append mode forces every write to the end of file regardless
        # of seek(). We need ``r+b`` so :meth:`write_main_page` honors its
        # seek offsets. Skip the reopen for existing files that were already
        # upgraded above (their close+reopen left them in "r+b" mode).
        if self._file is not None and "a" in str(self._file.mode):
            self._file.close()
            self._file = open(self._path, "r+b")

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
        """Release mmap, file handle, and any open WAL handle."""
        if self._wal is not None:
            try:
                self._wal.close()
            finally:
                self._wal = None
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

    # ------------------------------------------------------------------
    # WAL integration (Task 2 of tinydb-acid).
    # ------------------------------------------------------------------
    def _init_wal(self) -> None:
        """Initialize WAL handle (no-op for :memory:).

        Schema v3 contract: if the WAL file is present alongside a v3 main
        file, run crash recovery on open. Recovery is supplied by
        ``tinydb.recovery`` which arrives in Task 5; until then this is a
        no-op (ImportError-tolerant).
        """
        if self._is_memory:
            return
        self._wal_path = self._path + ".wal"
        wal_exists = os.path.exists(self._wal_path)
        if not wal_exists:
            return
        # Run recovery against the existing WAL. ``recovery`` is added in
        # Task 5; missing-import is tolerated so Task 2 is independently
        # testable.
        try:
            from tinydb.recovery import Recovery
        except ImportError:
            return
        wal = Wal(self._wal_path)
        Recovery.replay(self._path, wal)
        # After recovery, the WAL may be truncated to drop everything below
        # the oldest still-uncommitted transaction. If replay already
        # truncated, the file is shortened — leave the handle open so the
        # Pager can keep appending.
        self._wal = wal

    def _read_header_bytes(self) -> bytes:
        """Read the first 9 bytes (magic + schema) from ``self._fd``.

        Used by SchemaMismatch paths and v2 auto-upgrade detection.
        """
        if self._file is None:
            raise RuntimeError("Pager not opened against a file")
        self._file.seek(0)
        return self._file.read(len(MAGIC) + 1)

    def _upgrade_v2_header_to_v3(self) -> None:
        """Bump the on-disk schema byte from 0x02 to 0x03 in place.

        Caller must have already verified byte 8 == 0x02 and confirmed
        there is no WAL residue. We close + reopen in "r+b" so we can
        seek+write anywhere (the original handle was opened in "a+b"
        which appends).
        """
        if self._file is None:
            raise RuntimeError("Pager not opened against a file")
        self._file.close()
        self._file = open(self._path, "r+b")
        self._file.seek(len(MAGIC_PREFIX) + 1)
        self._file.write(bytes([SCHEMA_VERSION]))  # 0x02 -> 0x03
        self._file.flush()

    def _get_or_open_wal(self) -> "Wal":
        """Lazily open the WAL handle (file or in-memory).

        File-mode path: the WAL is opened in append mode by ``Wal.__init__``.
        In-memory mode: a fresh ``Wal(None)`` is created.
        """
        if self._wal is None:
            if self._is_memory:
                self._wal = Wal(None)
            else:
                self._wal = Wal(self._wal_path)
        return self._wal

    def wal_append_page(self, txn_id: int, page_id: int, data: bytes) -> None:
        """Append a PAGE_WRITE record to the WAL (kind=1)."""
        self._get_or_open_wal().append(txn_id, 1, page_id, data)

    def wal_append_commit(self, txn_id: int) -> None:
        """Append a COMMIT record to the WAL (kind=2)."""
        self._get_or_open_wal().append(txn_id, 2)

    def wal_append_rollback(self, txn_id: int) -> None:
        """Append a ROLLBACK record to the WAL (kind=3)."""
        self._get_or_open_wal().append(txn_id, 3)

    def wal_truncate_before(self, txn_id: int) -> None:
        """Drop all WAL records older than ``txn_id`` and flush main file.

        Called after the checkpoints have been written; truncation lets the
        WAL stay bounded in size even on long-lived processes.
        """
        wal = self._get_or_open_wal()
        wal.truncate_before(txn_id)
        # Mirror flush so the (rewritten) WAL bytes are durable alongside the
        # main file. ``flush`` covers both mmap + the underlying file handle;
        # for in-memory WAL we don't need separate fsync.
        self.flush()

    def write_main_page(self, page_id: int, data: bytes) -> None:
        """Write ``data`` to the main file at the slot for ``page_id``.

        Unlike :meth:`write_page`, this bypasses ``len(data) == PAGE_SIZE``
        so it can carry partial writes (PAGE_WRITE records in the WAL may
        contain < PAGE_SIZE if the logging layer skipped unchanged halves —
        see Task 3+).
        """
        if self._is_memory:
            if len(data) != PAGE_SIZE:
                raise ValueError(
                    f"page data must be {PAGE_SIZE} bytes, got {len(data)}"
                )
            if page_id not in self._mem_pages:
                self._mem_pages[page_id] = bytearray(PAGE_SIZE)
            self._mem_pages[page_id][:] = data
            return
        if self._file is None:
            raise RuntimeError("Pager not opened against a file")
        self._file.seek(page_id * PAGE_SIZE)
        self._file.write(data)
        self._file.flush()

    def fsync_main(self) -> None:
        """fsync the main file to disk (durability barrier for WAL+main)."""
        if self._file is None:
            return
        self._file.flush()
        os.fsync(self._file.fileno())

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