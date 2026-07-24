# Brainstorm Summary

- Change: concurrency-control
- Date: 2026-07-24

## 确认的技术方案

**架构**：
- 新增 `src/tinydb/_filelock.py` 模块：纯 fcntl wrapper（`try_acquire`/`release`/context manager），可在测试中 monkey-patch
- 修改 `errors.py`：新增 `DatabaseLocked(TinydbError)` 异常
- 修改 `pager.py`：`__init__` 末尾通过 `_FileLock.try_acquire(LOCK_EX | LOCK_NB)` 获取锁；`close()` 释放
- 修改 `database.py`：新增 `locking: bool = True` kwonly 参数；持有 `self._lock = RLock() | None`；`execute`/`explain_plan`/`close` 路径加锁；`_is_closed` flag 防 use-after-close
- `recovery.py` 不变（`_REPLAY_IN_PROGRESS` 模块 guard 保留为已知 deviation）

**锁协议顺序**（Database `__init__`）：
1. `self._is_closed = False`
2. `self._lock = RLock() if locking else None`
3. `self.pager = Pager(path, locking=locking)` — 内含 `fcntl.flock(LOCK_EX | LOCK_NB)`
4. 后续 `Catalog.from_bytes`、`IndexManager` 初始化、`rebuild_for_table` 等全部隐式在 `_lock` 内

**API 变更**：
- `Database.__init__(path, *, locking=True)` — 新 kwonly 参数
- `DatabaseLocked` 异常带 `path` 属性 + 友好消息
- `db.close()` 后 `db.execute(...)` 抛 `RuntimeError("Database is closed")`

**关键决策**（来自 brainstorm Q&A）：
- Q1: 用 `_FileLock` helper 模块（不 inline fcntl）
- Q2: 跨进程测试用 `subprocess.Popen` driver（`_driver.py` 子模块）
- Q3: Database._lock 在 Pager 之前构造
- Q4: 加 `_is_closed` 标志 + RuntimeError

## 关键取舍与风险

| 风险 | 缓解 |
|---|---|
| Windows 不支持 fcntl | `try/except ImportError` → 抛 `ImportError("fcntl not available")` |
| WSL1 flock 不可用 | `_filelock.try_acquire` 捕 `OSError(EINVAL)` 降级 |
| `_REPLAY_IN_PROGRESS` workaround | 保留（deviation 记录），本次不重构 |
| 多线程读并发被牺牲 | 文档明确；未来 MVCC |
| 现有测试 fixture 改动可能 flaky | 5 次连续运行全套验证 |
| multiprocess test 偶发死锁 | `@pytest.mark.flaky(retries=2)` |
| `_filelock.release` 多次调用 | fcntl LOCK_UN 幂等 |

## 测试策略

- **单元测试** `tests/unit/concurrency/`：8 线程 × 100 INSERT、4 线程 × 200 UPDATE、`:memory:` 8 线程、`locking=False` 不调 fcntl、reentrant 不死锁
- **集成测试** `tests/integration/concurrency/`：subprocess driver + 4 进程 × 250 INSERT、1W + 1R 跑 2s、A 持有 B 100ms 内抛 `DatabaseLocked`、A close → B 立即获取、Recovery + 锁交互
- **覆盖率目标**：整体 ≥ 92%；`_filelock.py` ≥ 95%；`database.py` 锁分支 ≥ 90%；`pager.py` 锁分支 ≥ 85%
- **pytest fixtures**（`tests/conftest.py`）：`file_db`（locking=True）/ `file_db_unlocked`（locking=False）

## Spec Patch

无。`specs/concurrency-control/spec.md` 已包含完整 ADDED Requirements，不需要回写 delta spec。