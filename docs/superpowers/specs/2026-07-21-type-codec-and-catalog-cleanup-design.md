---
comet_change: type-codec-and-catalog-cleanup
role: technical-design
canonical_spec: openspec
status: final
---

# Design: type-codec-and-catalog-cleanup

> **关联文档**：[proposal.md](../../../../openspec/changes/type-codec-and-catalog-cleanup/proposal.md) · [design.md](../../../../openspec/changes/type-codec-and-catalog-cleanup/design.md) · [tasks.md](../../../../openspec/changes/type-codec-and-catalog-cleanup/tasks.md)
> **Brainstorm checkpoint**：见本文 §6 单轮澄清与三处探索发现
> **Date**：2026-07-21
> **承接 change 名**：`type-codec-and-catalog-cleanup`

本文档落实 `tinydb-quality-cleanup` 显式延后的 H6（type_system.py 双轨）与 H7（catalog.py 双序列化）两条 high-risk 评审项，提供实现级技术方案。

---

## 1. Context

2026-07-20 完成的 `tinydb-quality-cleanup` 修复了 83 条评审中的 73 条（全部 H1-H5+H8 + M + L），显式延后 H6 + H7。两条都属于"删除已无生产调用方的 dual-track 代码 + 同步清理测试/fixture"，但跨模块影响面广，需要独立 change。

**双轨现状**：

```
type_system.py (≈ 530 行)
├─ Codec registry (canonical)
│   ├─ _IntCodec / _TextCodec / _BoolCodec / _FloatCodec
│   ├─ _VarcharCodec / _CharCodec / _DecimalCodec
│   ├─ _DateCodec / _TimeCodec / _TimestampCodec
│   ├─ codec_for(type, params) → codec instance
│   ├─ lookup(type_name) → class
│   ├─ infer_literal_type(value) → (type, params)
│   ├─ validate_compare_types(type, params, ...)
│   └─ CodecError(TypeError, ValueError, OverflowError)
└─ Legacy module-level helpers (待删)
    ├─ encode_int / decode_int / encode_text / decode_text
    ├─ encode_bool / decode_bool / encode_float / decode_float
    ├─ py_to_db(value, type) / db_to_py(buf, type)
    └─ validate_compare(col_bytes, type, op, value)

catalog.py
├─ Column.to_dict() / Column.from_dict() (canonical v2 object format)
├─ _load_column(item) — dual-format loader (待删 list-form 分支)
└─ Catalog.create_table(name, schema, ...)
    └─ schema: tuple[Column, ...] OR list[tuple[str, str]] (待删 list-form)
```

**生产路径已全部走 codec registry**：所有 INSERT/SELECT/WHERE/index maintenance 经 `codec_for().encode_py()` / `decode_bytes()`。旧 helpers 仅余测试在用。

---

## 2. Goals / Non-Goals

**Goals：**
1. 删除 `type_system.py` 11 个旧 encode/decode/validate 函数（lines 42-167 全部）
2. 删除 `catalog.py` `_load_column` list-form 分支（lines 98-110）+ `create_table` list-form 分支（lines 159-165）
3. 删除 `tests/unit/test_type_system.py` 整文件（仅覆盖旧 API）
4. 删除 3 个回归测试：`test_catalog_loads_legacy_mvp_format`、`test_catalog_legacy_format_nullable_default_true`、`test_catalog_rejects_mixed_old_and_new_columns`
5. 删除 2 个 fixture：`tests/fixtures/legacy_mvp_schema.json`、`tests/fixtures/mixed_invalid_schema.json`
6. 修订 7 个测试文件 + 1 个 src 文件（row_codec.py docstring）以移除旧 API 引用
7. 修订 `openspec/specs/type-system-basic/spec.md` "Python to DB and DB to Python conversion" 章节指向 codec registry
8. 保持 coverage ≥ 93%、pyflakes clean、所有现有有效测试通过

