"""Compatibility: v2-schema .db files (no WAL sidecar) must still open and operate.

Background
----------
``tinydb-engine-v2`` writes schema byte ``0x02``. The ``tinydb-acid`` change
bumps the on-disk schema to ``0x03`` and adds a WAL sidecar. Without a
back-compat path, every existing ``tinydb-engine-v2`` database would refuse
to open after an upgrade.

The compatibility contract (:meth:`Pager._open_file`):

* v2 file (magic ``b'TINYDB\\x00\\x02'`` + schema byte ``0x02``) **without**
  a ``.wal`` sidecar  -> header byte 8 is auto-bumped to ``0x03`` in place.
* v2 file **with** a ``.wal`` sidecar -> :class:`SchemaMismatch` is raised
  (forces explicit migration).

This module exercises that contract end-to-end through the public Database
API, asserting both the auto-upgrade path AND that subsequent DDL on the
upgraded file works normally.

They live under ``tests/integration/`` and carry the ``integration`` pytest
marker.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tinydb import Database
from tinydb.errors import SchemaMismatch
from tinydb.pager import PAGE_SIZE, Pager, SCHEMA_VERSION

pytestmark = pytest.mark.integration


def _write_v2_db(path: Path, with_wal: bool = False) -> None:
    """Hand-craft a v2-shaped file (and optionally a WAL sidecar).

    Layout: ``b'TINYDB\\x00\\x02'`` magic + schema byte ``0x02`` + free_list_head
    (4 zero bytes) + zero-padded to PAGE_SIZE + one extra zero PAGE_SIZE for
    the catalog slot.
    """
    header = b"TINYDB\x00\x02" + bytes([0x02]) + b"\x00" * 4 + b"\x00" * 4083
    body = header + b"\x00" * PAGE_SIZE
    path.write_bytes(body)
    if with_wal:
        from tinydb.wal import Wal

        wal = Wal(str(path) + ".wal")
        # Append one PAGE_WRITE to make the sidecar non-empty; the precise
        # content does not matter, only that the file exists and is openable.
        wal.append(1, 1, page_id=0, data=b"")
        wal.close()


def test_v2_db_without_wal_opens_and_runs(tmp_path: Path) -> None:
    """v2-schema .db file with no WAL sidecar must open without SchemaMismatch.

    Back-compat: ``tinydb-engine-v2`` writes schema byte ``0x02`` without a
    WAL. The acid change must (a) auto-upgrade the header byte to ``0x03``
    in place, (b) NOT raise ``SchemaMismatch``, and (c) accept new DDL
    against the upgraded file.
    """
    path = tmp_path / "v2.db"
    _write_v2_db(path, with_wal=False)
    assert not (path.with_name(path.name + ".wal")).exists()

    # Open via Database (full pipeline: Pager + Catalog + IndexManager).
    with Database(str(path)) as db:
        # Header byte 8 must now read 0x03 (auto-upgraded in place).
        raw_header = db.pager.read_page(0)
        assert raw_header[8] == 0x03
        assert raw_header[8] == SCHEMA_VERSION

        # New DDL must work against the upgraded file (no recovery needed
        # because no WAL sidecar was present at open time).
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, v TEXT)")

    # Reopen the upgraded file and confirm the header byte stays at 0x03
    # across processes (auto-upgrade is in-place, not one-shot).
    with Database(str(path)) as db2:
        raw_header2 = db2.pager.read_page(0)
        assert raw_header2[8] == SCHEMA_VERSION


def test_v2_db_with_wal_residue_raises_schema_mismatch(tmp_path: Path) -> None:
    """v2-schema .db file WITH a WAL sidecar must raise SchemaMismatch.

    Counterpart to the auto-upgrade path: if a ``.wal`` is present alongside a
    v2 file we cannot safely auto-upgrade (the WAL may carry records written
    by an older ``tinydb-engine-v2`` that no longer match v3's record
    layout). The Database opener must raise :class:`SchemaMismatch` so the
    caller can run an explicit migration instead of silently corrupting state.
    """
    path = tmp_path / "v2_with_wal.db"
    _write_v2_db(path, with_wal=True)
    assert (path.parent / (path.name + ".wal")).exists()

    raised = False
    try:
        Database(str(path))
    except SchemaMismatch:
        raised = True
    finally:
        # Cleanup: remove the WAL so test re-runs do not see residue.
        wal_path = str(path) + ".wal"
        if os.path.exists(wal_path):
            os.remove(wal_path)
    assert raised, "expected SchemaMismatch when opening v2 db with WAL residue"
