"""fcntl.flock 的薄包装,提供 context manager API.

由 Pager 调用以获取 DB 文件上的独占 OS 锁.EWOULDBLOCK 时抛
DatabaseLocked.当平台不支持 fcntl 时(Windows),所有公开方法为 no-op.

模块级降级:若 ``import fcntl`` 失败,设置 ``_HAS_FCNTL=False``;Database 在
``locking=True`` 且 path 不为 ":memory:" 时检查此标志,缺则抛 ImportError.
"""
from __future__ import annotations

import errno as _errno

try:
    import fcntl  # noqa: F401
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

from tinydb.errors import DatabaseLocked


class FileLock:
    """per-fd 文件锁,提供 try_acquire / release 语义."""

    def __init__(self, fd: int, path: str) -> None:
        self._fd = fd
        self._path = path
        self._held = False

    def try_acquire(self) -> None:
        """获取 LOCK_EX | LOCK_NB;竞争时抛 DatabaseLocked."""
        if self._held:
            return  # 幂等
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._held = True
        except (BlockingIOError, OSError) as e:
            # EWOULDBLOCK / EAGAIN 竞争;EINVAL 在 WSL1 或不支持 flock 的
            # 文件系统上触发.两种都向调用方抛 DatabaseLocked;path 属性
            # 帮助定位被争用的 DB.
            if e.errno in (_errno.EWOULDBLOCK, _errno.EAGAIN, _errno.EINVAL):
                raise DatabaseLocked(self._path) from e
            raise

    def release(self) -> None:
        """释放锁(LOCK_UN).幂等;close 后调用安全.

        fd 已关闭时(Linux EBADF)静默成功 — 调用方不需要关心 close 顺序.
        """
        if not self._held:
            return
        try:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError as e:
                # fd 已 close 后调用 release:EBADF 是预期的.
                # 其他 OSError(罕见,如文件系统断连)透传以便诊断.
                if e.errno != _errno.EBADF:
                    raise
        finally:
            self._held = False

    def __enter__(self) -> "FileLock":
        self.try_acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
