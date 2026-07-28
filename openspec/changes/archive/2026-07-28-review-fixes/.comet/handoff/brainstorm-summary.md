# Brainstorm Summary

- Change: tinydb-review-2026-07-28-fixes
- Date: 2026-07-28

## 用户确认的技术方案

### 7-task 并行 + worktree + subagent-driven-development

工作树基于 main @ `08a9ca5`，在新 worktree `tinydb-review-2026-07-28-fixes` 上并行 7 个独立 task（文件不重叠，可完全并行）：

| Task | Items | 文件 | 复杂度 |
|------|-------|------|--------|
| T1 WAL ordering | 1 | transaction.py, pager.py | HIGH |
| T2 B+tree leaf chain | 2 | btree.py | HIGH |
| T3 catalog overflow | 3 | catalog.py | MEDIUM |
| T4 DB lifecycle | 4+6 | database.py | HIGH |
| T5 REPL _cmd_read | 5 | _repl_meta.py | LOW |
| T6 codec symmetry | 7+9 | type_system.py | MEDIUM |
| T7 exception hierarchy | 8 | errors.py + call sites | LOW |

每个 task: TDD（write tests first → minimal impl → 967 baseline 维持 + 新增测试） + thorough review（≤2 rounds） + commit to feature branch + 合并到 main 后 archive。

---

## 用户决策（已确认）

| 决策 | 选项 | 备注 |
|------|------|------|
| WAL truncation | **wal_truncate_before(self.id + 1)** | 保留当前 commit WAL 记录供恢复重放 |
| Catalog 溢出策略 | **上游 _serialize_segments 真实贪心分割** + _pack_chain raise 防御 | 不在 _pack_chain 偷偷截断 |
| Database init cleanup | **静默 close + re-raise**（不引入 logging） | 简化；OS 锁必须释放 |
| _cmd_read 性能 | **list-join 拼接**（简单机械改写） | 不重构 _is_unterminated 状态机 |

---

## Agent 判断决策（已记录、无需用户确认）

| 决策 | 选定方案 | 理由 |
|------|----------|------|
| **T2** B+tree chain 修补 | "right.next = original.next" 单步法 | `_insert_into_parent` 已有 left→right 修补；扩为 right→原 rightmost 即可。无须新状态字段。 |
| **T6.1** `_IntCodec` 边界语义 | 32 位有符号 closed-open `[-2^31, 2^31-1]` | 业界 INT4（PostgreSQL）标准；encode_py + decode_bytes 均统一调 `_check_bounds()` |
| **T7** `DatabaseLocked` parent | 改 `TinydbError` → `ExecutionError`；保留 `errors.__all__` 包含；CHANGELOG 标注 non-breaking | 用户代码 `except TinydbError` 仍生效（ExecutionError → TinydbError 链）。无破坏性。 |
| **T6** DV7 编码端 | 加代码注释标记 do-not-fix；仅补齐解码端 `_check(len(text))` | 与 2026-07-21 type-codec-cleanup closeout 一致 |

---

## 关键取舍与风险

### T1: WAL ordering
- **取舍**：commit 中途异常时状态转换需要"先 re-raise 再 set ROLLED_BACK"或"先 set ROLLED_BACK 再 raise"。选后者 → 异常类型保留原始错误信息；状态不残留 ACTIVE。
- **风险**：新增 `fsync_wal()` 方法可能与现有 Pager 接口不一致。`Pager._wal_file` 已存在（从 wal.py 看），需确认是 BufferedRandom。**缓解**：先读 pager.py 头部确认接口；如未暴露则新增公开方法并测试 fsync 幂等性。

### T2: B+tree chain
- **取舍**：原 `next_leaf_id=0` 在 right 创建时；fix 为捕获 original_next 后传给 right。最简改动。
- **风险**：3+ leaf stress test 可能暴露 index_manager 没追踪 rightmost 时其他 bug。**缓解**：单测 + 3000 行随机 stress test。若溢出 → 归类已知偏差，独立 fix。

### T3: catalog
- **取舍**：把 `_serialize_segments` 改为"每个表 entry 独立测大小，超阈值即开新段"；保留 `_pack_chain` 防御性 raise。
- **风险**：超大单表（> CHAIN_BODY_SIZE，~4086 字节）仍需拆为多段。**缓解**：在 `_serialize_segments` 内做"单表跨段"扩展测试。