**Non-Goals：**
- 不引入新 capability；只删除双轨
- 不修改 codec registry 本身（`_IntCodec`/`_TextCodec`/... 已稳定）
- 不动 `parse_int_literal`/`parse_float_literal`/`parse_text_literal`/`parse_bool_literal`（tokenizer 用，与 type_system 是平行的两套 API）
- 不动 `validate_compare_types`（现代 API，与待删的 `validate_compare` 不同名）
- 不动 `Column` 类本身；只删除其兼容加载分支
- 不为 v1 格式 `.db` 文件提供 in-process migration（迁移需用户手工执行；release notes 提示）
- 不引入 deprecation warning；一次性破坏性变更

---

## 3. Decisions

### D1. 直接删除而非 deprecation warning
两条路径都已无生产调用方，引入 deprecation warning 只会留下 dead code。直接删除 + 一次性破坏性变更。语义更清晰。

### D2. spec 改写为"canonical codec registry 入口"
旧 "Python to DB and DB to Python conversion" requirement 描述了 `py_to_db`/`db_to_py` 契约。改写为：
- **Python → DB bytes**：`codec_for(type, params).encode_py(value)`
- **DB bytes → Python**：`codec_for(type, params).decode_bytes(buf, offset)`
并保留 NaN/Inf/overflow 拒绝、type-mismatch `CodecError` 等语义约束。10 个 scenario 已写入 delta spec。

### D3. 调用方迁移策略：单点替换而非 wrapper
- `tests/unit/test_aggregation_executor.py`：唯一一处 `py_to_db(123, "TEXT")` 改写为 `codec_for("TEXT").validate(123)`（期望 `CodecError`，多继承自 `TypeError` 保留原 `pytest.raises(TypeError)` 语义）
- `tests/integration/test_catalog_overflow.py`：3 处 `cat.create_table(name, [("id", "INT")], ...)` 改写为 `cat.create_table(name, (Column(name="id", type="INT"),), ...)`
- `tests/integration/test_catalog.py`：**新发现**，2 处 list-form 改写为 Column 对象
- `src/tinydb/row_codec.py`：docstring 中 "Callers SHOULD pre-validate types via type_system.py_to_db" 改为 "Callers SHOULD pre-validate types via `codec_for(type, params).validate(value)`"

不引入 wrapper 函数，避免再次形成 dual track。

### D4. fixture 删除而非迁移
v1 fixture 的存在意义是守护 v1 加载路径。路径删除后 fixture 立即变为不可加载内容（`_load_column` 不再接受 list-form → `InvalidDatabaseFile`）。删除 fixture 是逻辑上必然的；如果未来需要 v1 加载回归测试，可在新 change 中基于专门的迁移脚本重建。

### D5. 工作区：feature branch（非 worktree）
评估 H6+H7 影响面：
- H6：type_system.py 改 1 文件 + row_codec.py docstring 1 行 + 4 测试文件 + 2 fixture
- H7：catalog.py 改 1 文件 + 3 测试文件 + 1 fixture
无 subagent 并行修改需求，且本仓库已有 `feature/<date>/<name>` 分支命名约定（acid/aggregation/engine-v2 都用）。沿用 branch 模式即可，无需 worktree 隔离。

分支名：`feature/20260721/type-codec-and-catalog-cleanup`，基于 `main@54874de`（已含 quality-cleanup merge）。

---

## 4. Architecture

### 4.1 type_system.py 变更

**删除清单**（11 个函数 + 1 个模块级常量检查）：

| 符号 | 行号 | 用途 |
|------|------|------|
| `encode_int` | 42 | 模块级 legacy helper |
| `decode_int` | 48 | 同上 |
| `encode_text` | 54 | 同上 |
| `decode_text` | 59 | 同上 |
| `encode_bool` | 68 | 同上 |
| `decode_bool` | 72 | 同上 |
| `encode_float` | 81 | 同上 |
| `decode_float` | 85 | 同上 |
| `py_to_db` | 119 | 旧 dispatcher |
| `db_to_py` | 142 | 旧 dispatcher |
| `validate_compare` | 154 | 旧 compare validator |
| `_FLOAT_FMT = ">d"` | 78 | 仅 `encode_float`/`decode_float` 用，删除 |

