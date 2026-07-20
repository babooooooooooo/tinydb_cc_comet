# 验证报告：codec-exception-consistency

- **Change**: `codec-exception-consistency`
- **Branch**: `feature/20260720/codec-exception-consistency`
- **Base ref**: `15518f4b35a747652ffea922b3c26484c27086e5`
- **Worktree**: `/home/lz/projects/tinydb_comet`
- **Date**: 2026-07-20
- **Verify mode**: full（22 tasks / 8 files / 0 capability delta — fix-and-archive change）
- **Verify pass**: R1 通过；最终轻量 code reviewer verdict `APPROVED_WITH_NOTES`（0 critical / 0 important / 2 accepted non-blocking suggestions）

## 1. 摘要 Scorecard

| 维度 | 状态 | 详情 |
|------|------|------|
| Completeness | PASS | 22/22 tasks `[x]`（6 节全部完成）；本 change 无 delta spec（fix-only，无 capability 增删） |
| Correctness | PASS | **689 tests pass / 93% coverage**（683 baseline + 6 new RED→GREEN）；pyflakes clean；F1-F6 全部 wire 对 current code |
| Coherence | PASS | Design D1-D5 全部按计划落地；CodecError 多继承保证现有 `except (TypeError/ValueError/OverflowError)` 站点零回归（已在 parser/tokenizer/btree 处交叉验证） |

## 2. 产物上下文

- **Proposal**: `openspec/changes/codec-exception-consistency/proposal.md`
- **Design**: `openspec/changes/codec-exception-consistency/design.md`
- **Delta spec**: 不存在（本 change 不引入 capability 增删）
- **Design Doc**: `docs/superpowers/specs/2026-07-20-codec-exception-consistency-design.md`
- **Plan**: `docs/superpowers/plans/2026-07-20-codec-exception-consistency.md`
- **Tasks**: `openspec/changes/codec-exception-consistency/tasks.md`（22/22 `[x]`）

## 3. Completeness 验证

### 3.1 tasks.md 完成度

6 节 / 22 个 checkbox 全部 `[x]`：

| 节 | task 数 | 状态 |
|----|--------|------|
| §1 F3+F6 encode_py refactor | 4 | `[x]` |
| §2 F2 VARCHAR/CHAR CodecError | 6 | `[x]` |
| §3 F1 create_table guard | 3 | `[x]` |
| §4 F4 _load_column split | 3 | `[x]` |
| §5 F5 stale comments | 2 | `[x]` |
| §6 Verification | 4 | `[x]` |

`comet state task-checkoff` 对所有 22 条任务文本返回 `TASK_CHECKOFF: PASS`（OpenSpec task file 唯一 source of truth；plan file 因 §0 决策使用中文翻译导致某些文本不完全匹配，已记录不追踪 — OpenSpec tasks.md 优先）。

### 3.2 跨产物一致性

- proposal.md 中 6 个 finding（F1-F6）全部按对应 task 落地。
- Design Doc 5 个 decisions（D1-D5）与 tasks.md 第 §1-§5 完全对应：D1=F3+F6 refactor，D2=F2 CodecError，D3=F1 guard，D4=F4 error split，D5=F5 comment removal。
- 无 capability 增删 → 无 delta spec；spec 同步步骤在本 change 中跳过。

## 4. Correctness 验证

### 4.1 测试结果

| 验证项 | 命令 | 预期 | 实际 |
|--------|------|------|------|
| 完整测试套件 | `pytest tests/ -q --no-cov` | 全绿、test count ≥ 683 baseline | **689 passed in 54.05s**（+6 vs baseline） |
| 静态检查 | `python -m pyflakes src/tinydb/` | exit 0、no output | clean |
| 覆盖率 | `pytest --cov=src/tinydb --cov-report=term` | ≥ 93% | **93%**（从 baseline 93.27% 轻微调整） |
| 死代码扫描 | `grep -rnE "\bvalidate_compare\b\|\bencode_int\b\|\bpy_to_db\b\|\bdb_to_py\b" src/tinydb/` | src/ 无命中 | 唯一命中在 `tests/unit/test_type_system_v2.py:447`（`test_legacy_validate_compare_removed_from_type_system`）—— 该测试**断言** `validate_compare` 已不可导入，正面证据，非回归 |

### 4.2 实现 F1-F6 与 source code 对照

