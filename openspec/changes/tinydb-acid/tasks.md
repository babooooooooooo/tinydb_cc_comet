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
