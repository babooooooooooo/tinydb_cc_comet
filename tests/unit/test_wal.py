"""Unit tests for the Write-Ahead Log (WAL).

See plan Task 1: append-only WAL with CRC32 records + truncate_before.
"""
from __future__ import annotations

import os
import struct
import zlib

import pytest

from tinydb.wal import (
    BEGIN,
    CHECKPOINT,
    COMMIT,
    HEADER_MAGIC,
    HEADER_SCHEMA,
    HEADER_SIZE,
    PAGE_WRITE,
    ROLLBACK,
    InvalidWalFile,
    Wal,
    WalCorruption,
)


# ---------------------------------------------------------------------------
# 1. test_wal_creates_new_file_with_header
# ---------------------------------------------------------------------------
def test_wal_creates_new_file_with_header(tmp_path):
    """A freshly created WAL file must begin with the 16-byte header."""
    db = tmp_path / "test.wal"
    w = Wal(str(db))
    try:
        assert os.path.getsize(db) == HEADER_SIZE
        with open(db, "rb") as f:
            hdr = f.read(HEADER_SIZE)
        assert hdr[:8] == HEADER_MAGIC
        assert hdr[8] == HEADER_SCHEMA
        assert hdr[9:16] == b"\x00" * 7
    finally:
        w.close()


# ---------------------------------------------------------------------------
# 2. test_wal_append_and_iter_returns_record
# ---------------------------------------------------------------------------
def test_wal_append_and_iter_returns_record(tmp_path):
    """Append BEGIN + PAGE_WRITE + COMMIT, iter yields them in order."""
    db = tmp_path / "test.wal"
    w = Wal(str(db))
    try:
        w.append(txn_id=1, kind=BEGIN, page_id=0, data=b"")
        w.append(txn_id=1, kind=PAGE_WRITE, page_id=7, data=b"\x01\x02\x03\x04")
        w.append(txn_id=1, kind=COMMIT, page_id=0, data=b"")

        records = list(w.iter_records())
        assert len(records) == 3
        assert records[0] == (1, BEGIN, 0, b"")
        assert records[1] == (1, PAGE_WRITE, 7, b"\x01\x02\x03\x04")
        assert records[2] == (1, COMMIT, 0, b"")
    finally:
        w.close()


# ---------------------------------------------------------------------------
# 3. test_wal_append_writes_valid_crc32
# ---------------------------------------------------------------------------
def test_wal_append_writes_valid_crc32(tmp_path):
    """Independently recompute the CRC and confirm it matches the stored one."""
    db = tmp_path / "test.wal"
    w = Wal(str(db))
    try:
        w.append(txn_id=42, kind=PAGE_WRITE, page_id=99, data=b"hello")

        with open(db, "rb") as f:
            f.seek(HEADER_SIZE)
            raw = f.read()

        # Record layout: u64 txn_id | u8 kind | u32 page_id | u32 data_len | payload | u32 crc
        assert len(raw) == 8 + 1 + 4 + 4 + 5 + 4
        body_len = 8 + 1 + 4 + 4 + 5
        body = raw[:body_len]
        stored_crc = struct.unpack(">I", raw[body_len:body_len + 4])[0]
        expected_crc = zlib.crc32(body) & 0xFFFFFFFF
        assert stored_crc == expected_crc
        assert struct.unpack(">Q", body[:8])[0] == 42
        assert body[8] == PAGE_WRITE
        assert struct.unpack(">I", body[9:13])[0] == 99
        assert struct.unpack(">I", body[13:17])[0] == 5
        assert body[17:22] == b"hello"
    finally:
        w.close()


