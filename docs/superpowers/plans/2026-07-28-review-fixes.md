---
change: tinydb-review-2026-07-28-fixes
design-doc: docs/superpowers/specs/2026-07-28-review-fixes-design.md
base-ref: 08a9ca55e316bd7114f5ef6193e969028cec0330
---

# 修复 2026-07-28 代码评审 9 项 HIGH 问题 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-07-28 全项目代码评审中 9 项跨 agent 共识的 HIGH 级别问题（WAL 顺序、B+tree 链断裂、catalog 溢出静默截断、Database 生命周期 race/异常泄漏、REPL `_cmd_read` O(n²)、`_IntCodec` 往返不对称、`_VarcharCodec`/`_CharCodec` 解码校验缺失、`DatabaseLocked` 父类不一致），共 7 个并行 task 全部基于 TDD 实现、维持 967/2 baseline、覆盖率 ≥ 90%。

**Architecture:** 工作树基于 main @ `08a9ca5`，在独立 worktree `tinydb-review-2026-07-28-fixes`（branch `feature/20260728/review-fixes`）上以 subagent-driven-development 模式并行执行 7 个互不重叠的 task（每个 task 负责一个文件/子模块）。每个 task 遵循 TDD（先写失败测试 → 最小实现 → 验证通过 → thorough review ≤ 2 轮 → commit）。所有 task 完成后整体 pytest + coverage 校验，然后 `--no-ff` 合并到 main，cherry-pick archive move，archive change。

**Tech Stack:** Python 3.11+、pytest、pytest-cov、Threading、fcntl/Pager WAL、JSON catalog、B+tree leaf chain 链表、REPL dispatch、OpenSpec、Comet workflow。

---

## 文件结构（File Structure）

| Task | 修改文件 | 创建/修改测试 | 职责边界 |
|------|----------|----------------|----------|
| T1 | `src/tinydb/transaction.py`、`src/tinydb/pager.py` | `tests/unit/transaction/test_wal_ordering.py` | WAL write-ahead 协议顺序 |
| T2 | `src/tinydb/btree.py` | `tests/unit/btree/test_leaf_chain.py`、`tests/unit/btree/test_chain_after_split.py` | B+tree leaf split 链修补 |
| T3 | `src/tinydb/catalog.py` | `tests/unit/catalog/test_overflow_chain_robustness.py` | catalog 溢出链真实贪心分割 |
| T4 | `src/tinydb/database.py` | `tests/unit/database/test_close_race.py`、`tests/unit/database/test_init_cleanup_on_exception.py` | Database 生命周期 hardening |
| T5 | `src/tinydb/_repl_meta.py` | `tests/unit/repl/test_cmd_read_perf.py` | REPL `_cmd_read` 性能 |
| T6 | `src/tinydb/type_system.py` | `tests/unit/test_type_system.py`（追加） | codec 往返对称 |
| T7 | `src/tinydb/errors.py`、`CHANGELOG.md` | `tests/unit/test_error_hierarchy.py` | 异常层级清理 |

> 7 个 task 文件互不重叠，可完全并行（subagent-driven-development 推荐模式）。
> 唯独 `CHANGELOG.md`（T7）需在所有 task 完成统一 commit 之前最后由协调者追加。

---

## 集成策略（Integration Strategy）

### 并行执行模式
- **build_mode**：`subagent-driven-development`
- **review_mode**：`thorough`（每 task ≤ 2 轮 review-fix 循环）
- **tdd_mode**：`tdd`（先写失败测试，最小实现，验证通过）
- **worktree 隔离**：`tinydb-review-2026-07-28-fixes` worktree，branch `feature/20260728/review-fixes`（基于 main @ `08a9ca5`）
- **并行子任务**：T1-T7 互不重叠，每个 task 由独立 subagent 负责（同一 worktree 不同 commit）。协调者按序 review 每个 task 完成结果。

