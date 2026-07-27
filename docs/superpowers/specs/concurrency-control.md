# concurrency-control 公开契约

> **状态**: v0.2 新增能力（pending release）
> **来源**: 合并自 `openspec/changes/concurrency-control/proposal.md` 与
> `openspec/changes/concurrency-control/design.md` 的高阶汇总；增量场景细节见
> `openspec/changes/concurrency-control/specs/concurrency-control/spec.md`。

## 范围

`Database` 默认启用两层并发保护：

- **进程内**：`threading.RLock`（粗粒度、可重入），串行化 `execute()` 与 `explain_plan()`。
- **跨进程**：`fcntl.flock(LOCK_EX)`，对 DB 文件本身（不是 sidecar）加独占 OS 锁；进程退出或崩溃由 OS 自动释放。

`:memory:` 模式仅持线程锁，不调文件锁。

## 公开 API

### `Database.__init__`

```python
Database(
    path: str | Path = ":memory:",
    *,
    locking: bool = True,
) -> Database
```

| 行为 | `locking=True`（默认） | `locking=False` |
|------|------------------------|-----------------|
| 构造 per-instance `threading.RLock` | 是 | 否 |
| 调用 `fcntl.flock(LOCK_EX)` | 是（path 非 `:memory:`） | 否 |
| 跨进程争用时上抛 `DatabaseLocked(path)` | 是 | 不抛（争用会损坏文件） |
| `:memory:` 模式 | 仅 RLock | 无锁 |
| `execute()` / `explain_plan()` 边界持 RLock | 是 | 否 |

### `Pager.__init__`

```python
Pager(
    path: str | Path = ":memory:",
    *,
    locking: bool = True,
) -> Pager
```

Pager 是 storage 层的实现细节，但为支持应用直接复用（如自定义 driver）暴露同一 `locking` 参数。`Pager` 本身不持 `threading.RLock` —— 线程安全由 `Database` 层承担；`Pager` 仅负责跨进程文件锁。`Pager.close()` 释放文件锁（同时关闭 fd，OS 自动清理）。

### `DatabaseLocked` 异常

```python
class DatabaseLocked(TinydbError):
    path: str  # 被争用的 DB 文件路径
```

`DatabaseLocked` 是公开异常，导出在 `tinydb` 顶层命名空间与 `tinydb.errors` 模块下。message 模板：

```
database '<path>' is locked by another process
```

抛出场景：

- 进程 A 持有 DB 文件的 `LOCK_EX`，进程 B `Database.__init__` 打开同一文件。
- `fcntl.flock` 返回 `EWOULDBLOCK` / `EAGAIN` / `EINVAL`（EINVAL 在 WSL1 或不支持 flock 的文件系统上触发）。

非抛出场景：

- `:memory:` 模式（无文件 → 无争用）。
- `locking=False`（无锁 → 无争用）。

## 实现细节（非公开）

### `tinydb._filelock.FileLock`

`_filelock.FileLock` 是 `fcntl.flock` 的薄包装，提供 `try_acquire` / `release` 与
context-manager 协议。**不视为公开 API**：

- 模块名带下划线前缀（私有约定）。
- 不导出在 `tinydb` 顶层命名空间。
- 行为可能在 minor 版本中调整而无 deprecation 周期。

应用代码不应 `import tinydb._filelock`，需要并发控制应使用 `Database(path, locking=...)` 公开接口。

## 失败模式

| 场景 | 行为 |
|------|------|
| `db.close()` 后 `db.execute(...)` | 抛 `RuntimeError("Database is closed")` |
| `db.close()` 多次调用 | 幂等（重复调用安全） |
| 跨进程争用 | `DatabaseLocked(path)` 在 100ms 内上抛（`LOCK_NB` 非阻塞） |
| Recovery 期间另一进程 open | 后者抛 `DatabaseLocked`，直到 replay 完毕 |
| 平台无 `fcntl` 且 `locking=True` | `Pager.__init__` 抛 `ImportError("tinydb concurrency control requires fcntl (Linux/WSL only)")` |

## 不支持范围（Out of Scope）

- **Windows**：`fcntl` 不可用；`locking=True` 抛 `ImportError`，需要 `locking=False`。
- **MVCC / Snapshot Isolation**：未实现；所有读也走 EX 锁，读并发被牺牲。
- **`Database.begin()` / `commit()` / `rollback()` 公开事务 API**：内部使用 `tinydb.recovery` 机制但不暴露；用户代码仅依赖 `execute()` 的 autocommit 语义。
- **跨网络 / 分布式并发**：flock 仅在本机文件系统上语义可靠；NFS / SMB / 跨主机需自备协调层。
- **Reader-writer split**：不分 `LOCK_SH` / `LOCK_EX`；统一 `LOCK_EX`。

## 已知偏差（Deviations）

- **`_REPLAY_IN_PROGRESS` 模块级 guard**（`tinydb/recovery.py`）是 workaround：触发循环是
  `Pager.__init__` → `_init_wal` → `Recovery.replay` → `_apply_committed` →
  `Pager(main_path, locking=False)` → `_init_wal` → `Recovery.replay` …。当前哨兵机制依赖
  Python 进程内全局状态。Future cleanup：显式 `Recovery.replay(pager=...)` 参数让内层
  Pager 跳过 `_init_wal`，或将 `_apply_committed` 改为直接 `os.pwrite` + `os.fsync`
  绕开 Pager 构造。本 change 不修复（与 `Database`/`Pager` 加锁协议正交）。
- **Recovery 内层 Pager 重新 flock**：在 WSL1 / 特殊文件系统可能因 per-fd 语义与 Linux 标准
  实现不一致而失败；当前未做平台特判。
- **macOS `flock` 语义**：POSIX 严格语义不保证；macOS 上 flock 实际退化为 `lockf` 行为，
  跨进程互斥未必生效。建议 macOS 上显式 `locking=False` 由应用层协调。

## 迁移指引

- 现有调用方代码（`tinydb.Database(path)`）**无需改动**；默认行为从「单线程不安全」升级为
  「双层并发安全」，对调用方是新增能力（无损升级）。
- 已有外部并发控制的应用可在初始化时显式 `Database(path, locking=False)` 关闭内建锁，
  保持原行为（零开销）。
- Windows / macOS 应用必须显式 `locking=False`，否则 `Database.__init__` 抛 `ImportError`。
- 锁状态不写入文件头（v3 schema 不变）；已存在的 `.db` 文件无需迁移。