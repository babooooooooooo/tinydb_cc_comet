---
comet_change: concurrency-control
role: technical-design
canonical_spec: openspec
---

# 并发控制技术设计（Concurrency Control Design）

> 本设计文档是 `concurrency-control` change 的深度技术细化。OpenSpec 高层架构在 `openspec/changes/concurrency-control/design.md`；本文档给出实现细节、边界条件、测试策略与缓解方案。

## Context（背景）

`tinydb` 是单进程嵌入式关系数据库。`Pager` 持有可变共享状态（`_file`、`_mmap`、`_mem_pages`、`_next_page_id`、`_free_list_head`、`_wal`），没有任何同步原语。随着聚合、JOIN、CLI 等能力上线，应用开始把 tinydb 当作真实并发工作负载的后端，缺并发安全已成上线路径上的最大障碍。

OpenSpec change `concurrency-control` 新增：

- `Database` 实例上的 `threading.RLock`（粗粒度、可重入）
- 底层 DB 文件上的 OS 层 `fcntl.flock(LOCK_EX)`（跨进程排他锁）
- `DatabaseLocked` 异常以暴露锁获取失败
- opt-out 参数 `Database(path, locking=False)` 用于单线程或外部已做并发控制的场景

本文档对 OpenSpec `design.md` 做实现层细化，覆盖边界条件、锁协议、测试矩阵与风险缓解。

## Goals / Non-Goals（目标 / 非目标）

**Goals（目标）：**
- 多线程安全：通过 per-instance `threading.RLock` 串行化同一实例上的 `Database.execute()` 与 `explain_plan()`
- 跨进程安全：DB 文件独占 OS 锁；第二个 opener 在 100 ms 内抛 `DatabaseLocked`
- `:memory:` 模式：仅线程锁（RLock），不调文件锁
- 默认开启：`Database(path, locking=True)`
- opt-out：`Database(path, locking=False)` 跳过两层锁
- close 后禁止使用：`_is_closed` 标志 → `RuntimeError`
- 新增锁分支 ≥ 80% 覆盖率
- 整体覆盖率保持 ≥ 92%

**Non-Goals（非目标）：**
- Windows 平台（`fcntl` 不可用；仅 Linux/WSL）
- MVCC / Snapshot isolation / 读写分离
- `Database.begin()` / `commit()` / `rollback()` API 暴露
- 跨网络 / 分布式并发
- 在线备份协调
- Schema 迁移（v3 header 不变）

## Architecture（架构总览）

### 新增模块清单

```
src/tinydb/_filelock.py            [NEW]   ~60 行：FileLock 辅助
src/tinydb/errors.py               +10     DatabaseLocked 异常类
src/tinydb/pager.py                +20     try_acquire/release（__init__/close）
src/tinydb/database.py             +25     _lock、_is_closed、kwarg、execute/close 包装
src/tinydb/__init__.py             +1      导出 DatabaseLocked
src/tinydb/recovery.py             [不变]  _REPLAY_IN_PROGRESS guard 保留为已知 deviation
```

### 测试新增

```
tests/conftest.py                          +file_db / file_db_unlocked fixture
tests/unit/concurrency/                    [NEW] 5 个文件
tests/integration/concurrency/             [NEW] _driver.py + 4 个文件
tests/integration/test_recovery_lock.py    [NEW] 1 个文件
```

## Module Spec: `_filelock.py`

```python
"""fcntl.flock 的薄包装，提供 context manager API。

由 Pager 调用以获取 DB 文件上的独占 OS 锁。EWOULDBLOCK 时抛
DatabaseLocked。当平台不支持 fcntl 时（Windows），所有公开方法为 no-op。
"""
from __future__ import annotations

import fcntl
import os

from tinydb.errors import DatabaseLocked


class FileLock:
    """per-fd 文件锁，提供 try_acquire / release 语义。"""

    def __init__(self, fd: int, path: str) -> None:
        self._fd = fd
        self._path = path
        self._held = False

    def try_acquire(self) -> None:
        """获取 LOCK_EX | LOCK_NB；竞争时抛 DatabaseLocked。"""
        if self._held:
            return  # 幂等
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._held = True
        except (BlockingIOError, OSError) as e:
            # EWOULDBLOCK / EAGAIN 竞争；EINVAL 在 WSL1 或不支持 flock 的
            # 文件系统上触发。两种都向调用方抛 DatabaseLocked；path 属性
            # 帮助定位被争用的 DB。
            if e.errno in (os.errno.EWOULDBLOCK, os.errno.EAGAIN, os.errno.EINVAL):
                raise DatabaseLocked(self._path) from e
            raise

    def release(self) -> None:
        """释放锁（LOCK_UN）。幂等；close 后调用安全。"""
        if not self._held:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            self._held = False

    def __enter__(self) -> "FileLock":
        self.try_acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
```

