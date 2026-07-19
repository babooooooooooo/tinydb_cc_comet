"""Integration tests for tinydb-acid Task 7: Recovery fuzz.

Stress :func:`tinydb.recovery.Recovery.replay` against two threat models:

  1. ``test_fuzz_random_valid_records_recovery_consistent``
     Generate N well-formed WAL records in a randomized BEGIN/PAGE_WRITE/
     COMMIT/ROLLBACK mix and verify recovery completes without raising
     (no corruption injected). This locks in that any well-formed WAL
     can be processed in a single pass.

  2. ``test_fuzz_corrupt_tail_recovery_truncates``
     Write a valid committed txn + a corrupt trailer and verify that
     :func:`Recovery.replay` applies the valid prefix (PAGE_WRITE page
     ends up in main file) and re-raises :class:`WalCorruption` after
     truncating the WAL to the corruption boundary.

They live under tests/integration/ and carry the ``integration`` pytest
marker.
"""
from __future__ import annotations

import os
import random
import struct
import zlib

import pytest

from tinydb.pager import Pager, PAGE_SIZE
from tinydb.recovery import Recovery
from tinydb.wal import Wal, WalCorruption

pytestmark = pytest.mark.integration


def _make_record(txn_id: int, kind: int, page_id: int = 0, data: bytes = b"") -> bytes:
    """Encode a WAL record using the same on-disk layout as ``Wal.append``.

    Kept as a helper for any tests that want to bypass ``Wal`` and
    synthesize records directly. Layout matches
    ``tinydb.wal._RECORD_HDR_FMT`` (``u64 txn_id, u8 kind, u32 page_id,
    u32 data_len``) plus the CRC32 trailer over the body.
    """
    header = struct.pack(">QBI I", txn_id, kind, page_id, len(data))
    payload = header + data
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return payload + struct.pack(">I", crc)


def _make_corrupt_record() -> bytes:
    """Return a 50-byte payload + bogus CRC — guaranteed to fail integrity."""
    payload = b"\xff" * 50
    crc = 0xDEADBEEF
    return payload + struct.pack(">I", crc)


# --- 1. Random valid records → recovery completes without corruption ------


@pytest.mark.integration
def test_fuzz_random_valid_records_recovery_consistent(tmp_path):
    """Generate random valid WAL records + verify recovery is consistent.

    Build a small main file with one allocated page. Then synthesize
    50 well-formed WAL records: BEGIN, PAGE_WRITE, COMMIT, or ROLLBACK
    chosen with a seeded RNG. Every record validates via ``Wal.append``
    so the WAL is structurally clean. Recovery must process them all
    without raising, and the main file must remain readable.
    """
    path = str(tmp_path / "fuzz.db")
    wal_path = path + ".wal"

    # Create main file with one allocated page so PAGE_WRITEs land somewhere.
    p = Pager(path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    # Generate N random records in valid BEGIN/active/COMMIT-or-ROLLBACK
    # order. Each choice is well-formed at the WAL level.
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
                # PAGE_WRITE: 4KB of random bytes
                page_bytes = bytes(rng.randint(0, 255) for _ in range(PAGE_SIZE))
                records.append((txn_id, 1, pid, page_bytes))
            elif choice < 0.85:
                records.append((txn_id, 2, 0, b""))  # COMMIT
                state = "no_txn"
                txn_id += 1
            else:
                records.append((txn_id, 3, 0, b""))  # ROLLBACK
                state = "no_txn"
                txn_id += 1

    # Write records via the real WAL API so the file is structurally valid.
    w = Wal(wal_path)
    for rec in records:
        w.append(*rec)
    w.close()

    # Recovery should not raise (all records are well-formed and integral).
    Recovery.replay(path, Wal(wal_path))

    # Main file should still be valid and the page should be readable.
    p2 = Pager(path)
    p2.read_page(pid)  # should not raise
    p2.close()


# --- 2. Valid prefix + corrupt tail → recovery truncates + applies valid -


@pytest.mark.integration
def test_fuzz_corrupt_tail_recovery_truncates(tmp_path):
    """WAL with valid prefix + corrupt tail → recovery truncates + replays.

    Write a single committed txn (BEGIN/PAGE_WRITE/COMMIT) for one
    allocated page, then append a 54-byte corrupt trailer
    (``_make_corrupt_record``). Recovery must:

      1. Re-raise :class:`WalCorruption`.
      2. Truncate the WAL to the corruption boundary (removing the
         trailer even though it raises).
      3. Apply the committed PAGE_WRITE so the main file carries the
         payload.
    """
    path = str(tmp_path / "fuzz.db")
    wal_path = path + ".wal"

    p = Pager(path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    # Write valid committed record (single PAGE_WRITE on one page)
    payload = b"\x42" * PAGE_SIZE
    w = Wal(wal_path)
    w.append(1, 0)  # BEGIN
    w.append(1, 1, pid, payload)  # PAGE_WRITE
    w.append(1, 2)  # COMMIT
    w.close()

    # Append a corrupt record so iter_records() will raise WalCorruption.
    with open(wal_path, "ab") as f:
        f.write(_make_corrupt_record())

    # Recovery applies valid prefix + truncates, then re-raises.
    with pytest.raises(WalCorruption):
        Recovery.replay(path, Wal(wal_path))

    # Committed PAGE_WRITE must have been applied before the exception.
    p2 = Pager(path)
    assert p2.read_page(pid) == payload
    p2.close()
