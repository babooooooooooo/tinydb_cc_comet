# Proposal: tinydb-acid

> **范围声明**：本 change 引入 **ACID 事务**：`BEGIN` / `COMMIT` / `ROLLBACK` 三语句 + **write-ahead log (WAL)** + 启动期 **crash recovery**。**前置依赖**：`tinydb-engine-v2`（页格式 0x02、多页 catalog、B-tree index），但 WAL 设计不依赖 index 自身。

## Why

MVP 实现里"每条 `execute()` 立即落盘"导致三个实际问题：
1. 写 5 行的中途 `kill -9` → 文件不可读
2. UPDATE 失败的 delete/insert fallback 中途崩溃 → 数据丢失
3. PRIMARY/UNIQUE 校验失败也直接抛错，但若用户想"先试不通过就回滚"毫无办法

只有 WAL 能同时满足三件事：崩溃可恢复、原子提交、失败回滚。

## What Changes

- **新增** parser/executor：`BEGIN` / `COMMIT` / `ROLLBACK`（事务控制语句；不允许在事务中嵌套 BEGIN）
- **新增** WAL 文件：`<db_path>.wal`；每条写入事务都先 append 一条 record（commit/rollback/aborted 时 fsync）
- **新增** `Pager` 接口：`write_through_wal(page_id, bytes, txn_id)`；page 修改在 commit 前不入主 db file
- **新增** `Transaction` 状态机：
  - `BEGIN`：新建 txn_id，记 active
  - `COMMIT`：把所有 modified pages 从 wal 映回 db file；fsync main db；truncate wal
  - `ROLLBACK`：discard wal 中该 txn 的所有 records
- **新增** 启动期 crash recovery：
  - 若 wal 存在，按 record 类型回放
  - 看到 commit record → 应用其 pages
  - 看到 txn begin 但没 commit → roll back
- **新增** 隐式 auto-commit：未 `BEGIN` 时单语句视为一次性事务
- **新增** schema_version 升级到 0x03（数据存储 v2 + WAL 路径写入）

## Capabilities

### New Capabilities

- `acid-begin-commit-rollback`：`BEGIN` / `COMMIT` / `ROLLBACK` 三个事务控制语句；BEGIN 后所有 DML 都打到 txn；COMMIT 一次性 fsync + 清 wal；ROLLBACK 丢弃
- `storage-wal`：每条 page 修改追加到 wal；commit 时由 wal 一次性拷到主文件
- `crash-recovery`：下次 open 时读 wal，按 record 重放 commit / 丢弃未 commit

### Modified Capabilities

- `storage-engine`：`Pager.write_page` 改为事务可见性版本；用户经由 `txn_write` 落数据，commit 时升级为可见
- `tinydb-engine-v1`（UPDATE）：落入事务路径；commit 失败时 txn rollback 覆盖 in-place + delete/insert 两条路径
- `tinydb-constraints`（PRIMARY/UNIQUE）：失败时若在事务内则 ROLLBACK 回退；自动事务则单语句失败即抛

## Impact

- 受影响/新增文件：
  - `src/tinydb/pager.py`（重写 +~120 行 WAL 逻辑）
  - `src/tinydb/transaction.py`（新增，~250 行）
  - `src/tinydb/wal.py`（新增，~150 行）
  - `src/tinydb/recovery.py`（新增，~150 行）
  - `src/tinydb/executor.py`（+~80 行事务分支）
  - `src/tinydb/parser.py`（+~30 行事务语句）
- 模块行数：
  - `pager.py` ≤ 520 行
  - 新 `transaction.py` ≤ 300 行
  - 新 `wal.py` ≤ 200 行
  - 新 `recovery.py` ≤ 200 行
  - `executor.py` ≤ 1000 行
- 测试：单元 ~40、集成 ~25、crash recovery fuzz ~15
- 文件格式：`.db` 头升 v0x03；新增 `.db.wal` 副文件

## Out of Scope

- MVCC、savepoint、隔离级别 → 永久 out
- 分布式事务、XA → 永久 out
- 异步 fsync / group commit → 不在本 change（性能优化后续）
- 加密 WAL / 校验和 → 永久 out
- 与 `tinydb-engine-v1`/`constraints`/`aggregation` 的功能新增耦合 → 后续