### 协调者责任
- 在 T1-T6 全部 commit 之后追加 T7 的 `CHANGELOG.md` 变更（如果 T7 subagent 没写）
- 在所有 task commit 后运行整树 `pytest --no-cov` 验证 967/2 baseline
- 运行 `pytest --cov=src --cov-report=term-missing` 验证覆盖率 ≥ 90%
- `--no-ff` 合并到 main
- Cherry-pick archive move 到 main
- 写 `docs/superpowers/reports/2026-07-28-review-fixes-verify.md` 报告
- `openspec archive tinydb-review-2026-07-28-fixes --yes`
- 清理 worktree

### Test 套件不变量
- 967 baseline 维持（来自 concurrency-control + cli-enhancements + type-codec-cleanup 历史累计）
- 2 个 skip 维持（`cc51c46` 修复暴露的 latent test bug 之外的历史 skip）
- 覆盖率 ≥ 90%（项目历史 ~92.59%，不显著下降）
- `cc51c46` 测试仍通过（`test_explain_does_not_execute` 修复未 regress）

### 合并策略
- 所有 task commit 到 `feature/20260728/review-fixes`
- 协调者验证后 `git checkout main && git merge --no-ff feature/20260728/review-fixes -m "fix(review): apply 9 HIGH code-review findings"`
- Cherry-pick archive move commit 到 main（保持与历史归档一致）
- `git tag -a archive/review-fixes-feature-pre-archive feature/20260728/review-fixes` 保留分支可达性
- `git worktree remove --force tinydb-review-2026-07-28-fixes` 物理清理

### 最终验证（Final Verification）
1. 整树 `pytest --no-cov` 通过（967 + 新增测试，2 skip 维持）
2. 覆盖率 `pytest --cov=src --cov-report=term-missing` ≥ 90%
3. 静态检查 `ruff check src/` + `mypy src/` 无新增 warning
4. 性能 smoke test：5MB SQL `.read` < 1s
5. 跨平台 smoke：Linux/WSL（本环境）完整测试；macOS/Windows fcntl/WindowsError 子模块按现有 closeout 文档处理

---

## Task 1: WAL 协议顺序

**Files:**
- Modify: `src/tinydb/transaction.py:37-67`（`write_page`、`commit`、`rollback`）
- Modify: `src/tinydb/pager.py`（新增 `fsync_wal` 公开方法）
- Test: `tests/unit/transaction/test_wal_ordering.py`（新建）

**Goal:** `Transaction.commit()` 严格遵守 write-ahead 协议 — `wal_append_commit → fsync_wal → write_main_page × N → fsync_main → wal_truncate_before(self.id + 1)`。崩溃可恢复性 + 异常时不残留 ACTIVE 状态。

**Test Scope（Design Doc §1.3）:**
- `test_commit_writes_wal_commit_before_main`：spy pager 验证调用顺序
- `test_commit_failure_does_not_leave_active`：注入 IOError，验证 state 转 ROLLED_BACK
- `test_truncate_uses_id_plus_one`：spy `wal_truncate_before` 验证参数 `id + 1`
- `test_write_page_wal_first_on_failure`：注入 `wal_append_page` 失败，验证 `pending_writes` 未 mutate
- Baseline regression：`tests/unit/transaction/test_commit.py` + `tests/integration/test_acid_compliance.py` 全过

**Acceptance Criteria（Design Doc §1.5）:**
- `commit()` 调用顺序严格：wal_append_commit → fsync_wal → write_main_page × N → fsync_main → wal_truncate_before(self.id+1)
- `write_page()` 中 `wal_append_page` 抛异常时 `pending_writes` 不残留
- `commit()` 任意步骤抛异常时 state = ROLLED_BACK，无 ACTIVE 残留
- Recovery idempotent：相同 commit id 重复 replay 不会重复写主库
- 967 baseline + acid 测试全过