**保留清单**：codec registry（`_IntCodec`/`_TextCodec`/...）、`codec_for`、`lookup`、`infer_literal_type`、`validate_compare_types`、`CodecError`、`TypeCodec` Protocol、`parse_*_literal`（tokenizer 用）。

**预期净行数**：约 -85 行。

### 4.2 catalog.py 变更

**`_load_column(item)`** 简化为：

```python
def _load_column(item) -> Column:
    """Load column from v2 object format produced by Column.to_dict()."""
    if not isinstance(item, dict):
        raise InvalidDatabaseFile(
            f"unrecognized column entry: {item!r} "
            "(expected Column.to_dict() object form; legacy [name, type] arrays "
            "are no longer supported — please migrate to v2 object format)"
        )
    return Column.from_dict(item)
```

**`Catalog.create_table(name, schema, ...)`** schema 参数类型注解由 `tuple[Column, ...] | list[tuple[str, str]]` 收紧为 `tuple[Column, ...]`。原 list-form 兼容分支删除。

**`Catalog.from_bytes(raw)`** 中的"mixed legacy/new column formats not allowed" 整段删除（line 121-131）——既然没有 list-form 输入，该检查便无意义。

**`Column` 类 docstring** 移除 "Legacy catalogs that stored schema as `[[name, type], ...]` are loaded with the SQL92 defaults" 句子。

**预期净行数**：约 -20 行。

### 4.3 row_codec.py 变更

仅 docstring 1 行：

```diff
- Callers SHOULD pre-validate types via type_system.py_to_db for strict
+ Callers SHOULD pre-validate types via codec_for(type, params).validate(value)
  type checking (e.g., reject bool-as-INT, NaN/Inf FLOAT). This module
  performs mechanical encoding only.
```

---

## 5. Test & Fixture Changes

### 5.1 测试文件修改汇总

| 测试文件 | 改动类型 | 详情 |
|---------|---------|------|
| `tests/unit/test_type_system.py` | **整文件删除** | 11 旧函数测试，约 -50 个测试用例 |
| `tests/unit/test_aggregation_executor.py` | 1 处替换 | `py_to_db(123, "TEXT")` → `codec_for("TEXT").validate(123)`；`pytest.raises(TypeError)` 保留（CodecError 多继承自 TypeError） |
| `tests/integration/test_catalog_overflow.py` | 3 处替换 | `[("id", "INT"), ("name", "TEXT")]` → `(Column(name="id", type="INT"), Column(name="name", type="TEXT"))` |
| `tests/integration/test_catalog.py` | **新发现**，2 处替换 | line 38, 54 |
| `tests/integration/test_catalog_constraints.py` | 删除 3 个测试 + 其他 list-form 替换 | -3 测试用例，剩余 list-form 改 Column |

**预期测试数变化**：713 → 660 左右（删除 50+ 旧 API 测试 + 3 回归测试，新增 0）

### 5.2 fixture 删除

```
tests/fixtures/legacy_mvp_schema.json     — 守护 v1 [name, type] 数组格式
tests/fixtures/mixed_invalid_schema.json  — 守护 v1 + v2 混合错误
```

两个文件删除后，其对应的 fixture loader 若有 import 路径引用也需同步清理（grep 验证）。

### 5.3 验证策略

```
pytest tests/                          — 全绿，~660 tests
pyflakes src/tinydb/                   — clean
grep -rn "encode_int\|py_to_db\|validate_compare\b\|db_to_py" src/  — 无命中
coverage report --include="src/tinydb/*" — ≥ 93%
```

---

## 6. Brainstorm Checkpoint

**单轮澄清与三处探索发现**：

1. **`validate()` API 验证**：探索 `type_system.py` 后确认每个 `_IntCodec`/`_TextCodec`/... 类均实现 `validate(value) -> None` 方法（line 308+），D3 中 `codec_for("TEXT").validate(123)` 路径有效。

