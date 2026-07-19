import os

import pytest

from tinydb.recovery import Recovery
from tinydb.wal import Wal, HEADER_SIZE
from tinydb.pager import Pager, PAGE_SIZE


def _make_wal_file(path: str, records: list[tuple[int, int, int, bytes]]) -> None:
    w = Wal(path)
    for txn_id, kind, page_id, data in records:
        w.append(txn_id, kind, page_id, data)
    w.close()


def test_recovery_apply_committed_pages_to_main(tmp_path):
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    p = Pager(main_path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    payload = b"\xab" * PAGE_SIZE
    _make_wal_file(wal_path, [
        (1, 0, 0, b""),                # BEGIN
        (1, 1, pid, payload),          # PAGE_WRITE
        (1, 2, 0, b""),                # COMMIT
    ])

    Recovery.replay(main_path, Wal(wal_path))
    p2 = Pager(main_path)
    assert p2.read_page(pid) == payload
    p2.close()
    # WAL should be truncated after replay (only header remains)
    assert os.path.getsize(wal_path) == HEADER_SIZE


def test_recovery_discards_uncommitted_txn(tmp_path):
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    p = Pager(main_path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    _make_wal_file(wal_path, [
        (1, 0, 0, b""),                                # BEGIN
        (1, 1, pid, b"\xde\xad\xbe\xef" * 1024),      # PAGE_WRITE, no commit
    ])

    Recovery.replay(main_path, Wal(wal_path))
    p2 = Pager(main_path)
    assert p2.read_page(pid) != b"\xde\xad\xbe\xef" * 1024
    p2.close()


def test_recovery_truncates_corrupt_tail(tmp_path):
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    p = Pager(main_path)
    pid = p.alloc_page()
    p.flush()
    p.close()

    payload = b"\x42" * PAGE_SIZE
    _make_wal_file(wal_path, [
        (1, 0, 0, b""),
        (1, 1, pid, payload),
        (1, 2, 0, b""),
    ])
    # Append junk to simulate partial write
    with open(wal_path, "ab") as f:
        f.write(b"\xff\xff\xff\xff\xff\xff\xff\xff")

    from tinydb.wal import WalCorruption
    w = Wal(wal_path)
    with pytest.raises(WalCorruption):
        Recovery.replay(main_path, w)
    # Page from committed txn should still be applied
    p2 = Pager(main_path)
    assert p2.read_page(pid) == payload
    p2.close()


def test_recovery_empty_wal_is_noop(tmp_path):
    main_path = str(tmp_path / "db.db")
    wal_path = main_path + ".wal"
    p = Pager(main_path)
    p.flush()
    p.close()

    w = Wal(wal_path)
    w.close()

    # Should not raise
    Recovery.replay(main_path, Wal(wal_path))
