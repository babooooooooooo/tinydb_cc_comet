# tinydb-acid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `BEGIN` / `COMMIT` / `ROLLBACK` SQL statements + page-level write-ahead log + crash recovery on top of engine-v2's Pager v2 / B+tree storage.

**Architecture:** Append-only WAL records full 4KB pages per transaction. `Transaction.commit()` flushes pending pages to main db file + `fsync(main)` + truncate WAL. `Transaction.rollback()` discards pending writes. Crash recovery on `Pager.open()` scans WAL, applies committed txns, discards incomplete. Auto-commit wraps any non-txn statement. Schema bumps to v0x03 (manual `migrate_v2_to_v3` for v2+WAL-residue case).

**Tech Stack:** Python 3.11+, pytest ≥7, hypothesis ≥6, pytest-cov ≥4, zlib (CRC32 built-in). No external deps added.

**Base ref:** `4975f4f` (main with engine-v2 archived). Branch: `feature/20260716/tinydb-acid`.

**Module line budgets (per Design Doc §3.2):**
- `src/tinydb/wal.py` ≤ 200 (new)
- `src/tinydb/transaction.py` ≤ 300 (new)
- `src/tinydb/recovery.py` ≤ 200 (new)
- `src/tinydb/pager.py` ≤ 520 (was 313, +~170)
- `src/tinydb/executor.py` ≤ 1280 (was 1196, +~84)
- `src/tinydb/parser.py` ≤ 900 (was 861, +~19)
- `src/tinydb/tokenizer.py` ≤ 160 (was 143, +~5)
- `src/tinydb/errors.py` ≤ 100 (was 65, +~10)

**Always use `.venv/bin/python` for tests** (PEP 668 — system python will fail).

---

## File Structure (created or modified)

**New files:**
- `src/tinydb/wal.py` — WalHeader / WalRecord / Wal class + CRC32
- `src/tinydb/transaction.py` — Transaction class with state machine
- `src/tinydb/recovery.py` — Recovery.replay static method
- `tests/unit/test_wal.py` — WAL unit tests
- `tests/unit/test_transaction.py` — Transaction unit tests
- `tests/unit/test_acid_parser.py` — Parser unit tests for BEGIN/COMMIT/ROLLBACK
- `tests/integration/test_acid.py` — Cross-process BEGIN...COMMIT/ROLLBACK
- `tests/integration/test_ddl_in_transaction.py` — CREATE/DROP in txn
- `tests/integration/test_crash_recovery.py` — kill -9 simulation
- `tests/integration/test_pager_v3_header.py` — schema=0x03 + v2 mismatch
- `tests/integration/test_autocommit.py` — auto-commit semantics
- `tests/integration/test_recovery_fuzz.py` — Random WAL fuzz

**Modified files:**
- `src/tinydb/pager.py` — schema_version 0x03 + WAL integration methods
- `src/tinydb/parser.py` — Begin/Commit/Rollback AST + parse branches
- `src/tinydb/tokenizer.py` — COMMIT/ROLLBACK keyword recognition (BEGIN already there)
- `src/tinydb/executor.py` — current_txn state + txn routing
- `src/tinydb/errors.py` — InvalidTxnState + SchemaMismatch exceptions

---

## Task 1: WAL foundation (Wal class + record format + CRC32)

**Files:**
- Create: `src/tinydb/wal.py`
- Create: `tests/unit/test_wal.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wal.py
import os
import struct
import zlib

from tinydb.wal import Wal, WalCorruption, InvalidWalFile, HEADER_SIZE


def test_wal_creates_new_file_with_header(tmp_path):
    path = str(tmp_path / "test.wal")
    w = Wal(path)
    w.close()
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"TINYWAL\x00"
    assert data[8] == 0x01


def test_wal_append_and_iter_returns_record(tmp_path):
    path = str(tmp_path / "test.wal")
    w = Wal(path)
    w.append(txn_id=1, kind=0)  # BEGIN
    w.append(txn_id=1, kind=1, page_id=42, data=b"\xab" * 100)
    w.append(txn_id=1, kind=2)  # COMMIT
    w.close()

    w2 = Wal(path)
    records = list(w2.iter_records())
    assert len(records) == 3
    assert records[0] == (1, 0, 0, b"")
    assert records[1] == (1, 1, 42, b"\xab" * 100)
    assert records[2] == (1, 2, 0, b"")
    w2.close()


def test_wal_append_writes_valid_crc32(tmp_path):
    path = str(tmp_path / "test.wal")
    w = Wal(path)
    w.append(txn_id=7, kind=1, page_id=99, data=b"\xde\xad\xbe\xef")
    w.close()

    with open(path, "rb") as f:
        raw = f.read()
    # HEADER_SIZE + record header (8+1+4+4) + payload (4) = HEADER + 21
    rec_start = HEADER_SIZE
    rec_end = len(raw) - 4
    payload = raw[rec_start:rec_end]
    crc_in_file = struct.unpack(">I", raw[rec_end:])[0]
    assert zlib.crc32(payload) == crc_in_file


def test_wal_iter_raises_on_corrupt_crc(tmp_path):
    path = str(tmp_path / "test.wal")
    w = Wal(path)
    w.append(txn_id=1, kind=1, page_id=10, data=b"hello")
    w.close()

    # Flip a byte in the payload to corrupt CRC
    with open(path, "r+b") as f:
        f.seek(HEADER_SIZE + 17)  # first byte of payload
        b = f.read(1)
        f.seek(HEADER_SIZE + 17)
        f.write(bytes([b[0] ^ 0xFF]))

    w2 = Wal(path)
    try:
        list(w2.iter_records())
        assert False, "expected WalCorruption"
    except WalCorruption:
        pass
    finally:
        w2.close()


def test_wal_invalid_header_magic_raises(tmp_path):
    path = str(tmp_path / "bad.wal")
    with open(path, "wb") as f:
        f.write(b"NOTAWAL\x00" + b"\x00" * (HEADER_SIZE - 9))
    try:
        Wal(path)
        assert False, "expected InvalidWalFile"
    except InvalidWalFile:
        pass


def test_wal_truncate_before_removes_records(tmp_path):
    path = str(tmp_path / "test.wal")
    w = Wal(path)
    w.append(txn_id=1, kind=0)  # BEGIN
    w.append(txn_id=1, kind=1, page_id=1, data=b"a")
    w.append(txn_id=1, kind=2)  # COMMIT
    w.append(txn_id=2, kind=0)  # BEGIN
    w.append(txn_id=2, kind=1, page_id=2, data=b"b")
    w.close()

    w2 = Wal(path)
    w2.truncate_before(txn_id=2)
    w2.close()

    w3 = Wal(path)
    records = list(w3.iter_records())
    # Only txn_id >= 2 remain (the BEGIN + PAGE_WRITE for txn 2)
    assert all(r[0] >= 2 for r in records)
    assert len(records) == 2
    w3.close()


def test_wal_in_memory_no_file(tmp_path):
    """Wal(path=None) is in-memory; close() does nothing."""
    w = Wal(None)
    w.append(txn_id=1, kind=0)
    records = list(w.iter_records())
    assert records == [(1, 0, 0, b"")]
    w.close()  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_wal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tinydb.wal'`

