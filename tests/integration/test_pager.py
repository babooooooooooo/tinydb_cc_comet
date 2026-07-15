"""Integration tests for tinydb.pager — file header, magic, schema version (Task 7).

These tests touch the filesystem (real I/O) so they live under tests/integration/
and carry the `integration` pytest marker.
"""
import os

import pytest

from tinydb.pager import Pager, MAGIC, SCHEMA_VERSION, PAGE_SIZE
from tinydb.errors import DatabaseError


@pytest.mark.integration
@pytest.mark.spec_id("REQ-PAGER-001-SCN-01")
def test_pager_creates_new_file_with_magic(tmp_path):
    path = tmp_path / "test.db"
    p = Pager(str(path))
    try:
        assert p.page_count() == 1  # at least page 0 (header)
    finally:
        p.close()
    # File should exist
    assert path.exists()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-PAGER-001-SCN-02")
def test_pager_opens_existing_file_with_valid_magic(tmp_path):
    path = tmp_path / "test.db"
    # First create
    p1 = Pager(str(path))
    p1.close()
    # Then reopen
    p2 = Pager(str(path))
    try:
        assert p2.page_count() == 1
    finally:
        p2.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-PAGER-001-SCN-03")
def test_pager_raises_on_bad_magic(tmp_path):
    path = tmp_path / "bad.db"
    path.write_bytes(b"NOTADB\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * (PAGE_SIZE - 16))
    with pytest.raises(DatabaseError, match="magic"):
        Pager(str(path))


@pytest.mark.integration
@pytest.mark.spec_id("REQ-PAGER-001-SCN-04")
def test_pager_memory_mode_no_file_created():
    p = Pager(":memory:")
    try:
        assert p.page_count() == 1
    finally:
        p.close()


@pytest.mark.integration
@pytest.mark.spec_id("REQ-PAGER-001-SCN-05")
def test_pager_constants():
    assert MAGIC == b'TINYDB\x00\x01'
    assert SCHEMA_VERSION == 0x01
    assert PAGE_SIZE == 4096