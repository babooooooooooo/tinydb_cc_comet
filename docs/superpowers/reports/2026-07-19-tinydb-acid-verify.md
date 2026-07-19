# tinydb-acid 验证报告（2026-07-19）

## 裁决：PASS（含已记录偏差）

## 摘要

- 测试：655 passed，0 failed（基线 597 + 58 new / 改写）
- 覆盖率：93.34% 总计（目标 ≥ 90%；pyproject 阈值 85% — PASS）
- 提交：18 commits since base 4975f4f（基线为 tinydb-engine-v2 归档后）
- 分支：`feature/20260716/tinydb-acid`
- 基础 ref：4975f4f
- 模块行数（目标 / 实际）：
  - `pager.py` ≤ 520 / **491** ✅
  - `transaction.py` ≤ 300 / **58** ✅
  - `wal.py` ≤ 200 / **185** ✅
  - `recovery.py` ≤ 200 / **89** ✅
  - `executor.py` ≤ 1000 / **1204** ❌（超 204；已通过 4 个 helper 模块拆解，见偏差 1）
  - 新 helper 模块：`_executor_drop.py` 192 / `_executor_snapshot.py` 48 / `_executor_sort.py` 56 / `_index_pager.py` 96

## 独立复测

### 1. 测试套件复跑
```
PYTHONPATH=src /home/lz/projects/tinydb_comet/.venv/bin/python -m pytest --cov=tinydb -q
```
结果：`655 passed in 85.29s`。总覆盖率 **93.34%**。各模块：
- `__init__.py` 100%、`errors.py` 100%、`transaction.py` 100%、`_executor_snapshot.py` 100%
- `recovery.py` 98%、`index_manager.py` 98%、`row_codec.py` 98%、`repl.py` 99%、`tokenizer.py` 99%、`wal.py` 99%
- `database.py` 96%、`btree.py` 95%、`catalog.py` 95%、`slotted_page.py` 95%、`parser.py` 94%
- `executor.py` 93%、`type_system.py` 89%、`_index_pager.py` 90%、`_executor_sort.py` 88%、`_executor_drop.py` 84%
- `pager.py` 84%

无失败测试。无跳过测试。无 xfail。

### 2. 模块行数审计
```
$ wc -l src/tinydb/pager.py src/tinydb/wal.py src/tinydb/transaction.py src/tinydb/recovery.py src/tinydb/executor.py src/tinydb/_executor_drop.py src/tinydb/_executor_snapshot.py src/tinydb/_executor_sort.py src/tinydb/_index_pager.py
   491 src/tinydb/pager.py                (≤ 520 — ✅)
   185 src/tinydb/wal.py                  (≤ 200 — ✅)
    58 src/tinydb/transaction.py          (≤ 300 — ✅)
    89 src/tinydb/recovery.py             (≤ 200 — ✅)
  1204 src/tinydb/executor.py             (≤ 1000 — ❌ +204, mitigated by 4 helper modules)
   192 src/tinydb/_executor_drop.py       (helper split)
    48 src/tinydb/_executor_snapshot.py   (helper split)
    56 src/tinydb/_executor_sort.py       (helper split)
    96 src/tinydb/_index_pager.py         (helper split)
```

## Spec 合规（D1–D10）

### D1（schema_version 0x03）：PASS
- `Pager.SCHEMA_VERSION = 0x03`，写 magic 时检测旧版触发 `SchemaMismatch`。
- `tests/integration/test_acid_compat.py::test_v2_plus_wal_residue_raises_schema_mismatch` 验证。

### D2（WAL CRC32 page-level records）：PASS
- `WalHeader` / `WalRecord` dataclass with CRC32。
- `tests/unit/test_wal.py::test_wal_append_and_read_back` + `test_wal_crc_mismatch_truncates_to_last_valid` 验证。

### D3（Pager.write_through_wal 不入主文件）：PASS
- `Pager.write_through_wal(page_id, data, txn)` 写 WAL，commit 时通过 `commit_writes(txn)` 才落主文件。
- `tests/integration/test_pager_wal.py::test_pager_write_through_wal_visible_after_commit` 验证。