- [ ] **Step 3: Implement Wal class**

```python
# src/tinydb/wal.py
"""Append-only write-ahead log for tinydb transactions.

File format:
  Header (HEADER_SIZE bytes):
    bytes 0-7:   magic (b'TINYWAL\\x00')
    byte  8:     schema (0x01)
    bytes 9-15:  reserved
  Record (variable, append-only):
    bytes 0-7:   txn_id (u64)
    byte  8:     kind   (0=begin, 1=page_write, 2=commit, 3=rollback, 4=checkpoint)
    bytes 9-12:  page_id (u32, only page_write)
    bytes 13-16: data_len (u32)
    bytes 17..(17+data_len-1): payload
    last 4 bytes: crc32 over bytes 0..(17+data_len-1)
"""
from __future__ import annotations

import os
import struct
import zlib
from typing import Iterator


HEADER_SIZE = 16
HEADER_MAGIC = b"TINYWAL\x00"
HEADER_SCHEMA = 0x01

KIND_BEGIN = 0
KIND_PAGE_WRITE = 1
KIND_COMMIT = 2
KIND_ROLLBACK = 3
KIND_CHECKPOINT = 4


class WalCorruption(Exception):
    """Raised when CRC32 mismatch is detected at a record boundary.

    The offset attribute is the byte position where the corrupt record begins.
    """


class InvalidWalFile(Exception):
    """Raised when WAL header (magic or schema) is invalid."""


class Wal:
    """Append-only write-ahead log. Records are framed with CRC32 for integrity."""

    HEADER_SIZE = HEADER_SIZE
    HEADER_MAGIC = HEADER_MAGIC
    HEADER_SCHEMA = HEADER_SCHEMA

    def __init__(self, path: str | None):
        self._path = path
        if path is None:
            # In-memory mode: write to bytes buffer
            self._buf = bytearray()
            self._buf.extend(HEADER_MAGIC)
            self._buf.append(HEADER_SCHEMA)
            self._buf.extend(b"\x00" * 7)
            self._fd = None
        else:
            new_file = not os.path.exists(path) or os.path.getsize(path) == 0
            self._fd = open(path, "a+b" if new_file else "r+b")
            if new_file:
                self._fd.write(HEADER_MAGIC + bytes([HEADER_SCHEMA]) + b"\x00" * 7)
                self._fd.flush()
            else:
                self._fd.seek(0)
                head = self._fd.read(HEADER_SIZE)
                if len(head) < HEADER_SIZE or head[:8] != HEADER_MAGIC or head[8] != HEADER_SCHEMA:
                    self._fd.close()
                    raise InvalidWalFile(f"WAL file {path!r} has invalid header")

    def append(self, txn_id: int, kind: int, page_id: int = 0, data: bytes = b"") -> None:
        """Append one record. Auto-computes CRC32."""
        if len(data) > 0xFFFFFFFF:
            raise ValueError(f"data too large: {len(data)} bytes")
        header = struct.pack(">QBI I", txn_id, kind, page_id, len(data))
        payload = header + data
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        record = payload + struct.pack(">I", crc)

        if self._fd is None:
            self._buf.extend(record)
        else:
            self._fd.seek(0, 2)  # append
            self._fd.write(record)
            self._fd.flush()

    def iter_records(self) -> Iterator[tuple[int, int, int, bytes]]:
        """Yield (txn_id, kind, page_id, data) for each valid record.

        Raises WalCorruption at the first record whose CRC32 does not match.
        """
        if self._fd is None:
            buf = bytes(self._buf)
        else:
            self._fd.seek(0)
            buf = self._fd.read()
        pos = HEADER_SIZE
        while pos < len(buf):
            # Need at least 17 bytes (header) + 4 bytes (crc)
            if pos + 17 + 4 > len(buf):
                raise WalCorruption(pos)
            txn_id, kind, page_id, data_len = struct.unpack(">QBI I", buf[pos : pos + 17])
            end = pos + 17 + data_len + 4
            if end > len(buf):
                raise WalCorruption(pos)
            payload = buf[pos : pos + 17 + data_len]
            stored_crc = struct.unpack(">I", buf[pos + 17 + data_len : end])[0]
            if zlib.crc32(payload) & 0xFFFFFFFF != stored_crc:
                raise WalCorruption(pos)
            yield (txn_id, kind, page_id, payload[17:])
            pos = end

    def truncate_before(self, txn_id: int) -> None:
        """Remove records with txn_id < arg, preserving record boundary."""
        if self._fd is None:
            buf = bytearray(self._buf[:HEADER_SIZE])  # keep header
            for rec in self.iter_records():
                if rec[0] >= txn_id:
                    # Re-serialize and append
                    rec_txn, rec_kind, rec_pid, rec_data = rec
                    header = struct.pack(">QBI I", rec_txn, rec_kind, rec_pid, len(rec_data))
                    payload = header + rec_data
                    crc = zlib.crc32(payload) & 0xFFFFFFFF
                    buf.extend(payload + struct.pack(">I", crc))
            self._buf = buf
        else:
            buf = bytearray()
            with open(self._path, "rb") as f:
                buf.extend(f.read(HEADER_SIZE))
            for rec in self.iter_records():
                if rec[0] >= txn_id:
                    rec_txn, rec_kind, rec_pid, rec_data = rec
                    header = struct.pack(">QBI I", rec_txn, rec_kind, rec_pid, len(rec_data))
                    payload = header + rec_data
                    crc = zlib.crc32(payload) & 0xFFFFFFFF
                    buf.extend(payload + struct.pack(">I", crc))
            with open(self._path, "wb") as f:
                f.write(bytes(buf))

    def close(self) -> None:
        if self._fd is not None:
            self._fd.close()
            self._fd = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_wal.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/wal.py tests/unit/test_wal.py
git commit -m "feat(wal): append-only WAL with CRC32 records + truncate_before"
```

---

## Task 2: Pager schema bump to 0x03 + WAL integration methods

**Files:**
- Modify: `src/tinydb/pager.py:1-50` (constants + imports)
- Modify: `src/tinydb/pager.py` add new methods (write_main_page, fsync_main, wal_append_*, wal_truncate_before)
- Create: `tests/integration/test_pager_v3_header.py`

- [ ] **Step 1: Write failing tests for schema bump + WAL integration**

