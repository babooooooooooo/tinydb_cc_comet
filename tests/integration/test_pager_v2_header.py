"""Integration test for v1 -> v2 header auto-upgrade.

See plan Task 1: opening a v1 file must transparently upgrade the header
to v2 (magic version byte + schema_version + free_list_head=0).
"""
import os
from tinydb.pager import Pager, MAGIC, SCHEMA_VERSION


def test_v1_file_upgrades_header_on_open(tmp_path):
    db = tmp_path / "v1.db"
    # Write a v1 file by hand: 8-byte magic + 0x01 + zeros for PAGE_SIZE*2.
    page = MAGIC.replace(b"\x02", b"\x01") + bytes([0x01]) + b"\x00" * (4096 - 9) + b"\x00" * 4096
    db.write_bytes(page)
    p = Pager(str(db))
    raw = p.read_page(0)
    assert raw[7] == 0x02  # magic version byte upgraded
    assert raw[8] == 0x02  # schema_version upgraded
    assert raw[9:13] == b"\x00\x00\x00\x00"  # free_list_head = 0
    p.close()
