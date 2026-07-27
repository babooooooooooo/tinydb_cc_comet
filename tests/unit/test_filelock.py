"""Unit tests for tinydb._filelock.FileLock (Task 1.1/1.3)."""
import errno
import os
import pytest

from tinydb._filelock import FileLock, _HAS_FCNTL
from tinydb.errors import DatabaseLocked


pytestmark = pytest.mark.unit


def test_filelock_acquire_and_release_roundtrip(tmp_path):
    """第一次 try_acquire 必须成功；release 后再次 acquire 也成功。"""
    f = open(tmp_path / "x.db", "w+")
    try:
        lock = FileLock(f.fileno(), str(tmp_path / "x.db"))
        lock.try_acquire()
        assert lock._held is True
        lock.release()
        assert lock._held is False
        lock.try_acquire()  # 第二次也成功
        lock.release()
    finally:
        f.close()


def test_filelock_release_idempotent(tmp_path):
    """release 在未持有时调用安全（幂等）。"""
    f = open(tmp_path / "x.db", "w+")
    try:
        lock = FileLock(f.fileno(), str(tmp_path / "x.db"))
        lock.release()  # 未持有
        assert lock._held is False
    finally:
        f.close()


def test_filelock_try_acquire_idempotent(tmp_path):
    """重复 try_acquire 不抛异常。"""
    f = open(tmp_path / "x.db", "w+")
    try:
        lock = FileLock(f.fileno(), str(tmp_path / "x.db"))
        lock.try_acquire()
        lock.try_acquire()  # 已持有
        assert lock._held is True
        lock.release()
    finally:
        f.close()


def test_filelock_context_manager(tmp_path):
    """__enter__/__exit__ 等同 try_acquire/release。"""
    f = open(tmp_path / "x.db", "w+")
    try:
        with FileLock(f.fileno(), str(tmp_path / "x.db")) as lock:
            assert lock._held is True
        assert lock._held is False
    finally:
        f.close()


def test_filelock_raises_database_locked_on_ewouldblock(tmp_path, monkeypatch):
    """模拟 fcntl.flock 抛 BlockingIOError(EWOULDBLOCK) → DatabaseLocked。"""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl

    def fake_flock(fd, op):
        if op & real_fcntl.LOCK_EX:
            raise BlockingIOError(errno.EWOULDBLOCK, "Resource temporarily unavailable")

    monkeypatch.setattr(real_fcntl, "flock", fake_flock)
    f = open(tmp_path / "x.db", "w+")
    try:
        lock = FileLock(f.fileno(), str(tmp_path / "x.db"))
        with pytest.raises(DatabaseLocked) as exc_info:
            lock.try_acquire()
        assert str(tmp_path / "x.db") in str(exc_info.value)
        assert exc_info.value.path == str(tmp_path / "x.db")
    finally:
        f.close()


def test_filelock_raises_database_locked_on_eagain(tmp_path, monkeypatch):
    """模拟 EAGAIN → DatabaseLocked（同 EWOULDBLOCK 分支）。"""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl

    def fake_flock(fd, op):
        raise BlockingIOError(errno.EAGAIN, "Try again")

    monkeypatch.setattr(real_fcntl, "flock", fake_flock)
    f = open(tmp_path / "x.db", "w+")
    try:
        lock = FileLock(f.fileno(), str(tmp_path / "x.db"))
        with pytest.raises(DatabaseLocked):
            lock.try_acquire()
    finally:
        f.close()


def test_filelock_raises_database_locked_on_einval(tmp_path, monkeypatch):
    """模拟 WSL1 / 不支持 flock 的 FS → OSError(EINVAL) → DatabaseLocked。"""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl

    def fake_flock(fd, op):
        raise OSError(errno.EINVAL, "Invalid argument")

    monkeypatch.setattr(real_fcntl, "flock", fake_flock)
    f = open(tmp_path / "x.db", "w+")
    try:
        lock = FileLock(f.fileno(), str(tmp_path / "x.db"))
        with pytest.raises(DatabaseLocked):
            lock.try_acquire()
    finally:
        f.close()


def test_filelock_propagates_other_oserror(tmp_path, monkeypatch):
    """非 EWOULDBLOCK/EAGAIN/EINVAL 的 OSError 透传（不被吞）。"""
    if not _HAS_FCNTL:
        pytest.skip("requires fcntl")
    import fcntl as real_fcntl

    def fake_flock(fd, op):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(real_fcntl, "flock", fake_flock)
    f = open(tmp_path / "x.db", "w+")
    try:
        lock = FileLock(f.fileno(), str(tmp_path / "x.db"))
        with pytest.raises(OSError) as exc_info:
            lock.try_acquire()
        assert exc_info.value.errno == errno.EACCES
    finally:
        f.close()


def test_filelock_release_on_closed_fd_is_safe(tmp_path):
    """release 在 fd 已关闭时调用安全（Linux 静默成功）。"""
    f = open(tmp_path / "x.db", "w+")
    fd = f.fileno()
    lock = FileLock(fd, str(tmp_path / "x.db"))
    lock.try_acquire()
    f.close()  # 关闭 fd
    # Lock 对象的 _held 仍为 True；release 会对已关闭 fd 调 fcntl LOCK_UN
    # Linux 上静默成功；不应抛 OSError
    lock.release()
    assert lock._held is False


def test_filelock_has_fcntl_module_flag_exists():
    """模块级 _HAS_FCNTL 标志存在（True or False）— 实现层降级开关。"""
    assert isinstance(_HAS_FCNTL, bool)


def test_database_locked_is_subclass_of_tinydb_error():
    """DatabaseLocked 必须继承 TinydbError(公共 API 异常契约)."""
    from tinydb.errors import TinydbError
    assert issubclass(DatabaseLocked, TinydbError)


def test_database_locked_carries_path_attribute():
    """DatabaseLocked.path 属性 == 构造时传入的路径."""
    exc = DatabaseLocked("/tmp/foo.db")
    assert exc.path == "/tmp/foo.db"
    assert "/tmp/foo.db" in str(exc)


def test_database_locked_is_importable_from_top_level():
    """DatabaseLocked 通过 tinydb package 入口可导入(spec contract REQ-LOCK-005)."""
    import tinydb
    assert hasattr(tinydb, "DatabaseLocked")
    assert tinydb.DatabaseLocked is DatabaseLocked