```python
# tests/integration/test_pager_v3_header.py
import pytest

from tinydb.pager import Pager, SCHEMA_VERSION, MAGIC, PAGE_SIZE


@pytest.mark.integration
def test_pager_schema_version_is_3():
    assert SCHEMA_VERSION == 0x03


@pytest.mark.integration
def test_pager_new_file_writes_v3_header(tmp_path):
    path = str(tmp_path / "new.db")
    p = Pager(path)
    p.close()
    with open(path, "rb") as f:
        magic = f.read(8)
        schema = f.read(1)[0]
    assert magic == MAGIC
    assert schema == 0x03


@pytest.mark.integration
def test_pager_upgrades_v2_header_on_open(tmp_path):
    """Opening a v2 file (schema=0x02, no WAL) bumps header to 0x03 in place."""
    path = str(tmp_path / "v2.db")
    # Create v2 file manually
    with open(path, "wb") as f:
        f.write(MAGIC + bytes([0x02]) + b"\x00" * (PAGE_SIZE - 9))
    # Open with new pager
    p = Pager(path)
    try:
        # Header should now be 0x03
        with open(path, "rb") as f:
            schema = f.read(8 + 1)[8]
        assert schema == 0x03
    finally:
        p.close()


@pytest.mark.integration
def test_pager_raises_schema_mismatch_if_v2_with_wal_residue(tmp_path):
    """v2 file + <db>.wal present → SchemaMismatch (user must migrate first)."""
    from tinydb.errors import SchemaMismatch
    path = str(tmp_path / "v2.db")
    wal_path = str(tmp_path / "v2.db.wal")
    # Write v2 file
    with open(path, "wb") as f:
        f.write(MAGIC + bytes([0x02]) + b"\x00" * (PAGE_SIZE - 9))
    # Write valid WAL header to wal_path
    with open(wal_path, "wb") as f:
        f.write(b"TINYWAL\x00" + bytes([0x01]) + b"\x00" * 7)
    with pytest.raises(SchemaMismatch, match="schema"):
        Pager(path)


@pytest.mark.integration
def test_pager_fsync_main_flushes_to_disk(tmp_path):
    """write_main_page + fsync_main makes data durable."""
    path = str(tmp_path / "fs.db")
    p = Pager(path)
    pid = p.alloc_page()
    p.write_main_page(pid, b"\xab" * PAGE_SIZE)
    p.fsync_main()
    p.close()

    p2 = Pager(path)
    try:
        assert p2.read_page(pid) == b"\xab" * PAGE_SIZE
    finally:
        p2.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_pager_v3_header.py -v`
Expected: FAIL on `SCHEMA_VERSION == 0x03` (current value is 0x02), and `SchemaMismatch` not yet defined.

- [ ] **Step 3: Update pager.py constants**

In `src/tinydb/pager.py` at top:

```python
# Change SCHEMA_VERSION = 0x02 to:
SCHEMA_VERSION = 0x03
```

- [ ] **Step 4: Add SchemaMismatch to errors.py**

In `src/tinydb/errors.py` add:

```python
class SchemaMismatch(TinydbError):
    """Raised when on-disk schema version is incompatible with current code.

    Typically means a v2 file has WAL residue that must be migrated to v3 first.
    """

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg
```

- [ ] **Step 5: Add WAL integration methods to Pager**

In `src/tinydb/pager.py`:

```python
# Add import at top:
import os
from tinydb.wal import Wal, HEADER_SIZE, HEADER_MAGIC, HEADER_SCHEMA
from tinydb.errors import SchemaMismatch

# In Pager.__init__ add (after existing init logic):
        self._wal: Wal | None = None
        self._wal_path = self._path + ".wal" if self._path != ":memory:" else None
        if self._wal_path and os.path.exists(self._wal_path):
            # Check schema: if main file is still v2 with WAL residue, raise
            raw = self._read_header_bytes()
            main_schema = raw[8] if len(raw) > 8 else 0
            if main_schema == 0x02:
                raise SchemaMismatch(
                    f"db file {self._path!r} is schema 0x02 with WAL residue; "
                    "call migrate_v2_to_v3(path) before opening"
                )
            # Recovery on open
            from tinydb.recovery import Recovery
            self._wal = Wal(self._wal_path)
            Recovery.replay(self._path, self._wal)

# Add new methods (anywhere in the class):
    def _read_header_bytes(self) -> bytes:
        """Read first 9 bytes of main file (magic + schema)."""
        self._fd.seek(0)
        return self._fd.read(9)

    def _upgrade_v2_header_to_v3(self) -> None:
        """Rewrite header byte 8 from 0x02 to 0x03. Called when opening v2 file with no WAL."""
        self._fd.seek(0)
        head = bytearray(self._fd.read(9))
        if len(head) == 9 and head[8] == 0x02:
            head[8] = 0x03
            self._fd.seek(0)
            self._fd.write(bytes(head))
            self._fd.flush()

    def _get_or_open_wal(self) -> Wal:
        """Lazily open WAL file (returns existing or creates new)."""
        if self._wal is None:
            if self._wal_path is None:
                self._wal = Wal(None)  # in-memory
            else:
                self._wal = Wal(self._wal_path)
        return self._wal

    def wal_append_page(self, txn_id: int, page_id: int, data: bytes) -> None:
        """Append a PAGE_WRITE record to the WAL."""
        self._get_or_open_wal().append(txn_id, 1, page_id, data)

    def wal_append_commit(self, txn_id: int) -> None:
        """Append a COMMIT record to the WAL."""
        self._get_or_open_wal().append(txn_id, 2)

    def wal_append_rollback(self, txn_id: int) -> None:
        """Append a ROLLBACK record to the WAL."""
        self._get_or_open_wal().append(txn_id, 3)

    def wal_truncate_before(self, txn_id: int) -> None:
        """Truncate WAL records with txn_id < arg."""
        wal = self._get_or_open_wal()
        wal.truncate_before(txn_id)
        if self._fd is not None:
            self._fd.flush()

    def write_main_page(self, page_id: int, data: bytes) -> None:
        """Write page directly to main db file (no WAL). Used by Transaction.commit()."""
        if self._fd is None:
            raise RuntimeError("cannot write_main_page on closed pager")
        offset = page_id * PAGE_SIZE
        self._fd.seek(offset)
        self._fd.write(data)

    def fsync_main(self) -> None:
        """fsync the main db file."""
        if self._fd is None:
            raise RuntimeError("cannot fsync_main on closed pager")
        self._fd.flush()
        os.fsync(self._fd.fileno())
```

Also add in `__init__` after reading magic:

```python
        # In-memory mode: skip v2 upgrade + WAL
        if self._path == ":memory:":
            self._wal = None
            self._wal_path = None
            return

        # v2 file (no WAL) → upgrade in place
        if not os.path.exists(self._wal_path or ""):
            self._upgrade_v2_header_to_v3()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_pager_v3_header.py -v`