2. **新发现 `tests/integration/test_catalog.py` 2 处 list-form**：探索阶段发现 `test_catalog.py:38, 54` 也使用 list-form `create_table`，未在 open 阶段 tasks.md 中列出。已加入 tasks.md 3.8。

3. **CodecError 多继承保留 TypeError 测试**：`CodecError(TypeError, ValueError, OverflowError)` 设计意味着 `pytest.raises(TypeError)` 仍能捕获 codec 拒绝。原 `test_agg_sum_text_raises` 不需要改 `pytest.raises` 参数。

**用户确认**：2026-07-21，用户批准设计方案（含 test_catalog.py 新发现的迁移点）。

---

## 7. Migration & Risks

### 7.1 持久化数据兼容性

- **v2 格式 `.db` 文件**：完全兼容，新代码直接读
- **v1 数组格式 `.db` 文件**：`Database.open()` 会 raise `InvalidDatabaseFile`（错误信息明确说明 "`[name, type]` array form is no longer supported; please migrate to v2 object format"）
- **release notes 提示**：用户在升级前需手工执行一次性 migration（out of scope，本 change 不提供 migration script；可由用户在 tinydb v0.3 DB 中加载并 dump 后用 v0.4 重新 CREATE 重建）

### 7.2 破坏性变更清单

- `from tinydb.type_system import encode_int` 等 11 个 import → ImportError
- `from tinydb.type_system import py_to_db, db_to_py, validate_compare` → ImportError
- `Catalog.create_table(name, [("col", "TYPE")], ...)` → TypeError（annotation 收紧）
- `_load_column([name, type])` → InvalidDatabaseFile
- `Column` 类自身不变

### 7.3 风险矩阵

| 风险 | 影响 | 缓解 |
|------|------|------|
| 外部依赖 `tinydb.type_system.encode_int` 等 | 仓库内无此类外部 import（grep 验证） | release notes 提示 major bump |
| v1 .db 文件生产存在 | 从 tinydb-mvp 升级的用户 | release notes 提示手工迁移 |
| row_codec docstring 失效 | 仅 API 文档问题 | 同步修正 docstring |
| 一次性破坏性变更混淆 | 用户困惑 | release notes 明确写"BREAKING" |

### 7.4 Out of Scope

- 提供 v1→v2 migration script（`scripts/migrate_v1_to_v2.py`）— 用户可后续提 issue
- 提供 shim 模块 `type_system_compat.py` 重新导出 11 个旧函数 — 拒绝，保持删除意图
- 单独 archive tag — 走标准 Comet archive 流程

---

## 8. Spec Patch

**无新增 Spec Patch**。Spec delta 已在 open 阶段写入 `openspec/changes/type-codec-and-catalog-cleanup/specs/type-system-basic/spec.md`：
- MODIFIED "Python to DB and DB to Python conversion" requirement
- 10 个 scenario：6 个 codec_for encode/decode + 2 个 type-mismatch + 2 个 legacy ImportError

Archive 时由 `openspec archive` 把 delta 合并到 `openspec/specs/type-system-basic/spec.md`。

---

## 9. Verification Checklist

- [ ] `pytest tests/` 全绿，测试数从 713 减至 660 左右
- [ ] `pyflakes src/tinydb/` clean
- [ ] `grep -rn "encode_int\|encode_text\|encode_bool\|encode_float\|decode_int\|decode_text\|decode_bool\|decode_float\|py_to_db\|db_to_py\|validate_compare\b" src/tinydb/` 无命中
- [ ] `grep -rn "from tinydb.type_system import (encode_|decode_|py_to_db|db_to_py|validate_compare)" src/ tests/` 无命中
- [ ] `coverage run --source=src/tinydb -m pytest tests/` ≥ 93%
- [ ] 删除 3 个回归测试 + 2 个 fixture
- [ ] 7 个测试文件 + row_codec.py 修改完成
- [ ] verify report 写入 `docs/superpowers/reports/2026-07-21-type-codec-and-catalog-cleanup-verify.md`
- [ ] archive：合并 delta spec → main spec，git mv 到 `archive/2026-07-21-type-codec-and-catalog-cleanup/`