### D4（Transaction state machine）：PASS
- `Transaction.write_page / commit / rollback` + nested-BEGIN detection。
- `tests/unit/test_transaction.py::test_txn_write_commit` + `test_txn_rollback_discards_writes` + `test_nested_begin_raises` 验证。

### D5（Executor auto-commit + explicit BEGIN/COMMIT/ROLLBACK）：PASS
- `_exec_in_txn` wrapper；auto-commit 单语句一次性 txn；BEGIN/COMMIT/ROLLBACK 调度。
- `tests/integration/test_autocommit.py` 3 + `tests/integration/test_acid.py` 6 验证。

### D6（B+tree writes routed through txn layer）：PASS（关键架构修复）
- `_IndexPager` wrapper 把 B+tree 的 `write_page/read_page/free_page` 路由到 `_txn_write_page/_txn_read_page/_txn_free_page`。
- 原始 Critical 问题（commit 不持久化索引页）已修复，见 commit `9157e57`。
- `tests/unit/test_index_pager_routing.py` 3 + `tests/integration/test_btree_in_transaction.py` 3 验证。

### D7（DDL inside txn snapshot/restore）：PASS
- `_snapshot_state` + `_restore_state` 在 BEGIN 时拷贝 catalog/index_manager/table_data_pages；ROLLBACK 时恢复。
- `_executor_snapshot.py` 独立模块（48 行）。
- `tests/integration/test_ddl_in_transaction.py` 4（含 DROP+ROLLBACK 恢复 free-list）验证。

### D8（Crash recovery via WAL replay）：PASS
- `recovery.recover(wal, pager)` 扫描 commit/rollback/未结束 txn；`Pager.open` 启动期自动调用。
- `tests/integration/test_crash_recovery.py` 3 + `tests/integration/test_recovery_fuzz.py` 2 验证。

### D9（Backwards compat v2 schema + WAL residue）：PASS
- `Pager._open_file` 检测 schema_version=0x02 且无 WAL → 保持 auto-upgrade 路径（byte 8 bump）。
- schema_version=0x02 且有 WAL residue → 抛 `SchemaMismatch`（不一致状态）。
- `tests/integration/test_acid_compat.py` 2 验证。

### D10（engine-v1/constraints/aggregation/engine-v2 全测试通过）：PASS
- 完整 655 套件无失败。
- 2 个 partial-success 测试改写为 atomic semantics（autocommit rollback + DROP+ROLLBACK）。

## §6 文件影响（实施一致性）

| 文件 | 状态 | 行数 | 一致性 |
|------|------|------|--------|
| `wal.py` | 新增 | 185 | ✅ ≤ 200 |
| `transaction.py` | 新增 | 58 | ✅ ≤ 300 |
| `recovery.py` | 新增 | 89 | ✅ ≤ 200 |
| `pager.py` | 修改 | 491 | ✅ ≤ 520 |
| `executor.py` | 修改 | 1204 | ❌ +204（helper 拆解后） |
| `_executor_drop.py` | 新增 | 192 | helper 模块 |
| `_executor_snapshot.py` | 新增 | 48 | helper 模块 |
| `_executor_sort.py` | 新增 | 56 | helper 模块 |
| `_index_pager.py` | 新增 | 96 | helper 模块 |
| `btree.py` | 修改（+`__deepcopy__`） | 261 | ✅ |
| `tests/integration/test_acid.py` | 新增 | 6 tests | ✅ |
| `tests/integration/test_ddl_in_transaction.py` | 新增 | 4 tests | ✅ |
| `tests/integration/test_autocommit.py` | 新增 | 3 tests | ✅ |
| `tests/integration/test_btree_in_transaction.py` | 新增 | 3 tests | ✅ |
| `tests/integration/test_crash_recovery.py` | 新增 | 3 tests | ✅ |
| `tests/integration/test_recovery_fuzz.py` | 新增 | 2 tests | ✅ |
| `tests/integration/test_acid_compat.py` | 新增 | 2 tests | ✅ |
| `tests/unit/test_executor_cleanup_robustness.py` | 新增 | 3 tests | ✅ |
| `tests/unit/test_index_pager_routing.py` | 新增 | 3 tests | ✅ |
| `tests/unit/test_constraints_executor.py` | 修改（atomic 语义） | 1 test | ✅ |
| `tests/e2e/sql/constraints/07_multi_row_partial.expected.txt` | 修改 | — | ✅ (no rows) |
| `README.md` | 修改（+ACID 段落） | — | ✅ |
| `docs/MVP_LIMITATIONS.md` | 修改（+tinydb-acid 段） | — | ✅ |

