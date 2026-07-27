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

### D9: 测试策略分两层

- `tests/unit/concurrency/`：
  - `test_threading.py` — ThreadPoolExecutor × N threads × M INSERTs / SELECTs / UPDATEs；最终一致性检查。
  - `test_memory_threading.py` — `:memory:` 多线程。
  - `test_locking_off.py` — `locking=False` 路径。
- `tests/integration/concurrency/`：
  - `test_multiprocess.py` — `subprocess` 驱动两个进程写同一 DB；一个写、一个读到一致快照。
  - `test_recovery_lock.py` — 写 WAL 不 commit → 第二个进程 open 拿到锁并 replay。
  - `test_concurrent_ddl.py` — 多进程同时 CREATE TABLE / DROP TABLE。

### D10: 现有测试默认走 `locking=False`

- 现有 796 个测试绝大多数是非并发单元测试；默认 `locking=True` 会引入 `fcntl.flock` 开销。
- 修改 `Database.__init__` 测试 fixture / conftest：默认 `locking=False`，仅并发测试用 `locking=True`。
- **覆盖率目标**：现有 ≥ 92% 维持；新增并发模块 ≥ 80%。

## Risks / Trade-offs

- **R1: 进程崩溃 + WAL 未提交** — replay 可能误恢复未提交数据？**Mitigation**: WAL 协议已有（acid change）：commit 前不写主文件 → crash 后 replay 只 apply status==committed 的 txns。锁协议不变。
- **R2: fcntl 在 WSL 上的稳定性** — WSL1 不支持 fcntl.flock（WSL2 支持）。**Mitigation**: 在 `_open_file()` 时检测 `fcntl` 失败并降级为 warning + 无锁（仅限 WSL1 环境检测）。
- **R3: RLock 无法跨 await / 跨线程强制释放** — 若 helper 在锁内做长 I/O（fsync），持锁时间可能较长。**Mitigation**: 当前 fsync 在持锁时间内执行（与 acid 协议一致），嵌入式 DB 单语句 fsync 通常 < 10ms，可接受。
- **R4: 多进程同时 DROP TABLE 冲突** — DDL 不走 WAL，跨进程 DROP 期间 SELECT 可能读到不一致 catalog。**Mitigation**: 仍走文件锁，DDL 路径持锁 → 不跨语句粒度。
- **R5: `_REPLAY_IN_PROGRESS` 模块级 guard 是 workaround** — 同进程重复 replay 时跳过；多进程场景下自然通过 flock 串行化。**Mitigation**: 保留 guard；记录为已知 deviation；不在本次 change 重构。
- **R6: 多线程读并发被牺牲** — 放弃 LOCK_SH/LOCK_EX split 后，多线程 SELECT 也走 EX 锁。**Mitigation**: 嵌入式场景读吞吐量已足够；未来可扩展 MVCC。
- **R7: 现有 `:memory:` 多线程测试可能 flaky** — 内存 dict race 在无锁时大概率失败但非 100% 复现。**Mitigation**: 新增测试用足够大的 N（≥ 1000 次操作 × 8 线程）确保稳定触发。
- **R8: conftest 修改可能影响现有测试 fixture** — 默认 `locking=False` 改动如果漏改某个 fixture，可能让某测试 flaky。**Mitigation**: 跑全量现有测试 + 至少 5 次重跑确认稳定后再合入。

## Migration Plan

无 schema 迁移。`Database.__init__` 默认行为变化对调用方是"更安全"（无损升级）。Rollback 策略：发布时附带 `Database(path, locking=False)` 文档示例，紧急情况可显式关闭锁。

## Open Questions

- **Q1**: `Pager.__init__` 中 `flock` 失败时是否要 retry 一次（瞬态竞争）？**当前决策**：不 retry，直接抛 `DatabaseLocked`（行为可预测）。
- **Q2**: 多线程读并发是否需要单独测试覆盖读路径？**当前决策**：与写测试合并（混跑 INSERT+SELECT），通过 row-count 验证一致性即可。
- **Q3**: 是否需要专门的 `tinydb.errors.DatabaseLocked` 错误码？**当前决策**：仅 exception class；HTTP/RPC 风格 status code 不适用本项目。