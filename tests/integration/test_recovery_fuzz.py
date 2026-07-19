"""Integration tests for tinydb-acid Task 7: Recovery fuzz.

Stress :func:`tinydb.recovery.Recovery.replay` against two threat models:

  1. ``test_fuzz_random_valid_records_recovery_consistent``
     Generate N well-formed WAL records in a randomized PAGE_WRITE/
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

import pytest

from tinydb.pager import Pager, PAGE_SIZE
from tinydb.recovery import Recovery
from tinydb.wal import Wal, WalCorruption

pytestmark = pytest.mark.integration


def _make_corrupt_record() -> bytes:
    """Return a 50-byte payload + bogus CRC — guaranteed to fail integrity."""
    payload = b"\xff" * 50
    crc = 0xDEADBEEF
    return payload + crc.to_bytes(4, "big")


# --- 1. Random valid records → recovery completes without corruption ------


def test_fuzz_random_valid_records_recovery_consistent(tmp_path):
    """Generate random valid WAL records + verify recovery is consistent.

    Build a small main file with one allocated page. Then synthesize
    50 well-formed WAL records: PAGE_WRITE, COMMIT, or ROLLBACK chosen
    with a seeded RNG. (Production ``Transaction`` does NOT emit a
    BEGIN record — see ``src/tinydb/transaction.py`` — so the fuzz
    generator mirrors production and omits BEGIN.) Every record
    validates via ``Wal.append`` so the WAL is structurally clean.
    Recovery must process them all without raising, AND committed
    PAGE_WRITEs must be applied to the main file (not just made
    readable).
    """
    path = str(tmp_path / "fuzz.db")
    wal_path = path + ".wal"

    # Capture the pre-fuzz page contents (zeros for a fresh alloc).
    p = Pager(path)
    pid = p.alloc_page()
    initial_page = bytes(p.read_page(pid))
    assert initial_page == b"\x00" * PAGE_SIZE  # sanity: clean
    p.close()

    # Generate N random records in valid PAGE_WRITE / COMMIT-or-ROLLBACK
    # order. Each choice is well-formed at the WAL level. The generator
    # mirrors production (no BEGIN record).
    rng = random.Random(42)
    n_records = 50
    records = []
    txn_id = 1
    state = "no_txn"
    for _ in range(n_records):
        if state == "no_txn":
            choice = rng.random()
            if choice < 0.7:
                # PAGE_WRITE: 4KB of random bytes
                page_bytes = bytes(rng.randint(0, 255) for _ in range(PAGE_SIZE))
                records.append((txn_id, 1, pid, page_bytes))
                state = "active"
            else:
                # Skip — we only start a txn with a write, mirroring
                # production (COMMIT without a preceding PAGE_WRITE
                # would be malformed).
                continue
        elif state == "active":
            choice = rng.random()
            if choice < 0.7:
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

    # Track the LAST committed PAGE_WRITE for this page so we can assert
    # recovery actually applied the payload (not just kept the file
    # readable).
    last_committed_payload: bytes | None = None
    cur_payload: bytes | None = None
    txn_state: dict[int, str] = {}
    for tid, kind, _, data in records:
        if kind == 1:
            cur_payload = data
        elif kind == 2:
            if cur_payload is not None:
                last_committed_payload = cur_payload
            txn_state[tid] = "committed"
        elif kind == 3:
            txn_state[tid] = "rolled_back"
            cur_payload = None  # discard pending on rollback

    # Write records via the real WAL API so the file is structurally valid.
    w = Wal(wal_path)
    for rec in records:
        w.append(*rec)
    w.close()

    # Recovery should not raise (all records are well-formed and integral).
    with Wal(wal_path) as recovery_wal:
        Recovery.replay(path, recovery_wal)

    # Main file must carry the last committed payload — proving recovery
    # applied the WAL writes, not just kept the file openable.
    p2 = Pager(path)
    try:
        if last_committed_payload is not None:
            assert p2.read_page(pid) == last_committed_payload
        else:
            # No committed writes in this run — page must still be the
            # initial zero page (recovery must not have corrupted it).
            assert p2.read_page(pid) == initial_page
    finally:
        p2.close()


# --- 2. Valid prefix + corrupt tail → recovery truncates + applies valid -


def test_fuzz_corrupt_tail_recovery_truncates(tmp_path):
    """WAL with valid prefix + corrupt tail → recovery truncates + replays.

    Write a single committed txn (PAGE_WRITE/COMMIT) for one allocated
    page, then append a 54-byte corrupt trailer
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

    # Write valid committed record (single PAGE_WRITE on one page).
    payload = b"\x42" * PAGE_SIZE
    with Wal(wal_path) as w:
        w.append(1, 1, pid, payload)  # PAGE_WRITE
        w.append(1, 2)  # COMMIT

    # Capture size AFTER the valid prefix is on disk, BEFORE the corrupt
    # trailer — recovery must truncate to <= this size so the trailer is
    # gone.
    pre_corrupt_size = os.path.getsize(wal_path)

    # Append a corrupt record so iter_records() will raise WalCorruption.
    with open(wal_path, "ab") as f:
        f.write(_make_corrupt_record())

    # Recovery applies valid prefix + truncates, then re-raises.
    with pytest.raises(WalCorruption):
        with Wal(wal_path) as recovery_wal:
            Recovery.replay(path, recovery_wal)

    # WAL must have been truncated to the corruption boundary — its size
    # must not exceed the pre-corrupt size (corrupt trailer is gone).
    assert os.path.getsize(wal_path) <= pre_corrupt_size
    # Committed PAGE_WRITE must have been applied before the exception.
    p2 = Pager(path)
    try:
        assert p2.read_page(pid) == payload
    finally:
        p2.close()
