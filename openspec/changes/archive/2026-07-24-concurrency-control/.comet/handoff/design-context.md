# Comet Design Handoff

- Change: concurrency-control
- Phase: design
- Mode: compact
- Context hash: e67d6b6173d5a6f269bf0a8c8d1a2132b26bf15a8d0cbf85e7ddafb9c7a7e720

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/concurrency-control/proposal.md

- Source: openspec/changes/concurrency-control/proposal.md
- Lines: 1-53
- SHA256: 3a7ff430dd13ebf8a1ec875bb47e75b4ac1ab236b1500d5975e207c74bb4eebc

```md
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
- `src/tinydb/recovery.py` — 无直接改动；`Pager.__init__` 内部的 `Recovery.replay()` 调用自动纳入锁协议。`_REPLAY_IN_PROGRESS` 全局 guard 保留作为已知 workaround（deviation 记录）。

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
```

## openspec/changes/concurrency-control/design.md

- Source: openspec/changes/concurrency-control/design.md
- Lines: 1-117
- SHA256: 45bd3982d54844c749495d937b509de7a257a15d8517231d26bb0cb8fffcf736

[TRUNCATED]

```md
## Context

tinydb 是单进程嵌入式关系数据库。当前 `Pager` 持有 `_file`、`_mmap`、`_mem_pages`、`_next_page_id`、`_free_list_head`、`_wal` 等可变共享状态，无任何同步原语。`Database.execute()` 通过 `executor.execute(s)` 直接读写页面，绕过 `Transaction` 类（仅在 acid 路径显式调用）。`Recovery.replay()` 通过模块级 `_REPLAY_IN_PROGRESS` guard 避免重入。

聚合、JOIN、CLI 等能力使 tinydb 走向真实并发工作负载。需要：
1. 同进程多线程安全（避免 Pager 共享状态 race）
2. 跨进程文件锁（避免多进程同时写同一 .db 文件）
3. WAL 协议不变（已有 acid 保证）
4. 默认安全、`locking=False` opt-out

## Goals / Non-Goals

**Goals:**
- 同进程多线程：所有 `Database.execute()` 路径串行化，可重入（helper 互调不死锁）
- 跨进程：磁盘 DB 文件独占锁，第二个 opener 立即失败（不阻塞）
- `:memory:` 模式：仅线程锁，无文件锁
- 默认开启；显式 `locking=False` 可关闭
- 测试覆盖：threading 单元测试 + multiprocess 集成测试

**Non-Goals:**
- Windows 平台（fcntl 不可用；目标 Linux/WSL）
- MVCC / Snapshot isolation
- `Database.begin()/commit()/rollback()` 事务 API 暴露
- 跨网络并发
- 在线备份协调
- Schema 迁移（v3 头部保持不变；锁状态不写入文件头）

## Decisions

### D1: Database + Pager 双层锁职责分离

- **`Database` 持 `threading.RLock`** — 序列化同进程多线程访问 `execute()` 边界；可重入。
- **`Pager` 持 `fcntl.flock(LOCK_EX)`** — 序列化跨进程访问磁盘文件；OS 自动释放。
- **不引入 `LockManager` 模块** — 两层职责清晰，无需抽象。

### D2: Coarse-grained lock at `Database.execute()`

- 锁住整个 tokenize → parse → execute → return 路径。
- **不用 fine-grained Pager.write_page() 锁** — 减少 deadlock 面；当前 single-writer 模型足够。
- 读也走 execute 边界 → 简单一致；放弃读并发能力（见 D3）。

### D3: Global lock（无 reader-writer split）

- 不分 LOCK_SH / LOCK_EX；统一 LOCK_EX。
- **优点**：实现简单、无 deadlock。
- **缺点**：读阻塞写、写阻塞读。对嵌入式 OLTP 工作负载足够。
- **未来扩展点**：若读并发成为瓶颈，可在 `Pager.read_page()` 上加 `RLock`/`Condition` 实现 MVCC。

### D4: 默认开锁、`locking=False` opt-out

- `Database(path, locking=True)` — 默认。
- `Database(path, locking=False)` — 跳过所有锁。
- opt-out 路径：测试加速、嵌入式受限环境、用户在外部做并发控制。
- 当 `locking=False` 时，**所有现存的单线程测试**继续工作（无行为变化）。

### D5: 锁主 DB 文件本身（非 sidecar）

- `fcntl.flock(self._file, LOCK_EX)` 直接锁 Pager 持有的 DB 文件 fd。
- **不用 `<db>.lock` sidecar 文件** — 减少文件数；OS 在 fd close 时自动释放所有 flock 锁。
- 进程崩溃（无 cleanup）→ OS 回收 fd → flock 自动释放。

### D6: `:memory:` 跳过文件锁

- `Pager._is_memory == True` → `__init__` 跳过 `fcntl.flock`。
- 仅依赖 `Database` 层 RLock 保证多线程安全。
- 内存 DB 跨进程本就独立（私有内存），无跨进程场景。

### D7: Recovery.replay 在锁内完成

- `Pager.__init__` 末尾调用 `_init_wal()` → 若 WAL 非空 → `Recovery.replay()`。
- 自然流程：Pager 先 flock → 然后触发 replay → replay 构造的内部 Pager（`_apply_committed` 中 `Pager(main_path)`）会再次 flock。
- **第 2 次 flock 由同进程持有 → flock 累加引用计数 → 不会失败**。
- 第 3 进程尝试 open → flock 失败 → `DatabaseLocked`。
- `_REPLAY_IN_PROGRESS` 模块级 guard **保留**作为已知 workaround（deviation 记录）；本次 change 不重构。

### D8: `DatabaseLocked` 异常类型

- 在 `tinydb/errors.py` 新增 `DatabaseLocked(TinydbError)`，消息包含文件路径。
- 由 `Pager.__init__` 在 `fcntl.flock` 失败时抛出（关闭文件后）。
- `Database.__init__` 让异常向上传播。

```

