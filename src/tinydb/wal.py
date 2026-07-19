"""Write-Ahead Log (WAL) for TinyDB.

Append-only log of transaction records protected by a per-record CRC32. The
header is written once on file creation and is followed by a stream of variable
length records. Records may carry an arbitrary payload (typically a page image
for PAGE_WRITE) and are identified by a monotonically increasing ``txn_id``;
records for committed transactions may be physically removed via
``truncate_before``.

Record layout (big-endian, written after the 16-byte header)::

    u64  txn_id
    u8   kind
    u32  page_id
    u32  data_len
    u8[] payload (data_len bytes)
    u32  crc32   (over the preceding bytes)
"""
from __future__ import annotations

import os
import struct
import zlib
from typing import Iterator

HEADER_SIZE: int = 16
HEADER_MAGIC: bytes = b"TINYWAL\x00"
HEADER_SCHEMA: int = 0x01

BEGIN: int = 0
PAGE_WRITE: int = 1
COMMIT: int = 2
ROLLBACK: int = 3
CHECKPOINT: int = 4

_HEADER_FMT = ">8sB7s"
_RECORD_HDR_FMT = ">QBI I"  # u64 txn_id, u8 kind, u32 page_id, u32 data_len
_RECORD_HDR_SIZE = struct.calcsize(_RECORD_HDR_FMT)  # 17
_CRC_FMT = ">I"
_CRC_SIZE = struct.calcsize(_CRC_FMT)  # 4


class WalCorruption(Exception):
    """Raised when a record's CRC32 does not match its payload.

    The ``offset`` attribute holds the file offset where the corrupted record
    begins.
    """

    def __init__(self, offset: int, message: str = "WAL record CRC mismatch"):
        super().__init__(message)
        self.offset = offset


class InvalidWalFile(Exception):
    """Raised when the WAL file cannot be opened (bad magic or schema)."""


class Wal:
    """Append-only Write-Ahead Log with CRC32-protected records."""

    def __init__(self, path: str | None):
        self._file = None
        self._buf: bytearray | None = None
        if path is None:
            # In-memory mode: bytearray seeded with the header.
            self._buf = bytearray(HEADER_SIZE)
            self._buf[:8] = HEADER_MAGIC
            self._buf[8] = HEADER_SCHEMA
            self._buf[9:16] = b"\x00" * 7
            return

        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        self._file = open(path, "w+b" if new_file else "r+b")
        if new_file:
            self._file.write(self._make_header())
            self._file.flush()
            return

        # Existing file: validate header before allowing any operation.
        self._file.seek(0)
        hdr = self._file.read(HEADER_SIZE)
        if len(hdr) != HEADER_SIZE or hdr[:8] != HEADER_MAGIC or hdr[8] != HEADER_SCHEMA:
            self._file.close()
            self._file = None
            raise InvalidWalFile(
                f"WAL file {path!r} has invalid header "
                f"(magic={hdr[:8]!r}, schema=0x{hdr[8] if hdr else 0:02x})"
            )

    @staticmethod
    def _make_header() -> bytes:
        return struct.pack(_HEADER_FMT, HEADER_MAGIC, HEADER_SCHEMA, b"\x00" * 7)

    @staticmethod
    def _encode_record(txn_id: int, kind: int, page_id: int, data: bytes) -> bytes:
        body = struct.pack(_RECORD_HDR_FMT, txn_id, kind, page_id, len(data)) + data
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return body + struct.pack(_CRC_FMT, crc)

    def _buffer(self) -> bytes:
        if self._file is None:
            return bytes(self._buf)  # type: ignore[arg-type]
        self._file.flush()
        self._file.seek(0)
        return self._file.read()

    def _replace_buffer(self, new_buf: bytes) -> None:
        if self._file is None:
            self._buf = bytearray(new_buf)
            return
        self._file.seek(0)
        self._file.truncate()
        self._file.write(new_buf)
        self._file.flush()

    def append(
        self,
        txn_id: int,
        kind: int,
        page_id: int = 0,
        data: bytes = b"",
    ) -> None:
        """Append one record to the log."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(f"data must be bytes, got {type(data).__name__}")
        record = self._encode_record(txn_id, kind, page_id, bytes(data))
        if self._file is None:
            self._buf.extend(record)  # type: ignore[union-attr]
        else:
            self._file.seek(0, 2)  # SEEK_END
            self._file.write(record)
            self._file.flush()

    def iter_records(self) -> Iterator[tuple[int, int, int, bytes]]:
        """Yield every well-formed record.

        On the first CRC mismatch or torn write, raise :class:`WalCorruption`
        carrying the offset of the bad record.
        """
        buf = self._buffer()
        pos = HEADER_SIZE
        end = len(buf)
        while pos < end:
            if end - pos < _RECORD_HDR_SIZE + _CRC_SIZE:
                raise WalCorruption(pos, f"WAL truncated at offset {pos}")
            header = buf[pos:pos + _RECORD_HDR_SIZE]
            txn_id, kind, page_id, data_len = struct.unpack(_RECORD_HDR_FMT, header)
            payload_end = pos + _RECORD_HDR_SIZE + data_len
            if payload_end + _CRC_SIZE > end:
                raise WalCorruption(
                    pos, f"WAL truncated inside payload at offset {pos}"
                )
            body = buf[pos:payload_end]
            crc_stored = struct.unpack(_CRC_FMT, buf[payload_end:payload_end + _CRC_SIZE])[0]
            if crc_stored != (zlib.crc32(body) & 0xFFFFFFFF):
                raise WalCorruption(
                    pos,
                    f"WAL CRC mismatch at offset {pos} "
                    f"(stored=0x{crc_stored:08x})",
                )
            yield (txn_id, kind, page_id, body[_RECORD_HDR_SIZE:])
            pos = payload_end + _CRC_SIZE

    def truncate_before(self, txn_id: int) -> None:
        """Remove every record with ``txn_id < txn_id`` in place.

        The file is rewritten from scratch (header + surviving records).
        """
        kept = [rec for rec in self.iter_records() if rec[0] >= txn_id]
        new_buf = bytearray(self._make_header())
        for t, k, p, d in kept:
            new_buf.extend(self._encode_record(t, k, p, d))
        self._replace_buffer(bytes(new_buf))

    def close(self) -> None:
        """Close the underlying file (no-op for in-memory mode)."""
        if self._file is not None:
            self._file.close()
            self._file = None