**模块级降级**（Windows / fcntl 导入失败）：

```python
try:
    import fcntl  # noqa: F401
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False
```

`Database` 在用 `locking=True` 且 `path != ":memory:"` 构造 `Pager` 时检查 `_HAS_FCNTL`。若为 False，抛 `ImportError("tinydb concurrency control requires fcntl (Linux/WSL only)")` — 在 `__init__` 失败明确，避免静默降级。

## Module Spec: `Pager` 锁集成

### 新增属性

```python
class Pager:
    def __init__(self, path: str, locking: bool = True) -> None:
        # ... 现有初始化 ...
        self._is_locking_enabled = locking and not self._is_memory and _HAS_FCNTL
        self._file_lock: "FileLock | None" = None
        # ... 现有 _open_file / _init_page0 ...

        # 新增：在 _open_file() 成功之后获取跨进程锁
        if self._is_locking_enabled and self._file is not None:
            self._file_lock = FileLock(self._file.fileno(), self._path)
            try:
                self._file_lock.try_acquire()
            except DatabaseLocked:
                # 关闭已打开的 fd，保证失败的 Pager 不残留资源
                self._file.close()
                self._file = None
                raise  # 向上传播 DatabaseLocked
        # ... 现有 _init_wal()（可能触发 Recovery.replay）...
```

### 修改 `close()`

```python
def close(self) -> None:
    if self._file_lock is not None:
        self._file_lock.release()
        self._file_lock = None
    # ... 现有 close 逻辑（mmap 关闭、file 关闭）...
```

顺序注意：`release()` 在 `self._file.close()` 之前调用 — 但两者顺序无关（Linux 上对已关闭 fd 调 fcntl LOCK_UN 静默成功；close() 也会释放所有锁）。

## Module Spec: `Database` 锁集成

### 新 `__init__`

```python
import threading
from contextlib import nullcontext

class Database:
    def __init__(self, path: Union[str, Path] = ":memory:", *, locking: bool = True) -> None:
        self._is_closed: bool = False
        self._lock: "threading.RLock | None" = threading.RLock() if locking else None

        # Pager 构造可能抛 DatabaseLocked（另一进程持有锁）。
        # 这里故意不在 self._lock 内调 Pager —— RLock 是可重入的，
        # 我们希望 DatabaseLocked 在任何线程状态被污染之前干净上抛。
        self.pager = Pager(str(path), locking=locking)

        # ... 现有 catalog / index 初始化 ...
        # 后续代码（rebuild_for_table、install wrappers 等）隐式在
        # RLock 内运行（通过 execute 路径访问时）—— __init__ 自身不重新
        # 获取锁，因为单线程构造路径按定义就是安全的。
```

### 修改 `execute()`

```python
def execute(self, sql: str) -> list[Row]:
    ctx = self._lock if self._lock is not None else nullcontext()
    with ctx:
        if self._is_closed:
            raise RuntimeError("Database is closed")
        # ... 现有 tokenize / parse / run 逻辑 ...
```

### 修改 `explain_plan()`

与 `execute()` 同样模式。

### 修改 `close()`

```python
def close(self) -> None:
    ctx = self._lock if self._lock is not None else nullcontext()
    with ctx:
        if self._is_closed:
            return  # 幂等
        self._is_closed = True  # 在关闭 Pager 之前置位，
                                # 这样 reentrant 调用能感知到 closed
        if self.pager is not None:
            self.pager.close()  # 释放 FileLock + 关闭 fd
```

### 为什么 `nullcontext()` 处理 `locking=False`

`contextlib.nullcontext()` 返回 no-op context manager — 零开销。避免在热路径里出现 `if locking:` 分支。

## Module Spec: `errors.py` 新增

```python
class DatabaseLocked(TinydbError):
    """DB 文件被另一进程持有时抛出的异常。

    通过 fcntl.flock 做跨进程独占锁。`path` 属性标识被争用的 DB 文件。
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"database {path!r} is locked by another process")
```

## Recovery 与锁的交互

