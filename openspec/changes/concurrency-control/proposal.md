## Why

tinydb 当前是单线程、单进程嵌入式数据库。`Pager` 持有 `_file`、`_mmap`、`_mem_pages`、`_next_page_id`、`_free_list_head`、`_wal` 等共享可变状态，在并发场景下会出现 race（双分配 ID、丢失 free-page、撕裂读写、WAL 状态机错乱）。随着聚合、JOIN、CLI 等能力上线，应用开始把 tinydb 当作真实工作负载的后端；缺并发安全已成上线路径上的最大障碍。

## What Changes

- **`Database.__init__` 新增可选参数 `locking: bool = True`**，默认开启并发安全；显式 `locking=False` 走单线程零开销路径（仅供测试/嵌入式受限环境）。
- **`Database.execute()` 包粗粒度锁**：从 tokenize 到 executor 返回全过程持有 per-instance `threading.RLock`（可重入）。
- **`Pager` 新增跨进程文件锁**：对磁盘数据库（非 `:memory:`）的底层文件 fd 执行 `fcntl.flock(LOCK_EX)`；`:memory:` 跳过文件锁。
- **锁主 DB 文件本身**（非 sidecar `<db>.lock` 文件）；Pager 持有的 fd 即锁句柄；进程退出/崩溃由 OS 自动释放。
- **WAL 写入协议保持**：`wal_append_page` → `write_main_page` → `fsync_main` → `wal_append_commit` → `wal_truncate_before` 全程在文件锁内；现有 acid 协议无变化。
- **`Recovery.replay()` 持锁执行**：replay 路径中构造 `Pager` 时自然获取文件锁，崩溃后由新进程独占重放。
- **新增 `tests/unit/concurrency/`、`tests/integration/concurrency/`** 模块，覆盖多线程 INSERT、跨进程 SELECT+INSERT、`:memory:` 线程安全、`locking=False` opt-out、Recovery + 锁的交互。
- **覆盖率目标 ≥ 80%**（现有 92%+ 基准不退化）。

**BREAKING**: 无。`Database(path)` 默认行为从"不安全"变"安全"对调用方是新增能力。

## Capabilities

### New Capabilities

- `concurrency-control`: 多线程 + 多进程并发安全。覆盖 `Database` API 加锁参数、`Pager` 跨进程文件锁、`Recovery.replay` 锁协议、`:memory:` 仅线程锁、opt-out 路径、并发测试覆盖。

### Modified Capabilities

- 无。现存 spec（storage-engine / python-api / repl-shell / sql-minimal-parser / type-system-basic）均未声明并发行为，本 change 仅在实现层加锁，spec 层级需求不变。

## Impact

**Affected code:**
- `src/tinydb/database.py` — `__init__` 接受 `locking`；`execute`/`explain_plan`/`close` 路径加 RLock；持有 `self._lock: threading.RLock | None`。
- `src/tinydb/pager.py` — `__init__` 末尾 `fcntl.flock(self._file, LOCK_EX)`（仅非 `:memory:`）；`close()` 释放锁。新增 `_is_locking_enabled` 标志以支持 opt-out 路径。
- `src/tinydb/recovery.py` — 无直接改动；`Pager.__init__` 内部的 `Recovery.replay()` 调用自动纳入锁协议。`_REPLAY_IN_PROGRESS` 全局 guard 保留作为已知 workaround（deviation 记录）—— Task 8 §4.4 文档化：触发循环是 `Pager.__init__` → `_init_wal` → `Recovery.replay` → `_apply_committed` → `Pager(main_path, locking=False)` → `_init_wal` → `Recovery.replay` …；当前哨兵机制依赖 Python 进程内全局状态。Future cleanup：显式 `Recovery.replay(pager=...)` 参数让内层 Pager 跳过 `_init_wal`，或将 `_apply_committed` 改为直接 `os.pwrite` + `os.fsync` 绕开 Pager 构造——任一变更均可删除 `_REPLAY_IN_PROGRESS`。本 change 不修复（与 `Database`/`Pager` 加锁协议正交；扩大 scope 引入额外风险）。

**Affected APIs:**
- `Database(path, locking=True)` — 新参数。
- `Database.close()` — 新增语义：释放所有持有的锁（线程 RLock 释放 + 文件 flock 释放）。

**Affected tests:**
- 新增 `tests/unit/concurrency/` 子树。
- 新增 `tests/integration/concurrency/` 子树（含 multiprocess 真实跨进程测试驱动）。
- 现有测试默认 `locking=False` 加速（绝大多数是非并发单元测试）。

**Affected docs:**
- `docs/superpowers/specs/concurrency-control.md` — 新建 spec。
- `README.md` — 加 "Concurrency" 章节。

**Out of scope:**
- Windows 平台（fcntl 不可用）；目标平台 Linux/WSL。
- MVCC / 乐观并发 / Snapshot isolation。
- `Database.begin()` / `commit()` / `rollback()` 多语句事务 API（仅内部锁，不暴露）。
- 跨网络 / 分布式并发。
- 在线备份协调。
- Schema 迁移（v3 头部保持不变）。