Full source: openspec/changes/concurrency-control/design.md

## openspec/changes/concurrency-control/tasks.md

- Source: openspec/changes/concurrency-control/tasks.md
- Lines: 1-62
- SHA256: df469bbe0334c945a34ca238c1e813ccb98205b60f76e93f5561a186676dcb41

```md
## 1. 基础 — 锁原语与异常类型

- [ ] 1.1 在 `src/tinydb/errors.py` 中新增 `DatabaseLocked(TinydbError)` 异常类，带 `path` 属性，消息格式清晰可定位
- [ ] 1.2 在 `Pager` 上新增 `Optional[threading.RLock]` 字段（`_lock: threading.RLock | None = None`），用于单 Pager 实例内部的进程内互斥（防御性兜底）
- [ ] 1.3 验证 `fcntl` 在目标平台（Linux/WSL）可正常导入；添加 `try/except ImportError` 降级路径，并设置模块级 `_HAS_FCNTL` 标志

## 2. Database 层线程锁

- [ ] 2.1 在 `Database.__init__` 上添加 `locking: bool = True` 关键字参数；`locking=True` 时构造 `self._lock: threading.RLock | None = threading.RLock()`，`locking=False` 时设为 `None`
- [ ] 2.2 用 `with self._lock:`（非 None 时）包装 `Database.execute()` 函数体；保持可重入语义
- [ ] 2.3 用 `with self._lock:`（非 None 时）包装 `Database.explain_plan()` 函数体
- [ ] 2.4 更新 `Database.close()` 以释放锁状态（语义层面 — `RLock` 无强制释放；通过关闭 Pager 释放底层 fd，从而 OS 释放 flock）
- [ ] 2.5 在 `src/tinydb/__init__.py` 中导出 `DatabaseLocked`

## 3. Pager 层跨进程文件锁

- [ ] 3.1 在 `Pager.__init__` 中，于 `self._file` 打开后（非 `:memory:` 路径），调用 `fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)`；捕获 `BlockingIOError`（`EWOULDBLOCK`）并抛出 `DatabaseLocked(self._path)`
- [ ] 3.2 当 `self._is_memory` 为 True 时跳过 `fcntl.flock` 调用
- [ ] 3.3 当通过新的 `Pager(path, locking=True)` 关键字参数（从 `Database.__init__` 透传）传入 `locking=False` 时跳过 `fcntl.flock` 调用
- [ ] 3.4 验证 `Pager.close()` 关闭 `self._file`（已实现）— 无需新增代码，OS 在 fd 关闭时自动释放 flock
- [ ] 3.5 在 `tests/unit/test_pager_lock.py` 中新增单元测试：在临时文件上顺序两次打开 Pager，断言第一次关闭后第二次打开成功

## 4. Recovery 与锁的交互

- [ ] 4.1 验证 `Pager.__init__` 在 `_open_file()` 返回后才调用 `_init_wal()`（即 flock 已持有后再触发 replay）。若顺序相反则调换
- [ ] 4.2 验证 `recovery.py` 中的 `_apply_committed` 构造的 `Pager(main_path)` 能在同一 fd 上重新获取 flock（同进程 → flock 累加 → 成功）。新增单元测试断言不死锁
- [ ] 4.3 在 `tests/integration/test_recovery_lock.py` 中新增集成测试：进程 A 写 WAL 后不 commit 直接退出；进程 B 打开 DB → replay 执行 → B 看到干净状态（或已提交子集）
- [ ] 4.4 在 `design.md` R5 与 `proposal.md` Impact 中将既有的 `_REPLAY_IN_PROGRESS` 模块级 guard 记录为已知 deviation（本次 change 不修复）

## 5. 跨进程集成测试

- [ ] 5.1 创建 `tests/integration/concurrency/__init__.py` 与 `tests/integration/concurrency/_driver.py`，提供 subprocess 驱动辅助：打开 DB、执行 `execute()` 可调用对象、将结果以 JSON 写入 stdout 后退出
- [ ] 5.2 `test_multiprocess_writers.py`：派生 4 个子进程；每个插入 250 条不同行；父进程打开 DB 断言总行数 == 1000 且无重复 ID
- [ ] 5.3 `test_multiprocess_reader_writer.py`：派生 1 个 writer（循环 INSERT）和 1 个 reader（循环 SELECT），运行 2 秒；断言无异常抛出且 reader 的 row-counts 单调非减
- [ ] 5.4 `test_multiprocess_locked_open.py`：进程 A 持有 DB 打开；进程 B 打开并断言 100 ms 内抛出 `DatabaseLocked`
- [ ] 5.5 `test_lock_release_on_close.py`：进程 A 打开后关闭；进程 B 在 A 关闭后立即打开并断言成功

## 6. 多线程单元测试

- [ ] 6.1 `tests/unit/concurrency/test_threading_inserts.py`：8 线程 × 每线程 100 次 INSERT 到同一表 → 最终行数 == 800，所有 ID 唯一
- [ ] 6.2 `tests/unit/concurrency/test_threading_updates.py`：4 线程 × 每线程 200 次 UPDATE，作用在不重叠行子集上 → 最终状态匹配预期更新（无丢失写）
- [ ] 6.3 `tests/unit/concurrency/test_threading_memory.py`：与 6.1 相同但用 `Database(":memory:")` — 必须 NOT 调用 fcntl（通过 monkey-patch 或平台守卫断言）
- [ ] 6.4 `tests/unit/concurrency/test_locking_off.py`：在 `locking=False` 路径下，monkey-patch `fcntl.flock` 并断言它未被调用；断言 `threading.RLock` 也未被构造
- [ ] 6.5 `tests/unit/concurrency/test_reentrant_lock.py`：方法 `Database._exec_helper()` 在 `execute()` 内部调用另一个 `Database.execute()`，断言不死锁

## 7. 测试基础设施与覆盖率

- [ ] 7.1 更新 `tests/conftest.py`（或新建），让现有非并发测试默认使用 `Database(path, locking=False)`，避免 796 个基线测试产生 flock 开销
- [ ] 7.2 本地运行完整测试套件（`pytest`），确认通过且覆盖率 ≥ 92%，0 个新增失败
- [ ] 7.3 验证并发测试模块合计覆盖 `database.py`、`pager.py`、`errors.py` 新增锁相关分支 ≥ 80%
- [ ] 7.4 完整测试套件连续运行 5 次，检测因锁顺序引入的 flaky 测试

## 8. 文档

- [ ] 8.1 在 `README.md` 中新增 "Concurrency" 章节，说明：`Database(path, locking=True)` 默认行为、单线程 opt-out、仅 Linux flock、`:memory:` 行为
- [ ] 8.2 更新 `docs/superpowers/specs/concurrency-control.md`（在 `docs/superpowers/specs/` 下新建文件），汇总 `specs/concurrency-control/spec.md` 中的公开契约
- [ ] 8.3 若 `CHANGELOG.md` 存在，新增条目记录新增的 `locking` 参数与跨进程锁保证

## 9. 最终验证

- [ ] 9.1 运行 `comet-guard concurrency-control open --apply` 并确认 `ALL CHECKS PASSED`
- [ ] 9.2 确认 `.comet.yaml` 中 `phase` 已推进到 `design`
- [ ] 9.3 交接给 `/comet-design` 阶段（Comet 流程下一步）
```