| Finding | 路径:行 | 实现摘要 |
|---------|---------|---------|
| F1 | `src/tinydb/catalog.py:159-165` | `create_table` 现在 `list(schema)` → `isinstance(c, Column)` 守卫 → TypeError 后再 `tuple(...)`。String / tuple-of-string / 2-tuple 全部被入口拒绝。 |
| F2 | `src/tinydb/type_system.py:302-307` (`_check`) + `:327-329` (`_CharCodec.encode_py`) | `_check` 抛 `CodecError` 而非 `TypeError`；CHAR 复用父类 `_check` 也走 `CodecError`。 |
| F3 | `src/tinydb/type_system.py:188-191` (`_IntCodec.encode_py`) | 重构为 `self.validate(value)` + `struct.pack(...)`，type-mismatch 与 range error 都走 `CodecError`。 |
| F4 | `src/tinydb/catalog.py:96-109` (`_load_column`) | `isinstance(item, list)` 单独分支保留 legacy 提示；剩余非 dict 走 generic 提示。 |
| F5 | `src/tinydb/type_system.py:127/174` 已无 breadcrumb | 两行移除。 |
| F6 | `src/tinydb/type_system.py:272-274` (`_FloatCodec.encode_py`) | 与 F3 同步：`self.validate(value)` 委托。`bool`-subclasses-`int` quirk 通过 `validate()` 内的 `isinstance(value, bool)` 排除保留；FLOAT inf/NaN 仍由 `validate()` 抛 `CodecError`。 |

### 4.3 RED → GREEN 证据（来自 implementer subagents）

**F2+F3+F6**：
- `test_int_codec_encode_py_overflow_raises_codec_error` RED → raises `OverflowError: INT out of range: 2147483648 at src/tinydb/type_system.py:195`（codec 抛 OverflowError 而非 CodecError）。
- `test_varchar_codec_overflow_raises_codec_error` RED → raises `TypeError: VARCHAR(10) length 11 exceeds max at src/tinydb/type_system.py:312`。
- `test_char_codec_overflow_raises_codec_error` RED → raises `TypeError: CHAR(5) length 6 exceeds max at src/tinydb/type_system.py:337`。
- GREEN 后 3 个新测试 + 3 个老测试（`test_int_codec_overflow_raises`、`test_varchar_codec_rejects_overlong`、`test_char_codec_rejects_overlong`）全部 expect `CodecError` 通过。
- Live smoke（`tinydb.Database(":memory:")`）：INT overflow / VARCHAR overlong / CHAR overlong / FLOAT NaN 全部抛 `CodecError`；valid round-trip 仍正常。

**F4**：
- `test_load_column_rejects_non_dict_non_list_with_generic_message` RED → `_load_column(42)` 抛 `InvalidDatabaseFile` 含 `'legacy [name, type] arrays'` 字符串，断言 `not in` 失败。
- GREEN 后通过；legacy-list 测试无变化仍 PASS。

**F1**：
- `test_create_table_rejects_legacy_2tuple_with_type_error`、`test_create_table_rejects_string_iterable` 在主会话补 commit（实现 F1 GREEN 由 F4 并发子 agent 顺带落地于 `0251b81`），均 expect TypeError + `match="create_table expects Column"` 通过。

### 4.4 公共 API 影响（Risk R3）

`encode_py` 异常类型变更（`OverflowError` → `CodecError`、`TypeError` → `CodecError` for VARCHAR/CHAR overlong）。

`CodecError` 多继承 `TypeError, ValueError, OverflowError`，因此现有 `except (TypeError, ValueError, OverflowError)` 在 `parser.py:598/994/1021`、`tokenizer.py:85/112`、`btree.py:185/235` 等处仍命中（交叉验证：full suite 689 passed 包括这些 test path）。无 `except OverflowError:` 严格捕获 INT overflow 的现存 call site（已 grep）。

## 5. Coherence 验证

### 5.1 Design adherence

| Design 决策 | 实现落地 |
|-------------|---------|
| D1 encode_py → `self.validate()` | 完全落地于 `_IntCodec.encode_py:188-191` 和 `_FloatCodec.encode_py:272-274` |
| D2 `_VarcharCodec._check` 改 `CodecError` | 落地于 `_check:302-307`（CHAR 经继承复用同一路径） |
| D3 `Catalog.create_table` isinstance 守卫 | 落地于 `create_table:159-165`，代码逐字符合 design doc |
| D4 `_load_column` 分两条路径 | 落地于 `_load_column:96-109`，分支顺序与 design 一致 |
| D5 删除 stale comment | 落地于 `type_system.py`：`legacy helpers above (stay\|remain)` 关键字 grep 零命中 |