**Risks + Mitigations（Design Doc §1.4）:**
- R1.1：`Pager.fsync_wal` 需 `_wal_file` 是 BufferedRandom；先读 `pager.py` 头部确认接口 → 新增公开方法 + fsync 幂等性测试
- R1.2：commit 中途 IOError → state ROLLED_BACK，但主库部分写入可能已发生。崩溃恢复时 WAL commit 记录让 replay 重新 apply（必须 idempotent）→ 已通过 test_commit_failure_does_not_leave_active + recovery 测试覆盖
- R1.3：rollback `wal_truncate_before(self.id + 1)` 保留 rollback 记录与后续事务 → 后续事务的 WAL history 增加但每条记录元信息独立不会乱序

---

## Task 2: B+tree Leaf Chain 完整性

**Files:**
- Modify: `src/tinydb/btree.py:187-208`（`_insert_into_parent` 中 leaf split 分支）
- Test: `tests/unit/btree/test_leaf_chain.py`（新建）
- Test: `tests/unit/btree/test_chain_after_split.py`（新建）

**Goal:** 修复 B+tree leaf split 时新右侧 LeafNode 的 `next_leaf_id=0` 不被修补为原 leaf 的 next 指针的 bug。Split 时保存 `original_next = leaf.next_leaf_id`，分配新右侧 page，把 `right.next_leaf_id = original_next`、`left.next_leaf_id = right_pid`，保持链 `L1 → L2 → L3 → 0` 不变。

**Test Scope（Design Doc §2.3）:**
- `test_split_preserves_chain_to_original_right_neighbor`：随机插入 3000 行触发 3+ leaf split，range scan 命中全部
- `test_reverse_range_scan_after_multi_split`：反向插入 500..1，forward range scan 命中 500
- `test_split_chain_rightmost_leaf_no_regression`：200 行 zfill 插入，全部 200 命中
- Baseline regression：`tests/unit/btree/test_btree_basic.py` + `tests/integration/test_engine_v2.py` 全过

**Acceptance Criteria（Design Doc §2.5）:**
- 随机插入 3000 行 → range scan 命中全部
- 分裂后 right.next_leaf_id = original rightmost page id
- 反向 range scan / descending insert 不退化
- 现有 engine-v2 + index tests 全过

**Risks + Mitigations（Design Doc §2.4）:**
- R2.1：`right.next = original.next` 单步法不修改 root 状态 — 与现有 `_insert_into_parent` 路径独立，最小 diff
- R2.2：stress test（3000 行 → 多 leaf split）可能暴露 index_manager 中其他未追踪状态 → 若 3000 行 stress test 通过即 OK；否则归类已知偏差

---

## Task 3: Catalog 溢出链健壮性

**Files:**
- Modify: `src/tinydb/catalog.py:_serialize_segments`（`218-242`）— 真实贪心分割 + 单表跨段
- Modify: `src/tinydb/catalog.py:_pack_chain`（`245-270`）— 移除防御性截断
- Test: `tests/unit/catalog/test_overflow_chain_robustness.py`（新建）

**Goal:** 移除 `_pack_chain` 防御性截断 `body = seg[:CHAIN_BODY_SIZE]` 掩盖的上游 `_serialize_segments` bug。修复后上游实现真正贪心分割（单表 schema 超过 CHAIN_BODY_SIZE 时 columns 跨段），下 pack_chain 仅在真正 corrupt 时 raise。

**Test Scope（Design Doc §3.3）:**
- `test_single_table_overflow_chain_no_loss`：单表 250 列 → 多段且 round-trip 不丢失列
- `test_pack_chain_raises_on_oversize_segment`：monkey-patch 注入超大段 → CatalogCorrupt
- `test_round_trip_with_mixed_wide_and_narrow`：宽表 + 20 窄表混合 → round-trip 21 表全部
- Baseline regression：现有 catalog 测试全过