## openspec/changes/concurrency-control/specs/concurrency-control/spec.md

- Source: openspec/changes/concurrency-control/specs/concurrency-control/spec.md
- Lines: 1-76
- SHA256: d26b403cdd4cf8f5ef6a3c045ff6cdf71de5345866c10c1c4ac9b1e05daade2d

```md
## ADDED Requirements

### Requirement: Database constructor accepts locking flag

The system SHALL accept an optional `locking: bool = True` keyword argument in `Database.__init__`. When `locking=True` (default), the system MUST acquire both an in-process reentrant lock (`threading.RLock`) on the `Database` instance and an exclusive OS-level file lock on the underlying DB file when the path is not `:memory:`. When `locking=False`, the system MUST NOT acquire any lock and MUST behave as a single-threaded database with zero locking overhead.

#### Scenario: Default constructor enables locking
- **WHEN** calling `Database("/path/to/file.db")` with no `locking` argument
- **THEN** the system MUST acquire `threading.RLock` on the instance
- **AND** MUST acquire `fcntl.flock(LOCK_EX)` on the open DB file fd
- **AND** MUST raise `DatabaseLocked` if another process holds the lock on the same file

#### Scenario: Explicit opt-out disables locking
- **WHEN** calling `Database("/path/to/file.db", locking=False)`
- **THEN** the system MUST NOT acquire `threading.RLock`
- **AND** MUST NOT acquire `fcntl.flock`
- **AND** MUST succeed even if another process holds the DB lock

#### Scenario: In-memory database skips file lock
- **WHEN** calling `Database(":memory:", locking=True)` (or with default)
- **THEN** the system MUST acquire `threading.RLock` on the instance
- **AND** MUST NOT call `fcntl.flock` (no file exists)

### Requirement: Coarse-grained thread serialization at execute boundary

The system SHALL serialize concurrent calls to `Database.execute()` and `Database.explain_plan()` on the same instance via the per-instance `threading.RLock`. The lock MUST be acquired before tokenization begins and released after the executor returns. Reentrant calls from within the locked region (e.g., a method that internally invokes another locked method) MUST be allowed.

#### Scenario: Two threads executing concurrent INSERTs are serialized
- **WHEN** two threads on the same `Database` instance invoke `execute("INSERT ...")` simultaneously
- **THEN** the two calls SHALL NOT overlap their critical sections
- **AND** the final committed row count SHALL equal the sum of both inserts (no lost writes)

#### Scenario: Reentrant call from within locked region does not deadlock
- **WHEN** a helper method invoked inside `execute()` calls another method on the same `Database` that also acquires the lock
- **THEN** the system MUST NOT deadlock (RLock is reentrant)

### Requirement: Cross-process exclusive lock via fcntl

For non-`:memory:` paths, the system SHALL acquire `fcntl.flock(LOCK_EX)` on the Pager's file descriptor during `Pager.__init__`. The lock MUST be released when `Pager.close()` is called or when the process exits (OS-level automatic release on fd close). A second process attempting to open the same DB file while the first holds the lock MUST raise `DatabaseLocked` within 100 ms.

#### Scenario: Second process open raises DatabaseLocked
- **WHEN** process A holds an exclusive lock on `/tmp/x.db` and process B opens `/tmp/x.db` with `Database("/tmp/x.db")`
- **THEN** process B's `Database.__init__` MUST raise `DatabaseLocked` indicating `/tmp/x.db` is locked by another process

#### Scenario: Closing the first process frees the lock for the second
- **WHEN** process A closes its `Database` (or crashes) and process B retries the open
- **THEN** process B MUST successfully acquire the lock and complete `Database.__init__`

#### Scenario: In-memory mode does not call fcntl
- **WHEN** `Database(":memory:")` is opened
- **THEN** `Pager.__init__` MUST NOT call `fcntl.flock` (skipping file lock entirely)

### Requirement: Recovery replay cooperates with file lock

When `Pager.__init__` triggers `Recovery.replay()` because a non-empty WAL exists, the replay MUST run while the file lock is held. A concurrent process attempting to open the same DB during replay MUST observe `DatabaseLocked` until replay completes.

#### Scenario: Replay blocks competing opener
- **WHEN** process A opens a DB with a non-empty WAL and process B opens the same DB before A's `Pager.__init__` returns
- **THEN** process B MUST observe `DatabaseLocked` (the flock is held across the replay call)

### Requirement: Lock acquisition failure is observable

The system SHALL raise `tinydb.errors.DatabaseLocked` (a subclass of `TinydbError`) when an exclusive lock cannot be acquired. The exception message MUST include the DB file path.

#### Scenario: Lock failure raises DatabaseLocked with path
- **WHEN** `Pager.__init__` calls `fcntl.flock(LOCK_EX)` and it returns -1 with `EWOULDBLOCK`
- **THEN** the system MUST raise `DatabaseLocked` whose message includes the file path

### Requirement: Close releases all locks

`Database.close()` SHALL release the `threading.RLock` (semantic — `RLock` cannot be force-released, but the lock state is reset to released) and release the `fcntl.flock` (via closing the underlying file fd). After `close()`, the `Database` MUST NOT be usable.

#### Scenario: Close releases file lock
- **WHEN** `Database.close()` is called
- **THEN** the underlying Pager file fd is closed
- **AND** the OS automatically releases the flock held on that fd
- **AND** another process waiting on the lock can now acquire it
```