`Pager.__init__` 在 `_open_file()` 之后调 `_init_wal()`（既有顺序）。新锁协议下的流程：

1. `Database.__init__` → `Pager.__init__`
2. `Pager._open_file()` 打开 fd → 返回
3. `Pager.__init__` 调 `_FileLock.try_acquire(LOCK_EX | LOCK_NB)` → 持有 flock
4. `Pager.__init__` 调 `_init_wal()`
5. `_init_wal()` → 若 WAL 非空 → `Recovery.replay(wal)` → `_apply_committed(main_path, ...)`
6. `_apply_committed` 构造 `Pager(main_path)` → 它的 `_open_file()` 打开同文件新 fd → 它的 `try_acquire()` 在新 fd 上调 `fcntl.flock(LOCK_EX)`
7. **Linux fcntl 锁是 per-open-file-description（同一进程在新 fd 上的 `flock(LOCK_EX | LOCK_NB)` 会 EWOULDBLOCK，errno 11）**——与早先设计文档假设的「per-process 语义」相反（`flock(2)` man page 已明确为 per-ofd）。
8. **修正**：内层 `Pager` 在 `recovery._apply_committed` 中以 `locking=False` 构造，绕过自身的 flock 获取；外层 `Pager` 仍持有 flock 贯穿整个 replay，跨进程隔离语义由外层单点保证。
9. replay 结束后，内层 `Pager` 被关闭 → 外层 `Pager` 的锁仍持有。

**边界情况**：本实现依赖「外层 Pager 的 flock 覆盖整个 replay」语义。在未来若需要支持 recovery 时显式放弃锁（例如在线备份、跨节点迁移），应新增 `Database.__init__(recovery_locking=False)` 选项（本次 change 不包含）。

`recovery.py` 中既有的 `_REPLAY_IN_PROGRESS` 模块级 guard **保留**为已知 deviation（在 `proposal.md` Impact 与本设计风险章节均有记录）。本 change 不重构它。

### `_REPLAY_IN_PROGRESS` 已知偏差（Recorded deviation — Task 8 §4.4）

`_REPLAY_IN_PROGRESS` 是 `src/tinydb/recovery.py` 顶层的模块级布尔标志，作为 `Recovery.replay` 的可重入哨兵：

- **触发循环**：`Pager.__init__` → `_init_wal` → `Recovery.replay` → `_apply_committed` → `Pager(main_path, locking=False)` → `Pager.__init__` → `_init_wal` → `Recovery.replay` … 永无止境。
- **当前 workaround**：`recovery.replay` 入口检查全局标志，命中则直接 `return`；在 `try/finally` 中翻转以保证退出时复位。该方案依赖 Python 的全局状态在单进程内可观测；线程安全由 flag 在 `try/finally` 中串行设置保证，但跨线程重入仍会提前返回——目前可接受，因为 `Recovery.replay` 只在 `Pager.__init__` 中同步调用。
- **根因**：`_apply_committed` 构造 `Pager` 是为了复用其 `write_main_page` / `fsync_main`，但 `Pager.__init__` 默认会重新走 `_open_file` + `_init_wal`，从而再次进入 recovery。
- **未来清理（follow-up，不在本 change 范围）**：显式传入 `Recovery.replay(pager=...)` 参数，让内层 Pager 跳过 `_init_wal`；或者把 `_apply_committed` 改写为直接调底层 `os.pwrite` + `os.fsync`，绕开 Pager 构造。这样 `_REPLAY_IN_PROGRESS` 即可删除。
- **不在本 change 修复的原因**：本次 change 聚焦 `Database` / `Pager` 加锁协议，recovery 路径是协作方（§4.1-§4.3 验证其与锁的交互正常）。重构 recovery 会扩大 scope、引入额外风险，与 `verify_mode=thorough` 期望的"小步可审计"原则冲突。

## 公共 API 契约

```python
# 默认锁定
db = Database("/path/to.db")
db.execute("INSERT ...")  # 串行化

# 显式 opt-out
db = Database("/path/to.db", locking=False)
db.execute("INSERT ...")  # 不加锁；并发下可能 race

# 内存模式：仅线程锁，无文件锁
db = Database(":memory:")
db.execute("INSERT ...")

# 跨进程争用
# 进程 A: db = Database("/x.db")  → 持有 flock
# 进程 B: db = Database("/x.db")  → 抛 DatabaseLocked("/x.db")

# close 后调用
db.close()
db.execute("...")  # RuntimeError("Database is closed")
```