**Acceptance Criteria（Design Doc §3.5）:**
- 单表 200+ 列 → `_pack_chain` 输出页数 ≥ ⌈payload / CHAIN_BODY_SIZE⌉
- `_serialize_segments` 中 `len(seg) > CHAIN_BODY_SIZE` 真正触发跨段
- Round-trip 完整保留所有列
- `_pack_chain` 输入预 corrupt 段 → CatalogCorrupt
- 现有 catalog 测试全过

**Risks + Mitigations（Design Doc §3.4）:**
- R3.1：单列超大（>CHAIN_BODY_SIZE 的单列）仍不能跨段 → 先检查列大小；超长列抛 CatalogCorrupt + 文档化为已知限制
- R3.2：`_split_single_table` 实现未细化 → 在 build 阶段由 implementer 按需实现；满足 200+ 列测试即可
- R3.3：改 `_serialize_segments` 行为可能影响其他 module 单元测试 → 跑完整 catalog 测试集

---

## Task 4: Database Lifecycle Hardening

**Files:**
- Modify: `src/tinydb/database.py:78-98`（`__init__` 中 Pager 之后步骤包 try/except）
- Modify: `src/tinydb/database.py:121-128`（`execute` 中 `_is_closed` 移入 lock 内）
- Modify: `src/tinydb/database.py:explain_plan`（同样修复）
- Modify: `src/tinydb/database.py:close`（Pager 异常不掩盖原状态）
- Test: `tests/unit/database/test_close_race.py`（新建）
- Test: `tests/unit/database/test_init_cleanup_on_exception.py`（新建）

**Goal:** (a) `_is_closed` 检查移入 `with self._acquire_lock()` 内（消除 race）。(b) `__init__` 中 Pager 之后的步骤包 try/except → 释放 OS 锁。(c) `close()` 中 Pager 关闭异常时不置 `_is_closed`，允许重试。

**Test Scope（Design Doc §4.3）:**
- `test_close_during_execute_raises_runtime_error`：线程 A close + 线程 B 100 次 execute，要么全成功要么 RuntimeError("Database is closed")
- `test_init_cleanup_releases_pager_lock`：mock `Catalog.from_bytes` raise → 重新 `Database(path)` 成功（OS 锁已释放）
- Baseline regression：现有 database.py 测试全过；`cc51c46` 修复保留

**Acceptance Criteria（Design Doc §4.5）:**
- `_is_closed` 检位于 `with self._lock:` 内
- `__init__` 中后期异常触发 `self.pager.close()`
- `close()` idempotent + Pager 异常不掩盖原状态
- `cc51c46` test 仍过（latent fix preserved）
- 现有 database 测试全过

**Risks + Mitigations（Design Doc §4.4）:**
- R4.1：`_is_closed` 移入 lock 内会轻微改变现有 `cc51c46` 修复行为 → 保留同一异常类型与消息；回归 `cc51c46` 测试
- R4.2：`__init__` 末段抛异常时 `pager.close()` 也会抛异常 → 第二个异常覆盖第一个（Python 默认）→ 已用 try/finally + `_is_closed=True` 保证；re-raise 原始异常优先

---

## Task 5: REPL `_cmd_read` 性能

**Files:**
- Modify: `src/tinydb/_repl_meta.py:128-136`（`_cmd_read` 字符累积改 list-join）
- Test: `tests/unit/repl/test_cmd_read_perf.py`（新建）

**Goal:** `_cmd_read` 当前用 `buf += char` 在 16 MiB 文件上 O(n²)。改为 list-join 拼接（`buf_parts: list[str] = []` + `''.join(buf_parts)` 或 `buf := "".join(buf_parts)` walrus 表达式）。5 MB SQL 文件 `.read` < 1s；16 MB < 5s。

**Test Scope（Design Doc §5.3）:**
- `test_read_5mb_under_1s`：5MB SQL body，elapsed < 1.0s
- `test_read_16mb_under_5s`：16MB SQL body，elapsed < 5.0s
- `test_small_read_unaffected`：小文件功能不变（INSERT INTO t VALUES (1)/(2) 全部能读到）
- Baseline regression：967 baseline 维持

