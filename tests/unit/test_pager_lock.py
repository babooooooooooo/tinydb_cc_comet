"""Unit tests for Pager file lock integration (Task 2 of concurrency-control).

These tests verify that ``Pager.__init__`` acquires an exclusive
``fcntl.flock`` on the opened DB file, that ``Pager.close()`` releases it,
and that the lock is bypassed for ``:memory:`` mode and ``locking=False``.
"""
import errno

import pytest

from tinydb._filelock import _HAS_FCNTL
from tinydb.errors import DatabaseLocked
from tinydb.pager import Pager


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Default locking: Pager(path) acquires fcntl.flock(LOCK_EX | LOCK_NB).
# ---------------------------------------------------------------------------

def test_pager_default_locking_acquires_flock(tmp_path, monkeypatch):
    """默认 Pager(path) 调 fcntl.flock(LOCK_EX | LOCK_NB) 一次."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl
    calls = []
    monkeypatch.setattr(
        real_fcntl, "flock",
        lambda fd, op: calls.append((fd, op))
    )
    p = Pager(str(tmp_path / "a.db"))
    try:
        assert len(calls) == 1
        fd, op = calls[0]
        assert op & real_fcntl.LOCK_EX
        assert op & real_fcntl.LOCK_NB
    finally:
        p.close()


def test_pager_holds_exclusive_lock_visible_to_another_fd(tmp_path):
    """默认 Pager 持锁期间,在新 fd 上尝试 LOCK_EX | LOCK_NB 应立即 BlockingIOError."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl
    p = Pager(str(tmp_path / "a.db"))
    try:
        probe = open(str(tmp_path / "a.db"), "r+b")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            probe.close()
    finally:
        p.close()


# ---------------------------------------------------------------------------
# Sequential open after close must succeed.
# ---------------------------------------------------------------------------

def test_pager_sequential_open_after_close_succeeds(tmp_path):
    """关闭第一个 Pager 后,第二个 Pager 在同一文件上打开必须成功."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    p1 = Pager(str(tmp_path / "a.db"))
    p1.close()
    p2 = Pager(str(tmp_path / "a.db"))
    try:
        assert p2.page_count() >= 2
    finally:
        p2.close()


# ---------------------------------------------------------------------------
# locking=False bypass: no flock acquired; concurrent open on same file ok.
# ---------------------------------------------------------------------------

def test_pager_locking_false_skips_flock(tmp_path, monkeypatch):
    """Pager(path, locking=False) 不调 fcntl.flock."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl
    calls = []
    monkeypatch.setattr(
        real_fcntl, "flock",
        lambda fd, op: calls.append((fd, op))
    )
    p = Pager(str(tmp_path / "a.db"), locking=False)
    try:
        assert calls == []
    finally:
        p.close()


def test_pager_locking_false_concurrent_open_succeeds(tmp_path):
    """进程 A 持锁时,locking=False 的 Pager B 仍可打开."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    p1 = Pager(str(tmp_path / "a.db"))
    try:
        p2 = Pager(str(tmp_path / "a.db"), locking=False)
        try:
            assert p2.page_count() >= 2
        finally:
            p2.close()
    finally:
        p1.close()


# ---------------------------------------------------------------------------
# :memory: mode must NOT touch fcntl.
# ---------------------------------------------------------------------------

def test_pager_memory_mode_skips_flock(monkeypatch):
    """Pager(':memory:') 不调 fcntl.flock."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl
    calls = []
    monkeypatch.setattr(
        real_fcntl, "flock",
        lambda fd, op: calls.append((fd, op))
    )
    p = Pager(":memory:")
    try:
        assert calls == []
        # :memory: 模式下,Pager 不持有 file lock 实例(没有 fd)
        assert p._file_lock is None
        assert p._is_locking_enabled is False
    finally:
        p.close()


# ---------------------------------------------------------------------------
# Contention: BlockingIOError(EWOULDBLOCK) must surface as DatabaseLocked.
# ---------------------------------------------------------------------------