# ---------------------------------------------------------------------------
# 4. test_wal_iter_raises_on_corrupt_crc
# ---------------------------------------------------------------------------
def test_wal_iter_raises_on_corrupt_crc(tmp_path):
    """Flipping a payload byte must cause iter_records() to raise WalCorruption."""
    db = tmp_path / "test.wal"
    w = Wal(str(db))
    try:
        w.append(txn_id=1, kind=PAGE_WRITE, page_id=2, data=b"\xAA\xBB\xCC\xDD")
        w.close()

        # Flip one byte inside the payload region (HEADER_SIZE + 17 .. +20).
        with open(db, "r+b") as f:
            f.seek(HEADER_SIZE + 17 + 1)  # flip second payload byte
            byte = f.read(1)
            f.seek(-1, 1)
            f.write(bytes([byte[0] ^ 0xFF]))

        w2 = Wal(str(db))
        try:
            with pytest.raises(WalCorruption) as excinfo:
                list(w2.iter_records())
            # The corrupted record begins at offset HEADER_SIZE.
            assert excinfo.value.offset == HEADER_SIZE
        finally:
            w2.close()
    finally:
        pass


# ---------------------------------------------------------------------------
# 5. test_wal_invalid_header_magic_raises
# ---------------------------------------------------------------------------
def test_wal_invalid_header_magic_raises(tmp_path):
    """A file with a wrong magic prefix must raise InvalidWalFile."""
    db = tmp_path / "test.wal"
    with open(db, "wb") as f:
        f.write(b"NOPE\x00\x00\x00" + b"\x00" * 8)

    with pytest.raises(InvalidWalFile):
        Wal(str(db))


# ---------------------------------------------------------------------------
# 6. test_wal_truncate_before_removes_records
# ---------------------------------------------------------------------------
def test_wal_truncate_before_removes_records(tmp_path):
    """truncate_before(2) must drop records with txn_id < 2 and keep the rest."""
    db = tmp_path / "test.wal"
    w = Wal(str(db))
    try:
        w.append(txn_id=1, kind=BEGIN, page_id=0, data=b"")
        w.append(txn_id=1, kind=COMMIT, page_id=0, data=b"")
        w.append(txn_id=2, kind=BEGIN, page_id=0, data=b"")
        w.append(txn_id=2, kind=PAGE_WRITE, page_id=5, data=b"abc")
        w.append(txn_id=3, kind=BEGIN, page_id=0, data=b"")
        w.append(txn_id=3, kind=COMMIT, page_id=0, data=b"")

        w.truncate_before(2)

        remaining = list(w.iter_records())
        assert len(remaining) == 4
        assert all(t >= 2 for (t, _k, _p, _d) in remaining)
        assert remaining[0] == (2, BEGIN, 0, b"")
        assert remaining[1] == (2, PAGE_WRITE, 5, b"abc")
        assert remaining[2] == (3, BEGIN, 0, b"")
        assert remaining[3] == (3, COMMIT, 0, b"")
    finally:
        w.close()

    # Re-open from disk and confirm persistence.
    w2 = Wal(str(db))
    try:
        again = list(w2.iter_records())
        assert len(again) == 4
        assert again[0][0] == 2
        assert again[-1][0] == 3
    finally:
        w2.close()


# ---------------------------------------------------------------------------
# 7. test_wal_in_memory_no_file
# ---------------------------------------------------------------------------
def test_wal_in_memory_no_file():
    """Wal(None) must work in-memory and not touch the filesystem."""
    w = Wal(None)
    try:
        w.append(txn_id=1, kind=BEGIN, page_id=0, data=b"")
        w.append(txn_id=1, kind=PAGE_WRITE, page_id=3, data=b"xyz")
        w.append(txn_id=1, kind=COMMIT, page_id=0, data=b"")

        records = list(w.iter_records())
        assert records == [
            (1, BEGIN, 0, b""),
            (1, PAGE_WRITE, 3, b"xyz"),
            (1, COMMIT, 0, b""),
        ]

        # truncate_before works in-memory too.
        w.truncate_before(1)
        assert list(w.iter_records()) == [
            (1, BEGIN, 0, b""),
            (1, PAGE_WRITE, 3, b"xyz"),
            (1, COMMIT, 0, b""),
        ]
    finally:
        w.close()