**Acceptance Criteria（Design Doc §5.5）:**
- 5 MB `.read` < 1 秒
- 16 MB `.read` < 5 秒
- 现有小文件 `.read` 行为不变
- 967 baseline 维持

**Risks + Mitigations（Design Doc §5.4）:**
- R5.1：若 `_cmd_read` 由 dispatch 后是 sync 调用（不返回 Row），需追加 `test_db.execute_handles_dot_read` → 与 T7 task 一起验证 dispatch path
- R5.2：walrus `:=` 在 nested 表达式中可读性略差 → 注释说明为何把 build + check 合并

---

## Task 6: Codec 往返对称

**Files:**
- Modify: `src/tinydb/type_system.py:_IntCodec`（新增 `_check_bounds` 共享方法 + encode_py/decode_bytes 双调用）
- Modify: `src/tinydb/type_system.py:_VarcharCodec`（新增 `_check` + `decode_bytes` 调用）
- Modify: `src/tinydb/type_system.py:_CharCodec`（新增 `_check` + `decode_bytes` 调用）
- Test: `tests/unit/test_type_system.py`（追加 4 个测试）

**Goal:** (a) `_IntCodec.encode_py` 和 `_IntCodec.decode_bytes` 均调用统一的 `_check_bounds` — 统一 32-bit signed `[-2^31, 2^31-1]` 语义。(b) `_VarcharCodec.decode_bytes` 调用 `_check(len(text))`，异常类型 `CodecError`。(c) `_CharCodec.decode_bytes` 同上。DV7 编码端只注释 do-not-fix；不动实现。

**Test Scope（Design Doc §6.3）:**
- `test_int_codec_symmetric_bounds_encode`：`_IntCodec().encode_py(2**31, "INT")` → CodecError
- `test_int_codec_symmetric_bounds_decode`：`_IntCodec().decode_bytes((2**31).to_bytes(4, "big", signed=True), 0, "INT")` → CodecError
- `test_varchar_decode_enforces_max_length`：构造 MAX_LEN+1 长度 → CodecError
- `test_char_decode_enforces_max_length`：构造 MAX_LEN+1 长度 → CodecError
- Baseline regression：现有 codec 测试全过

**Acceptance Criteria（Design Doc §6.5）:**
- `_IntCodec.encode_py(2**31)` → CodecError
- `_IntCodec.decode_bytes(<2^31>)` → CodecError
- `_VarcharCodec.decode_bytes(<too-long>)` → CodecError
- DV7 编码端保持现状（仅注释 do-not-fix）
- 现有 codec 测试全过

**Risks + Mitigations（Design Doc §6.4）:**
- R6.1：`_VarcharCodec` 的 `_check` 包含 `[1, 1024]` 范围 → 历史数据若 varchar 为 0 长度 → 解码报错 → 历史 varchar 字段应该非空；若引发回归，归类已知偏差 + 后续 fix
- R6.2：`_IntCodec.decode_bytes` 当前签名可能是 `(data, offset, sql_type)` 或 `(data, offset, length, sql_type)` → build 阶段 implementer 确认
- R6.3：`_VarcharCodec.encode_py` 编码端 DV7 不修（与 type-codec-cleanup closeout 一致）→ 加注释 + verify report 标注

---

## Task 7: Exception 层级清理

**Files:**
- Modify: `src/tinydb/errors.py:143-153`（`class DatabaseLocked(ExecutionError)` 替代 `TinydbError`）
- Modify: `src/tinydb/errors.py:__all__`（确认 `DatabaseLocked` 导出）
- Modify: `CHANGELOG.md`（标注 non-breaking）
- Test: `tests/unit/test_error_hierarchy.py`（新建）

**Goal:** `DatabaseLocked` 与其他用户错误不一致 — subclass `TinydbError` 而不是 `ExecutionError`。修复：改 parent 为 `ExecutionError`，统一通过 `errors.__all__` 仍导出。Non-breaking: `except TinydbError` 仍捕获（因父类链向上）。