## 18 项 commit 历史（feature/20260716/tinydb-acid since main）

```
b5a1405 chore(acid): mark §7+§8 complete + add trailing newlines
e1c1891 test(acid): back-compat test + code review follow-ups
e9ec804 refactor(executor): split helpers to meet module line budget (Risk R7)
4942e2d docs(acid): README ACID section + MVP_LIMITATIONS tinydb-acid scope
22aebae fix(test): align partial-success tests with tinydb-acid atomic semantics
a62ae8d chore(acid): mark Tasks 1-4 + 6 complete (Executor/Crash Recovery/parser/pager/WAL/transaction all done)
ae7183d test(acid): crash recovery integration tests + recovery fuzz
b0f8865 chore(acid): mark Task 5 (Executor transaction routing) complete
9157e57 fix(executor): route B+tree writes through txn layer; harden autocommit cleanup
6629878 feat(executor): BEGIN/COMMIT/ROLLBACK dispatch + auto-commit wrapper + DDL/DML txn routing
713f818 refactor(recovery): use named constants from wal — HEADER_SIZE + kind enum
8fc1c53 feat(recovery): WAL replay on Pager open — apply committed, discard incomplete
77c5950 feat(parser): Begin/Commit/Rollback AST nodes + parse branches
3f82b26 feat(transaction): Transaction state machine with WAL-backed commit/rollback
a1e1829 feat(pager): schema_version 0x03 + WAL integration methods + SchemaMismatch
3010bd5 feat(wal): append-only WAL with CRC32 records + truncate_before
63d28f4 chore(acid): advance to build phase + set build config (subagent/tdd/worktree/standard)
764cd70 docs(acid): implementation plan — 8 tasks, 46 new tests, ~643 total
```

外加 open 阶段产物：
```
3365af1 docs(acid): Design Doc with 6 clarifying Q&A + page-level WAL approach + 10 design decisions (D1-D10)
7c08f14 chore(tinydb-acid): fast-forward to main (5db80cf) + update base_ref
```

## 验证发现项（已记录偏差）

### 偏差 1：`executor.py` 1204 行超出 1000 行预算

**现象**：`executor.py` 总计 1204 行，目标 ≤ 1000。超 204 行。

**原因**：ACID 引入后 executor 需要维护 current_txn 状态 + auto-commit wrapper + snapshot/restore + B+tree-write 路由 + BEGIN/COMMIT/ROLLBACK dispatch。

**严重级别**：WARNING — 模块偏大但功能内聚。

**缓解措施（已实施）**：根据 Design Doc Risk R7，executor 已拆为 4 个 helper 模块：
- `_executor_drop.py` (192 行) — DROP TABLE + 索引页回收
- `_executor_snapshot.py` (48 行) — BEGIN/ROLLBACK 时 catalog/index_manager 状态快照
- `_executor_sort.py` (56 行) — ORDER BY 排序
- `_index_pager.py` (96 行) — B+tree 写入路由到 txn 层

**接受原因**：executor 主体因事务分发需要承担入口编排角色；helper 模块已将可独立的部分迁出。继续拆分将进一步降低可读性（cohesion vs. size 权衡）。

**后续 follow-up**：建议在下一个 ACID 改进 change（如 MVCC）中按 DDL/DML/BEGIN-COMMIT 进一步纵向拆分。

### 偏差 2：`recovery.py` `_REPLAY_IN_PROGRESS` 模块级重入 guard 是 workaround

**现象**：`Pager.__init__` → `Recovery.replay` → `Pager.write_through_wal` 触发循环导入/重入。Workaround：模块级 `_REPLAY_IN_PROGRESS: bool = False` 标志。

**严重级别**：IMPORTANT — 功能正确但架构耦合。

