# Comet Design Handoff

- Change: tinydb-acid
- Phase: design
- Mode: compact
- Context hash: 160aca09dd07d92dbe22451e57315076d974f4f835ac7b87a4e89ddf7e873dbf

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tinydb-acid/proposal.md

- Source: openspec/changes/tinydb-acid/proposal.md
- Lines: 1-68
- SHA256: a4e050eee8cd3c126a4257e46d0db94333f6c092b319995c59d01c9aa1668954

```md
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

```

## openspec/changes/tinydb-acid/design.md

- Source: openspec/changes/tinydb-acid/design.md
- Lines: 1-164
- SHA256: 2932e2582ac5b9e1abd44234cee235dba381db7d3ee85e1cc680ca5f6d35fa61

[TRUNCATED]

```md
# Design: tinydb-acid

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

教学型嵌入式数据库若不知道 ACID 是什么，永远只是 demo。本 change 把"原子提交 + 崩溃恢复"作为基础能力交付。

## Goals / Non-Goals

**Goals：**
- `BEGIN` / `COMMIT` / `ROLLBACK` 三语句完整语义
- 隐式 auto-commit：未 BEGIN 时单语句 = 一次性事务
- 崩溃恢复：下次 open 时根据 wal 决定是否重放
- 嵌套 BEGIN 报错；COMMIT/ROLLBACK 没在 BEGIN 时报错
- WAL 文件 fsync 在 commit 时
- 启动期 recovery 完成后再 accept user queries

**Non-Goals：**
- 不引入 MVCC / 隔离级别
- 不引入 savepoint
- 不引入异步 fsync
- 不引入加密 / 校验

## Architecture

### WAL 文件格式

```
struct WalHeader {
    magic:    bytes 8   ("TINYWAL\x00")
    schema:   u8        (0x01)
    reserved: bytes 7
}

struct WalRecord {
    txn_id:   u64
    kind:     u8        (0=begin, 1=page_write, 2=commit, 3=rollback)
    page_id:  u32       (page_write 时)
    data:     u32       (page 数据长度)
    payload:  bytes
    crc32:    u32       (record 结尾)
}
```

WAL append-only；每次 write 追加一条；commit 时把 page_write 的 payload 写回主 db 文件。

### Transaction 状态机

```python
class Transaction:
    def __init__(self, txn_id, pager):
        self.id = txn_id
        self.pager = pager
        self.pending_writes: dict[int, bytes] = {}
        self.state = "active"

    def write_page(self, page_id: int, data: bytes):
        self.pending_writes[page_id] = data
        self.pager.wal_append(self.id, page_id, data)

    def commit(self):
        for pid, data in self.pending_writes.items():
            self.pager.write_main_file(pid, data)  # 写主 db
        self.pager.wal_append(self.id, kind=commit)
        self.pager.fsync_main()                     # fsync
        self.pager.wal_truncate_before(self.id)
        self.state = "committed"

    def rollback(self):
        self.pager.wal_append(self.id, kind=rollback)
        self.pager.wal_truncate_before(self.id)
        self.state = "rolled_back"
```

### Executor 改造

```python
def execute(stmt, ...):
    if isinstance(stmt, Begin):

```

Full source: openspec/changes/tinydb-acid/design.md

## openspec/changes/tinydb-acid/tasks.md

- Source: openspec/changes/tinydb-acid/tasks.md
- Lines: 1-57
- SHA256: c07315682dfda1c4bd7bab56591b9de06c94ef5d762ec3b608cd8e6be181eaec

```md
# Tasks: tinydb-acid

> **前置**：`tinydb-engine-v2` 已合并。
> **TDD**：每任务 red→green。

## 1. WAL 基础

- [ ] 1.1 编写 `tests/unit/test_wal.py::test_wal_append_and_read_back`，红
- [ ] 1.2 在 `src/tinydb/wal.py` 定义 WalHeader / WalRecord dataclass + CRC32
- [ ] 1.3 实现 `Wal.append(txn_id, kind, page_id, data)` / `Wal.iter_records()` / `Wal.truncate_before(txn_id)`
- [ ] 1.4 编写 `test_wal_crc_mismatch_truncates_to_last_valid`，红；实现损坏恢复

## 2. Pager 集成 WAL

- [ ] 2.1 编写 `tests/integration/test_pager_wal.py::test_pager_write_through_wal_visible_after_commit`，红
- [ ] 2.2 升级 `Pager` schema_version 到 0x03
- [ ] 2.3 实现 `Pager.write_through_wal(page_id, data, txn)`：写 wal，**不**入主文件
- [ ] 2.4 实现 `Pager.commit_writes(txn)`：把 pending 写主文件 + fsync + truncate wal
- [ ] 2.5 编写 `test_pager_fsync_failure_surfaces_error`，绿

## 3. Transaction 状态机

- [ ] 3.1 编写 `tests/unit/test_transaction.py::test_txn_write_commit`，红
- [ ] 3.2 在 `transaction.py` 实现 `Transaction.write_page / commit / rollback`
- [ ] 3.3 编写 `test_txn_rollback_discards_writes`，绿
- [ ] 3.4 编写 `test_nested_begin_raises`，绿

## 4. Parser 事务语句

- [ ] 4.1 编写 `tests/unit/test_acid_parser.py::test_parse_begin_commit_rollback`，红
- [ ] 4.2 在 `tokenizer.py` 加 `BEGIN` / `COMMIT` / `ROLLBACK`（已存在 BEGIN）
- [ ] 4.3 在 `parser.py` 增加 `Begin / Commit / Rollback` AST 节点 + parse_top_level

## 5. Executor 事务路径

- [ ] 5.1 在 `executor.py` 维护 `current_txn` 状态
- [ ] 5.2 实现 auto-commit：未 BEGIN 单语句走一次性 txn
- [ ] 5.3 实现 BEGIN/COMMIT/ROLLBACK 调度
- [ ] 5.4 把现有 DML/DDL 路径重写为经 `txn.write_page(...)`，commit 时落主文件

## 6. Crash Recovery

- [ ] 6.1 编写 `tests/integration/test_recovery.py::test_kill_mid_commit_recovers_consistently`，红
- [ ] 6.2 在 `recovery.py` 实现扫描 wal、识别 commit/rollback/未结束 txn、应用 page_writes
- [ ] 6.3 `Pager.open` 启动期检测 wal 存在 → 调 `recovery.recover(wal, main_file)`
- [ ] 6.4 编写 `test_recovery_partial_wal_truncates_to_crc_boundary`，绿

## 7. 兼容性

- [ ] 7.1 旧版 .db（v2 schema）open 路径保持原行为（无 wal 就跳 recovery）
- [ ] 7.2 engine-v1 / constraints / aggregation / engine-v2 测试全套继续通过
- [ ] 7.3 文档：`README.md` 增 ACID 段落；`docs/MVP_LIMITATIONS.md` 移除 "best-effort pages" 条目

## 8. 回归

- [ ] 8.1 模块行数：`pager.py ≤ 520`、`transaction.py ≤ 300`、`wal.py ≤ 200`、`recovery.py ≤ 200`、`executor.py ≤ 1000`
- [ ] 8.2 覆盖率 ≥ 90%；新代码 100%

```
