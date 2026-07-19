"""Integration test for v1 -> v3 header auto-upgrade.

Originally (engine-v2): opening a v1 file must transparently upgrade the
header to v2 (magic version byte + schema_version + free_list_head=0).

After tinydb-acid Task 2: we skip v2 entirely; v1 files are auto-upgraded
to v3 (magic version 0x03 + schema_version 0x03 + free_list_head = 0).
"""
import os
from tinydb.pager import Pager, MAGIC, SCHEMA_VERSION


def test_v1_file_upgrades_header_on_open(tmp_path):
    db = tmp_path / "v1.db"
    # Write a v1 file by hand: 8-byte v1 magic + schema byte 0x01 + zeros for PAGE_SIZE*2.
    v1_magic = MAGIC.replace(b"\x03", b"\x01")
    page = v1_magic + bytes([0x01]) + b"\x00" * (4096 - 9) + b"\x00" * 4096
    db.write_bytes(page)
    p = Pager(str(db))
    raw = p.read_page(0)
    assert raw[7] == 0x03  # magic version byte upgraded to v3
    assert raw[8] == 0x03  # schema_version upgraded to 0x03
    assert raw[9:13] == b"\x00\x00\x00\x00"  # free_list_head = 0
    p.close()