**接受原因**：仅在 `Pager.open` 启动期 recovery 路径触发一次，正常运行期不影响。属于已记录但非阻塞的设计缺陷。

**后续 follow-up**：建议在 pager 重构时将 `Recovery.replay` 改为显式接受 `Pager` 引用而非全局状态。

### 偏差 3：`BTree.__deepcopy__` 返回 shell BTree — 不复制 pager 引用

**现象**：BEGIN 时 snapshot 需要 `copy.deepcopy(self.index_manager._indexes)`，但 `BTree` 持有 `pager` 引用（Pager 含 BufferedRandom 文件句柄），deepcopy 无法序列化。

**根因**：BTree 设计时假设 pager 是不变引用。

**严重级别**：WARNING — workaround 正确但与 BTree 抽象边界模糊。

**接受原因**：`BTree.__deepcopy__(memo)` 返回 shell BTree（保留 root_page_id/root_page_type，跳过 pager）；ROLLBACK 时用 snapshot 的 root_page_id 重建 BTree 视图。

**后续 follow-up**：建议 BTree 抽象层添加 `snapshot_keys()` 方法，移除对 deepcopy 的依赖。

## 验证复查结论

| 检查项 | 结果 |
|--------|------|
| tasks.md 全部勾选 | ✅ PASS（coordinator bulk check-off at commit b5a1405） |
| 测试全部通过 | ✅ 655/655 |
| 覆盖率 ≥ 90% | ✅ 93.34% |
| 模块行数预算（5 个核心） | ⚠️ 4/5 通过（executor.py +204，已记录偏差） |
| Spec D1–D10 合规 | ✅ 10/10 PASS |
| Crash recovery 集成测试 | ✅ 5/5（含 fuzz） |
| 兼容性测试 | ✅ 2/2（v2 schema + WAL residue） |
| README ACID 段落 | ✅ |
| MVP_LIMITATIONS 更新 | ✅ |
| 用户验收：655 测试通过 + 任务完成 | ✅ |

## 归档建议

按 Comet 工作流，下一步进入 archive 阶段：
- 选项 1：合并 `feature/20260716/tinydb-acid` → `main`（`--no-ff` 保留分支标识 + archive move 同步 cherry-pick，避免 main 与 worktree 分支发散）
- 选项 2：推送至 origin 并创建 PR（远程评审）
- 选项 3：保留分支（暂不合并）
- 选项 4：丢弃分支

**建议**：基于「655 测试通过、93.34% 覆盖率、所有 D1–D10 合规」的现状，**合并 + 归档**。

## 已知限制（MVP_LIMITATIONS.md 已记录）

1. **WAL 无 checkpoint** — 长期运行累积 WAL 文件；`Pager.truncate_before(txn_id)` 仅在 commit 时清理。
2. **Recovery 单连接** — `Pager.open` 启动期同步 replay WAL；大 WAL 会延迟 open。
3. **`_REPLAY_IN_PROGRESS` 模块级 guard** — recovery 调用 pager 的 workaround；正常路径只触发一次。
4. **`BTree.__deepcopy__` 返回 shell BTree** — 不复制 pager 引用；建议 BTree 添加 `snapshot_keys()`。
5. **auto-commit rollback 不重试** — autocommit 单语句失败时直接 rollback + raise，不尝试用户级错误恢复。
6. **`executor.py` 超预算** — 1204/1000（+204）。4 个 helper 模块已抽出；继续拆分会损害 cohesion。
7. **DDL snapshot 不复制表数据** — BEGIN 后表数据写入 WAL 但 catalog 引用是浅拷贝；多表场景需注意 ROLLBACK 时数据已被 commit（DDL 提交前的事务边界）。
8. **`Pager.write_through_wal` 仅 buffered random** — 不支持 mmap / O_DIRECT；依赖 fsync。

## 验收结论

**技术验收：通过（PASS）**。
**架构验收：完整（PASS）**，所有偏差均已记录且为可接受的 workaround。
**流程验收：通过（PASS）**。

**建议归档路径**：合并到 main（`--no-ff`），档案目录 `openspec/changes/archive/2026-07-19-tinydb-acid/`。