**Test Scope（Design Doc §7.3）:**
- `test_database_locked_subclasses_execution_error`：`issubclass(DatabaseLocked, ExecutionError)` + `issubclass(DatabaseLocked, TinydbError)` 双 True
- `test_repl_catches_database_locked_via_tinydb_error`：REPL `except (TinydbError, ...)` 路径仍捕获 DatabaseLocked
- `test_database_locked_importable_from_wildcard`：`from tinydb.errors import *` 包含 DatabaseLocked
- Baseline regression：967 baseline + cc51c46 test 仍过

**Acceptance Criteria（Design Doc §7.5）:**
- `class DatabaseLocked(ExecutionError)` — 替换 TinydbError
- `from tinydb.errors import *` 仍含 DatabaseLocked
- REPL `_run_sql` `except TinydbError` 仍捕获 DatabaseLocked
- CHANGELOG 标注 non-breaking
- 967 baseline + cc51c46 test 仍过

**Risks + Mitigations（Design Doc §7.4）:**
- R7.1：父类变更如被外部代码用 `isinstance(e, TinydbError)` 不严格判定 → True 仍成立（父类链向上）
- R7.2：REPL 子系统若对 `DatabaseLocked` 做特殊处理（与 `ExecutionError` 子类分支判定），需单测覆盖 → grep REPL codebase + 单测验证
- R7.3：已有 cli-enhancements closeout 报告 + cc51c46 fix 不可 regress

---

## Self-Review Checklist

**1. Spec coverage:**
- Item 1 (WAL) → T1 ✓
- Item 2 (B+tree) → T2 ✓
- Item 3 (catalog) → T3 ✓
- Item 4 (DB lifecycle race) → T4 ✓
- Item 5 (`_cmd_read` perf) → T5 ✓
- Item 6 (DB init cleanup) → T4 (与 4 合并) ✓
- Item 7 (`_IntCodec` symmetry) → T6 ✓
- Item 8 (`DatabaseLocked` parent) → T7 ✓
- Item 9 (`_VarcharCodec`/`_CharCodec` decode check) → T6 (与 7 合并) ✓

**2. Placeholder scan:** ✓ 无 "TBD"、"implement later"、"fill in details" 等占位符

**3. Type consistency:** ✓ 所有 task 文件路径、函数名、异常类型与 Design Doc §1-§7 一致；`DatabaseLocked` 在 T7 改 parent 为 `ExecutionError`；`_check_bounds`/`_check` 在 T6 是新增方法；`fsync_wal` 在 T1 是新增方法；`_split_single_table` 在 T3 是新增辅助函数

**4. Test baseline:** 967 + 2 skip 维持；cc51c46 修复保留；覆盖率 ≥ 90%

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-28-review-fixes.md`.**

按 Comet 工作流要求，build 阶段选择执行模式：

**1. Subagent-Driven (recommended)**
- 协调者在 `tinydb-review-2026-07-28-fixes` worktree 中按 task 顺序派发 7 个 implementer subagent
- 每个 task 完成后由 code-reviewer subagent 严格 review
- review_mode=thorough，每 task ≤ 2 轮 review-fix 循环
- 协调者每 task 验收后立即 commit + tasks.md 打勾
- 全部 task commit 后协调者跑全量验证（pytest + coverage + ruff + mypy）
- `--no-ff` 合并到 main → cherry-pick archive move → 写 verify report → archive

**2. Inline Execution**
- 在主会话按 T1→T2→...→T7 顺序执行，每 task 完成后做 batch checkpoint
- 适用于 token 计划受限、需要低并行度的场景

推荐方案：**Subagent-Driven**（与 concurrency-control + cli-enhancements 历史 7-10 task 并行 build 模式一致；任务文件互不重叠可完全并行；review_mode=thorough 利于发现跨 task 交互问题）。