### T4: Database lifecycle
- **取舍**：`_is_closed` 移到 lock 内；`__init__` 末尾 try/except。
- **风险**：`_is_closed` 移到 lock 内会改变原有 `RuntimeError("Database is closed")` 的异常时序。**缓解**：保留同一异常类型 + 同一消息。新单元测试覆盖 close race。

### T5: REPL _cmd_read
- **取舍**：list-join（最简）。若 benchmark 仍慢，再切换到 text.find streaming。
- **风险**：`_is_unterminated` 内部还有 O(n) 实现 → 整体仍是 O(n²)。**缓解**：测 .read < 1s 5MB。若失败 → 改为 text.find。

### T6: codec symmetry
- **取舍**：抽 `_check_bounds(value)` 公共方法，encode_py + decode_bytes 都调；异常类型 `CodecError`。
- **风险**：现有 row_codec 依赖 codec 静默越界（bug 行为）。**缓解**：grep row_codec 调用点 + 评估是否仍 break。

### T7: exception hierarchy
- **取舍**：保留 errors.__all__；CHANGELOG 标注。
- **风险**：`except TinydbError` 已成 REPL 唯一 catching 模式 — 验证仍捕获。**缓解**：单测覆盖。

---

## 测试策略

### T1 (WAL)
```python
# 模拟 WAL write 失败 → pending_writes 不残留
# 模拟 commit 中途异常 → state 转为 ROLLED_BACK，pending_writes 清空
# 模拟 commit 成功 → 验证 wal_truncate_before(id+1) 调用
# 模拟 crash-mid-commit + recovery → 应用 idempotent 幂等性
```

### T2 (B+tree)
```python
# 随机插入 3000 行 → 3+ leaf split → range scan 命中全部
# 反向 range scan（end < start）
# 多 leaf + 多 internal split stress test
# 既有 engine-v2 测试不 regress
```

### T3 (catalog)
```python
# 单表 200+ 列 → _pack_chain 输出页数 ≥ ⌈payload/BODY_SIZE⌉
# round-trip 完整
# _pack_chain 输入超大段 → CatalogCorrupt
```

### T4 (DB lifecycle)
```python
# Threaded: A close + B execute 竞争
# mock Catalog.from_bytes raise → pager.close() 被调用
# _is_closed check 位于 lock 内
```

### T5 (REPL perf)
```python
# 5MB SQL 文件 .read < 1s
# 16MB SQL 文件 .read < 5s
# 现有 .read 测试不 regress
```

### T6 (codec)
```python
# encode_py 2^31 → CodecError
# decode_bytes 越界 → CodecError
# _VarcharCodec decode_bytes 长度超 → CodecError
# DV7 编码端保持原状（仅注释）
```

### T7 (exception)
```python
# DatabaseLocked 是 ExecutionError 子类
# from tinydb.errors import * 包含 DatabaseLocked
# REPL _run_sql except(TinydbError) 捕获 DatabaseLocked
```

---

## Spec Patch 候选

> 仅 1 项 delta spec 更新建议（不影响 high-level proposal；OpenSpec specs 目录尚无对应 capability file）

**无新增 spec**：现有 change 是 bug fixes，不引入新 capability。但实现期间若发现某 task 实际需要新 capability（如 codec 边界文档化），由 build phase 决定是否回写。

---

## 工作流计划

1. 7 task 并行（subagent-driven-development）→ worktree 内 push 7 commits
2. 整树 `pytest --no-cov` 通过（967 + 新增）
3. 覆盖率 ≥ 90%
4. merge → main (`--no-ff`)
5. cherry-pick archive move
6. verify report → archive

---

## 不在 9 项内 / 推迟项

- MED/LOW 暂不修（DEFERRED）
- 留作后续 change：
  - `pager.read_page` 缺边界检查
  - `_repl_io._is_unterminated` CC=27（complexity）
  - `_filelock` Windows NameError
  - `VALID_OUTPUT_FORMATS` 三重定义
  - `global _state` 突变
- 后续 change 可走 `tweak` 或 `hotfix` 路径（按各自范围判定）

---

## 待 Step 2 创建的 Design Doc

- 路径：`docs/superpowers/specs/2026-07-28-review-fixes-design.md`
- Frontmatter：
  ```yaml
  ---
  comet_change: tinydb-review-2026-07-28-fixes
  role: technical-design
  canonical_spec: openspec
  ---
  ```
- 内容：每 task 的精确代码骨架（patch 前 → patch 后）+ 测试代码 + 风险偏差 + 验证策略
- 7 个 section，每个 section 含：实现目标、关键代码 diff、测试代码、风险、acceptance scenarios

---