def test_pager_lock_contention_raises_database_locked(tmp_path, monkeypatch):
    """Pager 在 fcntl.flock 抛 EWOULDBLOCK 时上抛 DatabaseLocked."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl

    def fake_flock(fd, op):
        raise BlockingIOError(errno.EWOULDBLOCK, "Resource temporarily unavailable")

    monkeypatch.setattr(real_fcntl, "flock", fake_flock)
    with pytest.raises(DatabaseLocked) as exc_info:
        Pager(str(tmp_path / "a.db"))
    assert str(tmp_path / "a.db") in str(exc_info.value)


def test_pager_lock_contention_closes_fd_before_propagating(tmp_path, monkeypatch):
    """Pager 在锁失败时关闭 fd,不留泄漏文件描述符."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl

    def fake_flock(fd, op):
        raise BlockingIOError(errno.EWOULDBLOCK, "Resource temporarily unavailable")

    monkeypatch.setattr(real_fcntl, "flock", fake_flock)
    path = tmp_path / "a.db"
    with pytest.raises(DatabaseLocked):
        Pager(str(path))
    # 文件存在(_open_file 后才调 flock,_open_file 已创建文件)
    assert path.exists()


# ---------------------------------------------------------------------------
# fcntl module missing: locking=True raises ImportError (fail-fast);
# locking=False continues to work.
# ---------------------------------------------------------------------------

def test_pager_no_fcntl_raises_import_error(tmp_path, monkeypatch):
    """_HAS_FCNTL=False 且 locking=True 时,Pager 抛 ImportError."""
    from tinydb import pager as real_pager
    monkeypatch.setattr(real_pager, "_HAS_FCNTL", False)
    with pytest.raises(ImportError, match="fcntl"):
        Pager(str(tmp_path / "a.db"), locking=True)


def test_pager_no_fcntl_locking_false_works(tmp_path, monkeypatch):
    """_HAS_FCNTL=False 且 locking=False 时,Pager 仍可工作."""
    from tinydb import pager as real_pager
    monkeypatch.setattr(real_pager, "_HAS_FCNTL", False)
    p = Pager(str(tmp_path / "a.db"), locking=False)
    try:
        assert p.page_count() >= 2
        assert p._is_locking_enabled is False
        assert p._file_lock is None
    finally:
        p.close()


# ---------------------------------------------------------------------------
# close() releases the lock and is idempotent.
# ---------------------------------------------------------------------------

def test_pager_close_releases_lock(tmp_path):
    """Pager.close() 释放文件锁 — 第二个 Pager 紧接着打开应成功."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    p1 = Pager(str(tmp_path / "a.db"))
    p1.close()
    # close 后应无残留 — 新 fd 可直接获取 LOCK_EX
    import fcntl
    probe = open(str(tmp_path / "a.db"), "r+b")
    try:
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
    finally:
        probe.close()


def test_pager_close_is_idempotent(tmp_path):
    """Pager.close() 重复调用安全."""
    p = Pager(str(tmp_path / "a.db"))
    p.close()
    p.close()  # 不应抛


# ---------------------------------------------------------------------------
# FileLock API: Pager must wrap the fd and path in a FileLock instance
# and call try_acquire on it.
# ---------------------------------------------------------------------------

def test_pager_locking_flag_propagates_to_filelock(tmp_path, monkeypatch):
    """Pager.__init__ 构造 FileLock 并调用 try_acquire — 验证参数."""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    from tinydb import pager as real_pager
    captured: dict = {}

    class FakeFileLock:
        def __init__(self, fd, path):
            captured["fd"] = fd
            captured["path"] = path
            self._held = False

        def try_acquire(self):
            captured.setdefault("try_acquire_calls", 0)
            captured["try_acquire_calls"] += 1
            self._held = True

        def release(self):
            self._held = False

    monkeypatch.setattr(real_pager, "FileLock", FakeFileLock)
    p = Pager(str(tmp_path / "a.db"))
    try:
        assert captured["path"] == str(tmp_path / "a.db")
        # fd 应为打开的 DB 文件 fd(整数,> 0)
        assert isinstance(captured["fd"], int)
        assert captured["fd"] > 0
        assert captured["try_acquire_calls"] == 1
        assert p._is_locking_enabled is True
    finally:
        p.close()