## Test Plan（测试策略）

### 单元测试（`tests/unit/concurrency/`）

| 文件 | 用例 |
|---|---|
| `test_threading_inserts.py` | 8 线程 × 100 INSERT 到同表 → 800 行，所有 ID 唯一，所有值匹配 |
| `test_threading_updates.py` | 4 线程 × 200 UPDATE 在不重叠行子集上 → 最终状态匹配预期更新 |
| `test_threading_memory.py` | 8 线程 × 100 INSERT 到 `:memory:` Database → 800 行；断言 `fcntl.flock` 未调用（monkey-patch） |
| `test_locking_off.py` | `locking=False` 路径；monkey-patch `fcntl.flock` 记录调用，断言零次；断言 `threading.RLock` 未构造 |
| `test_reentrant_lock.py` | `Database._exec_helper()` 在 `self.execute(...)` 内调 `self.execute(...)`；断言不死锁；断言最终行数精确 |

### 集成测试（`tests/integration/concurrency/`）

Subprocess 驱动模式（`_driver.py`）：

```python
def run_in_subprocess(workspace, fn_name, *args, **kwargs) -> dict:
    """在子进程中跑 fn_name(db, *args, **kwargs)，返回 JSON。"""
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, "src")
        from tinydb import Database
        from tests.integration.concurrency import _scenarios
        db = Database({str(workspace)!r} + "/test.db", locking=True)
        try:
            fn = getattr(_scenarios, {fn_name!r})
            result = fn(db, *{args!r}, **{kwargs!r})
            print("RESULT:" + json.dumps({{"ok": True, "result": result}}))
        except Exception as e:
            print("RESULT:" + json.dumps({{"ok": False, "type": type(e).__name__, "msg": str(e)}}))
        finally:
            db.close()
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    # ... 解析 stdout 最后一行 "RESULT:..."
```

场景函数（`_scenarios.py`）：

- `insert_n(db, n: int)` — INSERT n 行
- `count_users(db) -> int` — SELECT COUNT(*)
- `assert_locked(path) -> str` — 打开 Database(path)，若抛 `DatabaseLocked` 则返回 "locked"
- `open_and_close(db_path)` — 打开并关闭

测试文件：

| 文件 | 用例 |
|---|---|
| `test_multiprocess_writers.py` | 4 子进程各插入 250 行 → 父进程打开 DB 断言总数 == 1000，ID 唯一 |
| `test_multiprocess_reader_writer.py` | 1 writer（循环 INSERT 2s）+ 1 reader（循环 COUNT 2s）；reader 计数单调非减；无异常 |
| `test_multiprocess_locked_open.py` | 进程 A 持有 DB；进程 B 通过 `assert_locked` → 100ms 内返回 "locked" |
| `test_lock_release_on_close.py` | 进程 A 打开+关闭；进程 B 立即打开 → 成功 |

### Recovery 测试（`tests/integration/test_recovery_lock.py`）

- 进程 A：写 WAL 但不 commit → 退出（无 fsync_main）
- 进程 B：打开 DB → Recovery.replay() 在 flock 持锁下运行 → 完成 → B 看到干净状态（无半写数据、无撕裂行）
- 断言：replay 路径获取 flock（通过 monkey-patch 计数器验证）；replay 后状态匹配 A 写入前的状态

### close 后使用测试（`tests/unit/test_closed_database.py`）

- `db.close()` 后 `db.execute(...)` → `RuntimeError("Database is closed")`
- 幂等 close：`db.close(); db.close()` → 不抛异常

### pytest fixtures（`tests/conftest.py`）

```python
@pytest.fixture
def file_db(tmp_path):
    db = Database(tmp_path / "test.db", locking=True)
    yield db
    if not db._is_closed: db.close()

@pytest.fixture
def file_db_unlocked(tmp_path):
    db = Database(tmp_path / "test.db", locking=False)
    yield db
    if not db._is_closed: db.close()

@pytest.fixture
def memory_db_locked():
    db = Database(":memory:", locking=True)
    yield db
    if not db._is_closed: db.close()
```

### 覆盖率门槛

- 整体：≥ 92%（保持现有基线）
- `_filelock.py`：≥ 95%（小模块，必须近全覆盖）
- `database.py` 锁相关行（`__init__` lock arg、`execute`/`explain_plan`/`close` with-self._lock、`_is_closed` 检查）：≥ 90%
- `pager.py` 锁相关行（`_is_locking_enabled`、`try_acquire`/`release` 调用）：≥ 85%