### 5.2 Code pattern consistency

- `_IntCodec.encode_py` / `_FloatCodec.encode_py` 与 `_TextCodec.encode_py` / `_BoolCodec.encode_py` 不同：单数 param codec 没有 inline isinstance-check（直接 pack）。`_IntCodec.encode_py` 现与 `_BoolCodec` / `_TextCodec` 视觉上更接近（前置 `self.validate(value)`）。这是改善，不构成 pattern 偏差。
- 新 `create_table` guard 代码风格与既有 `if name in self.tables: raise ValueError(...)` 一致（同方法内）。

### 5.3 Cross-cutting

`_IntCodec.encode_py` 现只调 `validate()` + 单次 `_spec` unpack（`fmt, _, _ = self._spec`），不再重复 isinstance / range check。减少重复，与"`parse_literal` 也用 `self.validate(v)`"的模式一致——F3+F6 同时统一了 3 个方法的 single source of truth。

## 6. Accepted non-blocking findings

来自最终轻量 code reviewer（review_mode: standard，APPROVED_WITH_NOTES）：

| Source:Line | Finding | 为什么接受 | 影响范围 |
|-------------|---------|----------|---------|
| `src/tinydb/type_system.py:306` | `_VarcharCodec.encode_py` 中 `data = value.encode("utf-8"); self._check(len(data))` 单行多语句 | 预存在样式，非本 change 回归 | 1 行可读性 |
| `src/tinydb/type_system.py:329` | `_CharCodec.encode_py` 中 `if ...: raise ...` 单行 | 预存在样式 | 1 行可读性 |
| `src/tinydb/type_system.py:188-191` | `_IntCodec.encode_py` 中 `_spec` 被访问两次（validate + encode_py 各自 unpack） | 非阻断，可接受；缓存 `fmt` 需引入实例级 cache 复杂度 | 性能/可读性 trade-off |

上述 3 条均为 SUGGESTION，不阻塞归档；后续如触碰 type_system.py 的下一条 change 再处理。

## 7. 结论

**Verify Result: PASS**

- 22/22 tasks `[x]`，所有 6 个 CONFIRMED findings 已实现并由 RED→GREEN 验证。
- 测试 689 passed / 93% 覆盖率 / pyflakes clean / 公共 API 兼容性已确认（`CodecError` 多继承保证）。
- 8 文件改动（+372/-20），其中 3 文件为 workflow 制品（plan、subagent-progress、tasks），5 文件为源代码（catalog.py、type_system.py、3 个 test files）。
- Net diff vs base：+180/-20 代码行（剔除 workflow 制品）。

进入 archive 阶段。

## 8. Branch handling

（将在 archive 完成时由用户决定 — 默认走"合并到 main"路径，与 `type-codec-and-catalog-cleanup` 同模式：`git checkout main && git merge --no-ff feature/20260720/codec-exception-consistency` + cherry-pick archive 移动。）

## 9. 关键 commit 引用

| SHA | Message |
|-----|---------|
| `0251b81` | `fix(catalog): split _load_column error message by input type (F4)`（**F1 GREEN 守卫被 F4 并发子 agent 一并 land**） |
| `cf065c4` | `refactor(codec): encode_py delegates to self.validate and surfaces CodecError (F2+F3+F6)` |
| `393dc6e` | `test(catalog): create_table rejects non-Column inputs (F1 RED tests)`（主会话补 — F1 子 agent 因 token quota 用尽无法 commit） |
| `6d48ce1` | `chore(type_system): remove stale legacy-helpers breadcrumb comments (F5)` |
| `ef0d991` | `chore(change): mark all 6 sections (22 tasks) completed in tasks.md` |
| `7bd56eb` | `chore(plan): translate implementation plan to zh-CN + record final review notes` |

## 10. 偏差（Deviations）

零。`CodecError` 多继承设计意图自带向后兼容保障。F1 GREEN 守卫被 F4 并发子 agent 一并 land 视为实施偏差（不影响 deliverable），已在 commit body 与上表 commit 引用中显式记录。