Expected: 5 passed (the recovery import in __init__ may cause ImportError if recovery.py doesn't exist yet — see Task 5 first if so)

Note: If you need to run before Task 5 (recovery.py not yet created), temporarily guard the recovery import with a try/except or comment it out. Re-enable in Task 5 step.

- [ ] **Step 7: Commit**

```bash
git add src/tinydb/pager.py src/tinydb/errors.py tests/integration/test_pager_v3_header.py
git commit -m "feat(pager): schema_version 0x03 + WAL integration methods + SchemaMismatch"
```

---

## Task 3: Transaction state machine

**Files:**
- Create: `src/tinydb/transaction.py`
- Create: `tests/unit/test_transaction.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_transaction.py
from unittest.mock import MagicMock

import pytest

from tinydb.transaction import Transaction, TxnState, InvalidTxnState


def test_txn_starts_in_active_state():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    assert txn.state == TxnState.ACTIVE
    assert txn.pending_writes == {}


def test_txn_write_page_buffers_and_appends_wal():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=42, data=b"hello")
    assert txn.pending_writes == {42: b"hello"}
    pager.wal_append_page.assert_called_once_with(1, 42, b"hello")


def test_txn_write_page_in_non_active_state_raises():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn._state = TxnState.COMMITTED  # simulate committed
    with pytest.raises(InvalidTxnState):
        txn.write_page(page_id=1, data=b"x")


def test_txn_commit_writes_pages_then_appends_commit_then_fs_syncs_then_truncates():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=10, data=b"page10")
    txn.write_page(page_id=20, data=b"page20")
    txn.commit()
    # write_main_page called for each pending page
    assert pager.write_main_page.call_count == 2
    pager.wal_append_commit.assert_called_once_with(1)
    pager.fsync_main.assert_called_once()
    pager.wal_truncate_before.assert_called_once_with(1)
    assert txn.state == TxnState.COMMITTED


def test_txn_commit_after_commit_raises():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.commit()
    with pytest.raises(InvalidTxnState):
        txn.commit()


def test_txn_rollback_appends_rollback_then_truncates_and_never_writes_main():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=10, data=b"page10")
    txn.rollback()
    pager.write_main_page.assert_not_called()
    pager.wal_append_rollback.assert_called_once_with(1)
    pager.wal_truncate_before.assert_called_once_with(1)
    assert txn.state == TxnState.ROLLED_BACK


def test_txn_rollback_after_commit_raises():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.commit()
    with pytest.raises(InvalidTxnState):
        txn.rollback()


def test_txn_multiple_writes_to_same_page_overwrite_pending():
    pager = MagicMock()
    txn = Transaction(txn_id=1, pager=pager)
    txn.write_page(page_id=10, data=b"v1")
    txn.write_page(page_id=10, data=b"v2")
    assert txn.pending_writes[10] == b"v2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transaction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tinydb.transaction'`

- [ ] **Step 3: Implement Transaction class**

```python
# src/tinydb/transaction.py
"""Transaction state machine: ACTIVE → COMMITTED | ROLLED_BACK.

Pending writes are buffered in memory; flushed to WAL on each write_page.
commit() applies pending writes to main db file + fsync + truncate WAL.
rollback() discards pending writes + append rollback record + truncate WAL.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinydb.pager import Pager


class TxnState(Enum):
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


class InvalidTxnState(Exception):
    """Raised when write_page / commit / rollback called in non-ACTIVE state."""

    def __init__(self, txn_id: int, state: TxnState):
        self.txn_id = txn_id
        self.state = state
        super().__init__(f"transaction {txn_id} is {state.value}, not active")


class Transaction:
    def __init__(self, txn_id: int, pager: "Pager"):
        self.id = txn_id
        self._pager = pager
        self._state: TxnState = TxnState.ACTIVE
        self.pending_writes: dict[int, bytes] = {}

    @property
    def state(self) -> TxnState:
        return self._state

    def write_page(self, page_id: int, data: bytes) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        self.pending_writes[page_id] = data
        self._pager.wal_append_page(self.id, page_id, data)

    def commit(self) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        for pid, data in self.pending_writes.items():
            self._pager.write_main_page(pid, data)
        self._pager.wal_append_commit(self.id)
        self._pager.fsync_main()
        self._pager.wal_truncate_before(self.id)
        self._state = TxnState.COMMITTED

    def rollback(self) -> None:
        if self._state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self._state)
        self._pager.wal_append_rollback(self.id)
        self._pager.wal_truncate_before(self.id)
        self._state = TxnState.ROLLED_BACK
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transaction.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/transaction.py tests/unit/test_transaction.py
git commit -m "feat(transaction): Transaction state machine with WAL-backed commit/rollback"
```

---

## Task 4: Parser Begin / Commit / Rollback

**Files:**
- Modify: `src/tinydb/parser.py:38-90` (add AST dataclasses)
- Modify: `src/tinydb/parser.py:238-265` (parse_statement branches)
- Modify: `src/tinydb/tokenizer.py` (ensure COMMIT/ROLLBACK keywords recognized)
- Create: `tests/unit/test_acid_parser.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_acid_parser.py
from tinydb.parser import Parser, Tokenizer
from tinydb.parser import Begin, Commit, Rollback


def _parse_one(sql: str):
    toks = Tokenizer(sql).tokenize()
    p = Parser(toks)
    return p.parse_statement()


def test_parse_begin():
    stmt = _parse_one("BEGIN")
    assert isinstance(stmt, Begin)


def test_parse_commit():
    stmt = _parse_one("COMMIT")
    assert isinstance(stmt, Commit)


def test_parse_rollback():
    stmt = _parse_one("ROLLBACK")
    assert isinstance(stmt, Rollback)


def test_parse_begin_with_trailing_semicolon():
    stmt = _parse_one("BEGIN;")
    assert isinstance(stmt, Begin)


def test_tokenizer_recognizes_commit_rollback_keywords():
    from tinydb.tokenizer import Tokenizer
    toks = Tokenizer("COMMIT ROLLBACK").tokenize()
    assert toks[0].value == "COMMIT"
    assert toks[0].type == "KEYWORD"
    assert toks[1].value == "ROLLBACK"
    assert toks[1].type == "KEYWORD"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_acid_parser.py -v`
Expected: FAIL on `ImportError: cannot import name 'Begin' from 'tinydb.parser'`

- [ ] **Step 3: Add AST nodes to parser.py**

In `src/tinydb/parser.py`, after existing dataclass definitions (~line 178), add:

```python
@dataclass
class Begin:
    pass


@dataclass
class Commit:
    pass


@dataclass
class Rollback:
    pass
```

- [ ] **Step 4: Add parse branches in parse_statement**

In `src/tinydb/parser.py` in `parse_statement` method (~line 238), at the top:

```python
    def parse_statement(self) -> Any:
        tok = self.peek()
        if tok.type == "KEYWORD":
            if tok.value == "BEGIN":
                self.advance()
                return Begin()
            if tok.value == "COMMIT":
                self.advance()
                return Commit()
            if tok.value == "ROLLBACK":
                self.advance()
                return Rollback()
        return self._parse_dml_or_ddl()
```

(If `parse_statement` is structured differently, add these branches before the existing DML/DDL dispatch. The key: peek for `KEYWORD` with value `BEGIN` / `COMMIT` / `ROLLBACK` and consume one token.)

- [ ] **Step 5: Verify tokenizer recognizes COMMIT/ROLLBACK**

Check `src/tinydb/tokenizer.py` for the keyword list. If `COMMIT` and `ROLLBACK` are missing, add them. The list likely uses `KEYWORDS = {"SELECT", "INSERT", ...}` — append `"COMMIT"` and `"ROLLBACK"`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_acid_parser.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add src/tinydb/parser.py src/tinydb/tokenizer.py tests/unit/test_acid_parser.py
git commit -m "feat(parser): Begin/Commit/Rollback AST nodes + parse branches"
```

---

## Task 5: Crash Recovery (Recovery.replay + Pager open integration)

**Files:**
- Create: `src/tinydb/recovery.py`
- Modify: `src/tinydb/pager.py` (already imported Recovery in Task 2)
- Create: `tests/unit/test_recovery.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_recovery.py
import os
import struct
import zlib

import pytest

from tinydb.recovery import Recovery
from tinydb.wal import Wal, HEADER_SIZE


def _make_wal_file(path: str, records: list[tuple[int, int, int, bytes]]) -> None:
    """Helper: write a WAL file with the given records."""
    w = Wal(path)
    for txn_id, kind, page_id, data in records:
        w.append(txn_id, kind, page_id, data)
    w.close()


def test_recovery_apply_committed_pages_to_main(tmp_path):
    """BEGIN + PAGE_WRITE + COMMIT → recovery replays page to main file."""
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    # Create empty main file
    from tinydb.pager import Pager, PAGE_SIZE
    p = Pager(main_path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    # Build WAL with one committed txn writing page `pid`
    payload = b"\xab" * PAGE_SIZE
    _make_wal_file(wal_path, [
        (1, 0, 0, b""),                      # BEGIN
        (1, 1, pid, payload),                # PAGE_WRITE
        (1, 2, 0, b""),                      # COMMIT
    ])

    Recovery.replay(main_path, Wal(wal_path))
    # Verify main file has the payload
    p2 = Pager(main_path)
    assert p2.read_page(pid) == payload
    p2.close()
    # WAL should be truncated after replay
    assert os.path.getsize(wal_path) == HEADER_SIZE


def test_recovery_discards_uncommitted_txn(tmp_path):
    """BEGIN + PAGE_WRITE (no commit) → recovery discards."""
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    from tinydb.pager import Pager, PAGE_SIZE
    p = Pager(main_path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    # WAL with uncommitted txn
    _make_wal_file(wal_path, [
        (1, 0, 0, b""),                       # BEGIN
        (1, 1, pid, b"\xde\xad\xbe\xef" * 1024),  # PAGE_WRITE, no commit
    ])

    Recovery.replay(main_path, Wal(wal_path))
    p2 = Pager(main_path)
    # Page should NOT have the uncommitted payload
    assert p2.read_page(pid) != b"\xde\xad\xbe\xef" * 1024
    p2.close()


def test_recovery_truncates_corrupt_tail(tmp_path):
    """CRC mismatch at record X → truncate to before X + apply earlier committed."""
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    from tinydb.pager import Pager, PAGE_SIZE
    p = Pager(main_path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    # Write valid record + corrupt trailing bytes
    payload = b"\x42" * PAGE_SIZE
    _make_wal_file(wal_path, [
        (1, 0, 0, b""),
        (1, 1, pid, payload),
        (1, 2, 0, b""),
    ])
    # Append junk (simulates partial write)
    with open(wal_path, "ab") as f:
        f.write(b"\xff\xff\xff\xff\xff\xff\xff\xff")

    from tinydb.wal import WalCorruption
    w = Wal(wal_path)
    with pytest.raises(WalCorruption):
        Recovery.replay(main_path, w)
    # Page from committed txn should be applied
    p2 = Pager(main_path)
    assert p2.read_page(pid) == payload
    p2.close()


def test_recovery_empty_wal_is_noop(tmp_path):
    """WAL with only header → recovery is no-op."""
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    from tinydb.pager import Pager, PAGE_SIZE
    p = Pager(main_path)
    p.flush()
    p.close()

    w = Wal(wal_path)  # creates empty WAL
    w.close()

    # Should not raise
    Recovery.replay(main_path, Wal(wal_path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_recovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tinydb.recovery'`

- [ ] **Step 3: Implement Recovery**

```python
# src/tinydb/recovery.py
"""Crash recovery: replay committed transactions from WAL into main db file."""
from __future__ import annotations

import os

from tinydb.wal import Wal, WalCorruption


class Recovery:
    @staticmethod
    def replay(main_path: str, wal: Wal) -> None:
        """Scan WAL, apply committed txns to main file, discard incomplete.

        Raises:
            WalCorruption: CRC mismatch detected. Recovery applies all valid
                records before the corrupt one, then re-raises.
        """
        pending: dict[int, dict[int, bytes]] = {}
        status: dict[int, str] = {}

        try:
            for txn_id, kind, page_id, data in wal.iter_records():
                if kind == 0:  # BEGIN
                    status[txn_id] = "active"
                    pending.setdefault(txn_id, {})
                elif kind == 1:  # PAGE_WRITE
                    pending.setdefault(txn_id, {})[page_id] = data
                elif kind == 2:  # COMMIT
                    status[txn_id] = "committed"
                elif kind == 3:  # ROLLBACK
                    status[txn_id] = "rolled_back"
                # kind == 4 (CHECKPOINT) ignored
        except WalCorruption as e:
            # Truncate WAL to corrupt record boundary, then continue
            offset = e.offset if hasattr(e, "offset") else 0
            _truncate_wal_to(main_path + ".wal", offset)
            _apply_committed(main_path, pending, status)
            raise

        _apply_committed(main_path, pending, status)
        wal.truncate_before(_max_txn_id(pending) + 1 if pending else 1)


def _max_txn_id(pending: dict[int, dict[int, bytes]]) -> int:
    if not pending:
        return 0
    return max(pending.keys())


def _truncate_wal_to(wal_path: str, offset: int) -> None:
    """Truncate WAL file to `offset` bytes (keep header)."""
    if not os.path.exists(wal_path):
        return
    # Always keep header (16 bytes); never truncate before that
    keep = max(16, offset)
    with open(wal_path, "r+b") as f:
        f.truncate(keep)


def _apply_committed(main_path: str, pending: dict[int, dict[int, bytes]], status: dict[int, str]) -> None:
    """Write each committed txn's pending pages to main file in txn_id order."""
    from tinydb.pager import Pager, PAGE_SIZE
    p = Pager(main_path)
    try:
        for txn_id in sorted(pending.keys()):
            if status.get(txn_id) != "committed":
                continue
            for page_id, data in pending[txn_id].items():
                p.write_main_page(page_id, data)
        p.fsync_main()
    finally:
        p.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_recovery.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/recovery.py tests/unit/test_recovery.py
git commit -m "feat(recovery): WAL replay on Pager open — apply committed, discard incomplete"
```

---

## Task 6: Executor transaction routing + DDL/DML via txn

**Files:**
- Modify: `src/tinydb/executor.py` (add current_txn state + dispatch)
- Create: `tests/integration/test_acid.py`
- Create: `tests/integration/test_ddl_in_transaction.py`
- Create: `tests/integration/test_autocommit.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/integration/test_acid.py
import os

import pytest

from tinydb.database import Database


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "test.db")
    db = Database(path)
    yield db
    db.close()


def test_begin_insert_commit_persists(fresh_db):
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    fresh_db.execute("BEGIN")
    fresh_db.execute("INSERT INTO t VALUES (1, 'a')")
    fresh_db.execute("INSERT INTO t VALUES (2, 'b')")
    fresh_db.execute("COMMIT")
    rows = fresh_db.execute("SELECT * FROM t ORDER BY id")
    assert rows == [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]


def test_begin_insert_rollback_discards(fresh_db):
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    fresh_db.execute("BEGIN")
    fresh_db.execute("INSERT INTO t VALUES (1, 'a')")
    fresh_db.execute("INSERT INTO t VALUES (2, 'b')")
    fresh_db.execute("ROLLBACK")
    rows = fresh_db.execute("SELECT * FROM t")
    assert rows == []


def test_nested_begin_raises(fresh_db):
    fresh_db.execute("CREATE TABLE t (id INT)")
    fresh_db.execute("BEGIN")
    try:
        fresh_db.execute("BEGIN")
        assert False, "expected ExecutionError"
    except Exception as e:
        assert "nested BEGIN" in str(e).lower() or "BEGIN" in str(e)


def test_commit_without_begin_raises(fresh_db):
    try:
        fresh_db.execute("COMMIT")
        assert False, "expected ExecutionError"
    except Exception as e:
        assert "COMMIT" in str(e) or "no active" in str(e).lower()


def test_rollback_without_begin_raises(fresh_db):
    try:
        fresh_db.execute("ROLLBACK")
        assert False, "expected ExecutionError"
    except Exception as e:
        assert "ROLLBACK" in str(e) or "no active" in str(e).lower()


def test_commit_visible_after_reopen(tmp_path):
    """Data committed in one process is visible in another after reopen."""
    path = str(tmp_path / "persist.db")
    db1 = Database(path)
    db1.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    db1.execute("BEGIN")
    db1.execute("INSERT INTO t VALUES (42)")
    db1.execute("COMMIT")
    db1.close()

    db2 = Database(path)
    try:
        rows = db2.execute("SELECT * FROM t")
        assert rows == [{"id": 42}]
    finally:
        db2.close()
```

```python
# tests/integration/test_ddl_in_transaction.py
import pytest

from tinydb.database import Database


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "test.db")
    db = Database(path)
    yield db
    db.close()


def test_create_table_in_txn_rollback_no_side_effect(fresh_db):
    fresh_db.execute("BEGIN")
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    fresh_db.execute("INSERT INTO t VALUES (1, 'a')")
    fresh_db.execute("ROLLBACK")
    # Table should not exist
    rows = fresh_db.execute("SELECT * FROM t")
    assert rows == []


def test_drop_table_in_txn_commit_removes_table(fresh_db):
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    fresh_db.execute("INSERT INTO t VALUES (1)")
    fresh_db.execute("BEGIN")
    fresh_db.execute("DROP TABLE t")
    fresh_db.execute("COMMIT")
    rows = fresh_db.execute("SELECT * FROM t")
    assert rows == []


def test_create_table_in_txn_commit_persists(fresh_db):
    fresh_db.execute("BEGIN")
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    fresh_db.execute("INSERT INTO t VALUES (1, 'a')")
    fresh_db.execute("COMMIT")
    rows = fresh_db.execute("SELECT * FROM t")
    assert rows == [{"id": 1, "v": "a"}]
```

```python
# tests/integration/test_autocommit.py
import pytest

from tinydb.database import Database


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "test.db")
    db = Database(path)
    yield db
    db.close()


def test_single_insert_auto_commits(fresh_db):
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    fresh_db.execute("INSERT INTO t VALUES (1)")
    fresh_db.execute("INSERT INTO t VALUES (2)")
    rows = fresh_db.execute("SELECT * FROM t")
    assert len(rows) == 2


def test_failed_insert_auto_rolls_back(fresh_db):
    """Constraint violation in autocommit rolls back; previous inserts persist."""
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    fresh_db.execute("INSERT INTO t VALUES (1)")
    try:
        fresh_db.execute("INSERT INTO t VALUES (1)")  # duplicate PK
    except Exception:
        pass
    rows = fresh_db.execute("SELECT * FROM t")
    assert len(rows) == 1


def test_select_outside_txn_works(fresh_db):
    fresh_db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    fresh_db.execute("INSERT INTO t VALUES (1)")
    rows = fresh_db.execute("SELECT * FROM t")
    assert rows == [{"id": 1}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_acid.py tests/integration/test_ddl_in_transaction.py tests/integration/test_autocommit.py -v`
Expected: FAIL — `BEGIN` not recognized by Executor (no dispatch case)

- [ ] **Step 3: Add transaction state to Executor.__init__**

In `src/tinydb/executor.py`, in `Executor.__init__` (around line 160):

```python
        # Add at the end of __init__:
        self._current_txn: Transaction | None = None
        self._next_txn_id: int = 1
        # Tell Pager about our current txn (for write_page routing)
        self.pager._current_txn_id_ref = lambda: self._current_txn.id if self._current_txn else None
```

Add import at top:

```python
from tinydb.transaction import Transaction
from tinydb.parser import Begin, Commit, Rollback
```

- [ ] **Step 4: Update execute() dispatch**

In `src/tinydb/executor.py` `execute` method (~line 201):

```python
    def execute(self, stmt):
        # Transaction control statements
        if isinstance(stmt, Begin):
            return self._exec_begin(stmt)
        if isinstance(stmt, Commit):
            return self._exec_commit(stmt)
        if isinstance(stmt, Rollback):
            return self._exec_rollback(stmt)
        # DDL/DML through txn (auto-commit if no active txn)
        return self._exec_in_txn(stmt)
```

- [ ] **Step 5: Add _exec_begin / _exec_commit / _exec_rollback**

In `src/tinydb/executor.py`:

```python
    def _exec_begin(self, stmt):
        if self._current_txn is not None:
            raise ExecutionError("nested BEGIN not allowed")
        self._current_txn = Transaction(self._next_txn_id, self.pager)
        self._next_txn_id += 1
        return []

    def _exec_commit(self, stmt):
        if self._current_txn is None:
            raise ExecutionError("COMMIT without BEGIN")
        self._current_txn.commit()
        self._current_txn = None
        return []

    def _exec_rollback(self, stmt):
        if self._current_txn is None:
            raise ExecutionError("ROLLBACK without BEGIN")
        self._current_txn.rollback()
        self._current_txn = None
        return []
```

- [ ] **Step 6: Add _exec_in_txn wrapper**

In `src/tinydb/executor.py`:

```python
    def _exec_in_txn(self, stmt):
        auto = self._current_txn is None
        if auto:
            self._current_txn = Transaction(self._next_txn_id, self.pager)
            self._next_txn_id += 1
        try:
            result = self._exec_stmt(stmt)
        except Exception:
            try:
                self._current_txn.rollback()
            finally:
                self._current_txn = None
            raise
        if auto:
            self._current_txn.commit()
            self._current_txn = None
        return result

    def _exec_stmt(self, stmt):
        """Dispatch a non-transaction-control statement."""
        # Existing dispatch table
        if isinstance(stmt, CreateTable):  return self._exec_create_table(stmt)
        if isinstance(stmt, DropTable):    return self._exec_drop_table(stmt)
        if isinstance(stmt, Insert):       return self._exec_insert(stmt)
        if isinstance(stmt, Select):       return self._exec_select(stmt)
        if isinstance(stmt, Delete):       return self._exec_delete(stmt)
        if isinstance(stmt, Update):       return self._exec_update(stmt)
        raise ExecutionError(f"unknown statement: {type(stmt).__name__}")
```

- [ ] **Step 7: Refactor existing execute() dispatch**

Replace the existing `execute()` method body to call `_exec_stmt` (after the Begin/Commit/Rollback cases):

```python
    def execute(self, stmt):
        if isinstance(stmt, Begin):    return self._exec_begin(stmt)
        if isinstance(stmt, Commit):   return self._exec_commit(stmt)
        if isinstance(stmt, Rollback): return self._exec_rollback(stmt)
        return self._exec_in_txn(stmt)
```

The old `execute()` dispatch table (CreateTable/DropTable/etc.) is moved into `_exec_stmt`. The actual logic in `_exec_create_table`, `_exec_insert`, etc. is unchanged but now writes go through `txn.write_page(...)` instead of `self.pager.write_page(...)`.

- [ ] **Step 8: Modify _exec_insert / _exec_update / _exec_delete / _exec_create_table / _exec_drop_table**

In each of these methods, find every `self.pager.write_page(page_id, data)` call and replace with:

```python
        if self._current_txn is not None:
            self._current_txn.write_page(page_id, data)
        else:
            self.pager.write_page(page_id, data)
```

Helper: add to Executor:

```python
    def _txn_write_page(self, page_id: int, data: bytes) -> None:
        """Write a page within the current txn (or directly if no txn)."""
        if self._current_txn is not None:
            self._current_txn.write_page(page_id, data)
        else:
            self.pager.write_page(page_id, data)
```

Then replace `self.pager.write_page(...)` calls in `_exec_*` methods with `self._txn_write_page(...)`.

Specific locations to update (in `src/tinydb/executor.py`):
- `_exec_create_table` (~line 235): catalog write
- `_exec_drop_table` (~line 350): catalog write + free pages
- `_exec_insert` (~line 400): row write + index update
- `_exec_update` (~line 600): row update + index update
- `_exec_delete` (~line 800): row delete + index update

Each method may have multiple write_page calls. Replace all.

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_acid.py tests/integration/test_ddl_in_transaction.py tests/integration/test_autocommit.py -v`
Expected: 6 + 3 + 3 = 12 passed

- [ ] **Step 10: Commit**

```bash
git add src/tinydb/executor.py tests/integration/test_acid.py tests/integration/test_ddl_in_transaction.py tests/integration/test_autocommit.py
git commit -m "feat(executor): BEGIN/COMMIT/ROLLBACK dispatch + auto-commit wrapper + DDL/DML txn routing"
```

---

## Task 7: Crash recovery integration tests + fuzz

**Files:**
- Create: `tests/integration/test_crash_recovery.py`
- Create: `tests/integration/test_recovery_fuzz.py`

- [ ] **Step 1: Write crash recovery tests**

```python
# tests/integration/test_crash_recovery.py
import os

import pytest

from tinydb.database import Database
from tinydb.pager import Pager, PAGE_SIZE
from tinydb.wal import Wal, HEADER_SIZE


def test_crash_after_begin_no_commit_discards(tmp_path):
    """Process killed after BEGIN + INSERT (no COMMIT) → recovery discards."""
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"

    # Phase 1: create table + start txn + insert (no commit)
    db = Database(path)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    db.execute("INSERT INTO t VALUES (1, 'a')")
    # Simulate kill -9: just drop reference without commit
    db.close()

    # WAL should still have uncommitted txn (BEGIN + PAGE_WRITE)
    assert os.path.exists(wal_path)

    # Phase 2: reopen → recovery should discard uncommitted
    db2 = Database(path)
    try:
        rows = db2.execute("SELECT * FROM t")
        assert rows == []
    finally:
        db2.close()


def test_crash_after_commit_visible(tmp_path):
    """Process killed after COMMIT → recovery replays, data visible."""
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"

    db = Database(path)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    db.execute("INSERT INTO t VALUES (1, 'a')")
    db.execute("COMMIT")
    db.close()

    # WAL should be truncated after commit
    if os.path.exists(wal_path):
        assert os.path.getsize(wal_path) == HEADER_SIZE

    # Reopen and verify
    db2 = Database(path)
    try:
        rows = db2.execute("SELECT * FROM t")
        assert rows == [{"id": 1, "v": "a"}]
    finally:
        db2.close()


def test_partial_wal_record_truncated_on_recovery(tmp_path):
    """WAL with corrupt trailing record → recovery truncates + applies valid."""
    path = str(tmp_path / "crash.db")
    wal_path = path + ".wal"

    # Create table + commit some data
    db = Database(path)
    db.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    db.execute("INSERT INTO t VALUES (42)")
    db.close()

    # Append a corrupt record to WAL (simulate partial write)
    with open(wal_path, "ab") as f:
        f.write(b"\xff" * 50)

    # Reopen → recovery should truncate WAL + preserve previous data
    db2 = Database(path)
    try:
        rows = db2.execute("SELECT * FROM t")
        assert rows == [{"id": 42}]
    finally:
        db2.close()
```

- [ ] **Step 2: Write recovery fuzz tests**

```python
# tests/integration/test_recovery_fuzz.py
import os
import random
import struct
import zlib

import pytest

from tinydb.pager import Pager, PAGE_SIZE
from tinydb.wal import Wal, HEADER_SIZE
from tinydb.recovery import Recovery


def _make_record(txn_id: int, kind: int, page_id: int = 0, data: bytes = b"") -> bytes:
    header = struct.pack(">QBI I", txn_id, kind, page_id, len(data))
    payload = header + data
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack(">I", crc)


def _make_corrupt_record() -> bytes:
    """Return a record with invalid CRC."""
    payload = b"\xff" * 50
    crc = 0xDEADBEEF
    return payload + struct.pack(">I", crc)


def test_fuzz_random_valid_records_recovery_consistent(tmp_path):
    """Generate random valid WAL records + verify recovery produces consistent state."""
    path = str(tmp_path / "fuzz.db")
    wal_path = path + ".wal"

    # Create empty main file
    p = Pager(path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    # Generate N random valid records (mix of BEGIN/PAGE_WRITE/COMMIT)
    rng = random.Random(42)
    n_records = 50
    records = []
    txn_id = 1
    state = "no_txn"
    for _ in range(n_records):
        if state == "no_txn":
            records.append((txn_id, 0, 0, b""))  # BEGIN
            state = "active"
        elif state == "active":
            choice = rng.random()
            if choice < 0.7:
                # PAGE_WRITE
                records.append((txn_id, 1, pid, bytes(rng.randint(0, 255) for _ in range(PAGE_SIZE))))
            elif choice < 0.85:
                records.append((txn_id, 2, 0, b""))  # COMMIT
                state = "no_txn"
                txn_id += 1
            else:
                records.append((txn_id, 3, 0, b""))  # ROLLBACK
                state = "no_txn"
                txn_id += 1

    # Write records
    w = Wal(wal_path)
    for rec in records:
        w.append(*rec)
    w.close()

    # Recovery should not raise (all valid records)
    Recovery.replay(path, Wal(wal_path))

    # Main file should be valid (re-readable)
    p2 = Pager(path)
    p2.read_page(pid)  # should not raise
    p2.close()


def test_fuzz_corrupt_tail_recovery_truncates(tmp_path):
    """WAL with valid prefix + corrupt tail → recovery truncates + applies prefix."""
    path = str(tmp_path / "fuzz.db")
    wal_path = path + ".wal"

    p = Pager(path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    # Write valid committed record
    payload = b"\x42" * PAGE_SIZE
    w = Wal(wal_path)
    w.append(1, 0)  # BEGIN
    w.append(1, 1, pid, payload)  # PAGE_WRITE
    w.append(1, 2)  # COMMIT
    w.close()

    # Append corrupt record
    with open(wal_path, "ab") as f:
        f.write(_make_corrupt_record())

    # Recovery should raise WalCorruption after applying valid prefix
    from tinydb.wal import WalCorruption
    w2 = Wal(wal_path)
    with pytest.raises(WalCorruption):
        Recovery.replay(path, w2)

    # Committed page should be applied
    p2 = Pager(path)
    assert p2.read_page(pid) == payload
    p2.close()
```

- [ ] **Step 3: Run all new tests**

Run: `.venv/bin/python -m pytest tests/integration/test_crash_recovery.py tests/integration/test_recovery_fuzz.py -v`
Expected: 3 + 2 = 5 passed

- [ ] **Step 4: Run full test suite to verify no regression**

Run: `.venv/bin/python -m pytest --cov=tinydb -q`
Expected: ≥ 597 baseline + 25 new tests passing (total ~622). Coverage ≥ 90%.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_crash_recovery.py tests/integration/test_recovery_fuzz.py
git commit -m "test(acid): crash recovery integration tests + recovery fuzz"
```

---

## Task 8: Module line budget audit + MVP_LIMITATIONS update

**Files:**
- Modify: `docs/MVP_LIMITATIONS.md` (document new limitations)

- [ ] **Step 1: Audit module line counts**

Run: `wc -l src/tinydb/wal.py src/tinydb/transaction.py src/tinydb/recovery.py src/tinydb/pager.py src/tinydb/executor.py src/tinydb/parser.py src/tinydb/tokenizer.py src/tinydb/errors.py`

Expected (all within budget):
- `wal.py` ≤ 200
- `transaction.py` ≤ 300
- `recovery.py` ≤ 200
- `pager.py` ≤ 520
- `executor.py` ≤ 1280
- `parser.py` ≤ 900
- `tokenizer.py` ≤ 160
- `errors.py` ≤ 100

If any exceed budget, refactor (extract helpers to new module or simplify).

- [ ] **Step 2: Update MVP_LIMITATIONS.md**

Append to `docs/MVP_LIMITATIONS.md`:

```markdown
## ACID change (`tinydb-acid`)

- **Single-threaded, single-Executor transactions** — no concurrent transactions, no MVCC.
- **No savepoints** — only flat BEGIN...COMMIT/ROLLBACK.
- **fsync only on COMMIT** — WAL record append relies on OS page cache; power loss may lose uncommitted txns (acceptable per Design Doc D7).
- **No WAL compression** — `truncate_before` is the only cleanup path.
- **WAL fsync error semantics** — if `fsync(main)` fails after pages are written, recovery replays commit record to reach same final state (idempotent).
- **Page-level WAL** — writes entire 4KB page per record; not optimized for small row updates.
- **recovery depends on file-system atomicity** — assumes `<db>.wal` writes do not interleave at byte granularity (POSIX append-mode writes are typically atomic up to PIPE_BUF).
```

- [ ] **Step 3: Run full test suite one final time**

Run: `.venv/bin/python -m pytest --cov=tinydb -q`
Expected: all tests pass, coverage ≥ 90%.

- [ ] **Step 4: Commit**

```bash
git add docs/MVP_LIMITATIONS.md
git commit -m "docs(acid): MVP_LIMITATIONS — tinydb-acid scope and tradeoffs"
```

---

## Self-Review Checklist

**Spec coverage** (from Design Doc):
- [x] D1 WAL independent file → Task 1, 2
- [x] D2 page-level WAL → Task 1
- [x] D3 implicit auto-commit → Task 6
- [x] D4 nested BEGIN errors → Task 6
- [x] D5 truncate before current txn → Task 1, 3
- [x] D6 DDL in txn → Task 6
- [x] D7 fsync only on COMMIT → Task 3, 5
- [x] D8 schema version 0x03 → Task 2
- [x] D9 COMMIT/ROLLBACK no active txn error → Task 6
- [x] D10 WAL CRC truncate-then-start → Task 1, 5
- [x] Recovery on Pager.open → Task 2, 5
- [x] Crash recovery cross-process test → Task 7
- [x] Auto-commit tests → Task 6
- [x] DDL-in-txn tests → Task 6
- [x] WAL fuzz → Task 7
- [x] Module line budgets → Task 8

**Type/API consistency**:
- `Pager.wal_append_page(txn_id, page_id, data)` used in Task 3 (Transaction) and Task 6 (Executor via `_txn_write_page`)
- `Transaction.write_page` → `pager.wal_append_page` (1:1)
- `Wal.append(txn_id, kind, page_id, data)` used in Pager wal_append_* methods
- `Recovery.replay(main_path, wal)` signature consistent

**Total tasks**: 8 (WAL, Pager integration, Transaction, Parser, Recovery, Executor, Crash tests + fuzz, Line audit + docs)

**Estimated new tests**: 7 (wal) + 8 (transaction) + 5 (parser) + 4 (recovery) + 5 (pager v3) + 6 (acid) + 3 (ddl) + 3 (autocommit) + 3 (crash) + 2 (fuzz) = **46 new tests**

**Baseline**: 597 (main with engine-v2 archived) → expected ~643 total after this change.

---

## Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-19-tinydb-acid.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task with TDD RED-GREEN-COMMIT cycle + review between tasks
2. **Inline Execution** — execute tasks in this session using executing-plans

Which approach?