### 稳定性检查

实现完成后：连续运行 `pytest` 5 次。并发套件中任何 flaky 失败都会阻断 change 通过 verify。

## Risks / Trade-offs（风险与缓解）

| 风险 | 缓解 |
|---|---|
| **R1**: Windows 缺 `fcntl` | 模块顶层 `try/except ImportError` → `_HAS_FCNTL=False`；`Database(path, locking=True)` 抛 `ImportError("tinydb concurrency control requires fcntl (Linux/WSL only)")` |
| **R2**: WSL1 / 特殊 FS 缺 `flock` | `_FileLock.try_acquire` 捕 `OSError(EINVAL)` → `DatabaseLocked`；文档标为 "best effort" |
| **R3**: Recovery 内层 `Pager` 重新 flock 可能失败 | Linux per-fd 语义可接受；特殊 FS 文档为 out-of-scope |
| **R4**: 现有测试增加 ~1μs RLock 开销 | 可接受；最坏情况基线运行时间增加 ~5% |
| **R5**: `_REPLAY_IN_PROGRESS` 模块 guard 是 workaround | 保留；记录为 deviation — Task 8 §4.4 详述触发循环、当前哨兵机制、follow-up 清理路径（显式 `Recovery.replay(pager=...)` 或直接 `os.pwrite`/`os.fsync` 绕开 Pager 构造）。本 change 不修复 |
| **R6**: 读并发被牺牲 | 文档明确；未来 MVCC 扩展点 |
| **R7**: subprocess 测试在 CI 中 flaky | `@pytest.mark.flaky(retries=2, condition=has_subprocess_hang)` |
| **R8**: close 后使用 | `_is_closed` 标志 + RuntimeError |
| **R9**: multiprocess 测试失败难调试 | 每个测试把完整 subprocess stdout/stderr 写入 `tmp_path/"<test>.log"` |
| **R10**: `_FileLock.release` 在 fd 关闭后调用 | Linux 上对已关闭 fd 调 fcntl LOCK_UN 安全；测试验证不抛 `OSError` |
| **R11**: `Database.__init__` 在 `DatabaseLocked` 时处于部分状态 | `Pager.__init__` catch 块中关闭 fd；`Database` 仅在 Pager 成功后设 `self._is_closed`、`self._lock`、`self.pager` |
| **R12**: `_is_closed=True` 与并发 `execute` 之间的竞争 | `_is_closed` 检查在 `with self._lock:` 内 — 对 `close()` 原子可见 |
| **R13**: 长操作持锁阻塞所有线程 | 可接受：嵌入式 DB fsync 通常 <10ms；OLTP 工作负载可容忍 |
| **R14**: subprocess 测试间污染 | 每个测试用独立 `tmp_path` workspace |
| **R15**: 多语句脚本整段持锁 | 可接受：匹配 WAL 原子性边界 |

## Open Questions（开放问题）

- **Q1**: `Pager` 是否自身在 `read_page()` / `write_page()` 上包一层 per-Pager RLock 做 defense-in-depth？**决策**：否 —— Database 层 RLock 已覆盖所有通过 `execute()` 的访问路径；直接 Pager 访问非公开 API。
- **Q2**: 是否暴露 `Database.is_locked` 属性用于诊断？**决策**：推迟到 follow-up；当前通过 `DatabaseLocked` 异常的 path 属性定位足够。
- **Q3**: 是否加 `Database.__exit__`（context manager 协议）？**决策**：推迟 — 正交特性，与并发无关。
- **Q4**: 线程持锁时崩溃（如未捕获异常）会怎样？**决策**：`RLock` 不检测 — 存在死锁隐患。由更高层的 `execute()` try/except 缓解；文档标注为已知限制。

## Migration Plan（迁移计划）

无 schema 迁移。`Database(path)` 默认行为从 "不安全" 变 "安全" — 严格新增能力。

回滚策略：随发行版附带文档，明确推荐用户在特殊平台出现回归时使用 `Database(path, locking=False)`。

## Verification Strategy（验证策略）

- `pytest` 全套通过（≥ 92% 覆盖率）
- 并发测试套件连续 5 次通过
- 手动冒烟：从两个终端打开同一 DB 文件 — 第二个终端显示 `DatabaseLocked`
- 手动冒烟：真实磁盘 DB 上跑 8 线程 INSERT 测试 — 观察无丢失写、无重复 ID