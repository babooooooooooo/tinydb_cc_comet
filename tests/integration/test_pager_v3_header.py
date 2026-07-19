"""Integration tests for Pager schema_version 0x03 (WAL-integrated) header.

Task 2: bumper-up to v3 schema + WAL integration methods.

Acceptance:
  1. SCHEMA_VERSION constant is 0x03.
  2. New files are written with schema byte 0x03.
  3. Existing v2 files are auto-upgraded in place on open (no WAL present).
  4. v2 files with WAL residue raise SchemaMismatch (forces explicit migration).
  5. write_main_page + fsync_main round-trip a page across open/reopen.
"""
import os
from pathlib import Path

from tinydb.errors import SchemaMismatch
from tinydb.pager import Pager, SCHEMA_VERSION


def test_pager_schema_version_is_3() -> None:
    """Acceptance 1: SCHEMA_VERSION constant reads 0x03."""
    assert SCHEMA_VERSION == 0x03


def test_pager_new_file_writes_v3_header(tmp_path: Path) -> None:
    """Acceptance 2: a freshly created db file has header byte 8 = 0x03."""
    db = tmp_path / "fresh.db"
    p = Pager(str(db))
    try:
        raw = p.read_page(0)
        assert raw[8] == 0x03
    finally:
        p.close()


def test_pager_upgrades_v2_header_on_open(tmp_path: Path) -> None:
    """Acceptance 3: opening a hand-crafted v2 file bumps header byte 8 to 0x03."""
    db = tmp_path / "v2.db"
    # Hand-craft a v2-shaped file (no WAL on disk): 8-byte magic + schema byte 0x02 +
    # free_list_head (4 bytes zero) + zero-padded to PAGE_SIZE + zero PAGE_SIZE
    # second page for catalog slot.
    header = b"TINYDB\x00\x02" + bytes([0x02]) + b"\x00" * 4 + b"\x00" * 4083
    body = header + b"\x00" * 4096
    db.write_bytes(body)
    # Sanity: opening should NOT require migration (no WAL residue).
    p = Pager(str(db))
    try:
        raw = p.read_page(0)
        assert raw[8] == 0x03  # schema_version bumped to 0x03
    finally:
        p.close()


def test_pager_raises_schema_mismatch_if_v2_with_wal_residue(tmp_path: Path) -> None:
    """Acceptance 4: v2 file + WAL residue -> SchemaMismatch on open."""
    db = tmp_path / "v2_with_wal.db"
    # Hand-craft a v2 file
    header = b"TINYDB\x00\x02" + bytes([0x02]) + b"\x00" * 4 + b"\x00" * 4083
    body = header + b"\x00" * 4096
    db.write_bytes(body)
    # Write a valid WAL alongside using the public Wal API.
    wal_path = str(db) + ".wal"
    from tinydb.wal import Wal
    wal = Wal(wal_path)
    wal.append(1, 1, page_id=0, data=b"")  # PAGE_WRITE kind
    wal.close()

    raised = False
    try:
        Pager(str(db))
    except SchemaMismatch as e:
        raised = True
        msg = str(e).lower()
        assert "0x02" in str(e) or "0x03" in str(e) or "schema" in msg
    finally:
        # Cleanup: remove WAL so test re-running doesn't conflict
        if os.path.exists(wal_path):
            os.remove(wal_path)
    assert raised, "expected SchemaMismatch when opening v2 db with WAL residue"


def test_pager_fsync_main_flushes_to_disk(tmp_path: Path) -> None:
    """Acceptance 5: write_main_page + fsync_main is durable across reopen."""
    db = tmp_path / "durable.db"
    # First session: allocate a page (so the file is sized properly), then
    # write a marker via the WAL-integrated API and fsync.
    p = Pager(str(db))
    try:
        pid = p.alloc_page()  # first data page id = 2
        sentinel = b"\xAB" * 4096
        p.write_main_page(pid, sentinel)
        p.fsync_main()
    finally:
        p.close()
    # Second session: read the marker back
    p2 = Pager(str(db))
    try:
        data = p2.read_page(pid)
        assert data == b"\xAB" * 4096
    finally:
        p2.close()
