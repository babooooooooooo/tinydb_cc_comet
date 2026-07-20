# Comet Design Handoff

- Change: type-codec-and-catalog-cleanup
- Phase: design
- Mode: compact
- Context hash: 2a9d822bd4faf48cc21acb46393851af5e25e015e63a2e75303a66353bfb2e93

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/type-codec-and-catalog-cleanup/proposal.md

- Source: openspec/changes/type-codec-and-catalog-cleanup/proposal.md
- Lines: 1-48
- SHA256: 169def70cd6a66d2477c26c01486137433f911014da4022abe4b15209dde4b77

```md
# type-codec-and-catalog-cleanup

## Why

2026-07-20 完成的 `tinydb-quality-cleanup` change 修复了 83 条评审中的 73 条（包括全部 H1-H5+H8 + M + L），但显式延后了 H6（type_system.py 双轨）和 H7（catalog.py 双序列化）。这两条都属于"删除已无调用方的 dual-track 代码"，但因跨模块影响面广、测试与 fixture 需同步调整，需要独立的 change。

代码现状：
- `type_system.py` 同时存在 11 个旧 `encode_int`/`decode_int`/`py_to_db`/`validate_compare` 等函数和 codec registry（`codec_for` + `_IntCodec` 等）；新代码全部走 registry，旧函数仅余测试在用。
- `catalog.py` 的 `Column.to_dict()` 已持久化 v2 格式（带 nullable/unique/primary_key/type_params），但 `_load_column` 和 `Catalog.create_table` 仍接受 v1 数组 `[name, type]` 形式，且存在 `legacy_mvp_schema.json` fixture 与 3 个回归测试守护旧路径。

## What Changes

- **BREAKING**: 删除 `type_system.py` 中的 11 个旧 encode/decode/validate 函数：
  `encode_int`, `decode_int`, `encode_text`, `decode_text`, `encode_bool`, `decode_bool`,
  `encode_float`, `decode_float`, `py_to_db`, `db_to_py`, `validate_compare`
  调用方已全部迁移到 `codec_for().encode_py()` / `codec_for().decode_bytes()`，
  遗留的 `test_type_system.py` 旧函数测试用例随之一并删除。
- **BREAKING**: 删除 `catalog.py` 中 v1 数组 `[name, type]` 形式的兼容路径：
  `_load_column` 不再接受 list-form；`Catalog.create_table` 不再接受 `list[tuple[str, str]]`；
  所有调用方（`tests/integration/test_catalog_overflow.py` 等）改用 `Column(...)` 对象。
- 删除 2 个 fixture 文件：`tests/fixtures/legacy_mvp_schema.json`、`tests/fixtures/mixed_invalid_schema.json`。
- 删除 3 个回归测试：
  `test_catalog_loads_legacy_mvp_format`、`test_catalog_legacy_format_nullable_default_true`、`test_catalog_rejects_mixed_old_and_new_columns`。
- 修订 `tests/unit/test_aggregation_executor.py` 中调用 `py_to_db` 的 1 个测试用例（改用 `codec_for`）。
- 修订 `src/tinydb/row_codec.py` 文档中指向 `py_to_db` 的引用（改为 `codec_for(...)`）。
- 修订 `openspec/specs/type-system-basic/spec.md` 的 "Python to DB and DB to Python conversion" 章节，反映 conversion 路径已统一到 codec registry。

## Capabilities

### New Capabilities

（无新增 capability；本 change 仅删除双轨，不引入新行为。）

### Modified Capabilities

- `type-system-basic`：原 "Python to DB and DB to Python conversion" requirement 描述了 `py_to_db`/`db_to_py` 的契约。删除这两个函数后，conversion 的 canonical 入口是 `codec_for(type, params).encode_py()` 与 `codec_for(type, params).decode_bytes(buf, offset)`，requirement 改写为指向 codec registry。

## Impact

- 源码：`src/tinydb/type_system.py`（-85 行）、`src/tinydb/catalog.py`（-20 行）、`src/tinydb/row_codec.py`（docstring 修订）。
- 测试：删除约 4 个测试用例，修改 1 个；fixture 删除 2 个文件。
- Spec：`openspec/specs/type-system-basic/spec.md` 的 1 个 requirement 改写。
- 调用方迁移点：
  - `row_codec.py`（已仅在 docstring 中提及）
  - `tests/unit/test_aggregation_executor.py`（1 处）
  - `tests/integration/test_catalog_overflow.py`（多处 `[("id", "INT")]` → `Column(...)`）
  - `tests/unit/test_type_system.py`（整文件覆盖旧 API，删除）
- 持久化数据兼容性：删除 v1 加载路径后，已用 v2 格式写入的 `.db` 文件不受影响（向后兼容对象）；已用 v1 数组格式写入的 `.db` 文件需在升级前手工迁移。
- 验证：`pytest tests/` 通过；`pyflakes src/tinydb/` clean；`coverage ≥ 93%`（与清理前持平）。
```

## openspec/changes/type-codec-and-catalog-cleanup/design.md

- Source: openspec/changes/type-codec-and-catalog-cleanup/design.md
- Lines: 1-73
- SHA256: 837523476967147f101880f717e9a064aa928efd641e7a8fc260e10b1d98fa7d

```md
# Design: type-codec-and-catalog-cleanup

## Context

2026-07-20 完成的 `tinydb-quality-cleanup` 完成了 73 / 83 项评审修复，显式延后 H6（type_system 双轨）与 H7（catalog 双序列化）。两条都属于"删除已无生产调用方的 legacy 代码 + 同步清理测试/fixture"。

当前 `type_system.py` 中两条并行 type 编码路径：
- **Codec registry**（canonical）：`codec_for(type, params).encode_py(value)` / `decode_bytes(buf, offset)`，所有 `INSERT`/`SELECT`/`WHERE`/index maintenance 经此路径。
- **Legacy helpers**：`encode_int/decode_int/encode_text/decode_text/encode_bool/decode_bool/encode_float/decode_float/py_to_db/db_to_py/validate_compare`，生产代码已无调用方；仅 `tests/unit/test_type_system.py`、`tests/unit/test_aggregation_executor.py` 还在用。

`catalog.py` 中两条并行 schema 序列化路径：
- **v2 对象格式**（canonical）：`Column.to_dict()` → `{name, type, type_params, nullable, unique, primary_key}`。
- **v1 数组格式**（legacy）：`[name, type]` 二元数组，缺省 `nullable=True, unique=False, primary_key=False`。`_load_column` 与 `Catalog.create_table` 仍接受；fixture `legacy_mvp_schema.json` 与 3 个回归测试守护旧路径。

## Goals / Non-Goals

**Goals:**
1. 删除 `type_system.py` 11 个旧函数及其测试。
2. 删除 `catalog.py` v1 数组格式的加载/创建兼容路径；所有调用方改用 `Column` 对象。
3. 删除 2 个 fixture + 3 个回归测试。
4. 修订 `type-system-basic` spec 的 "Python to DB and DB to Python conversion" 章节，反映 conversion 入口已统一到 codec registry。
5. 保持 713-N 测试通过（预期 N=4），coverage ≥ 93%。

**Non-Goals:**
- 不引入新 capability；只删除双轨。
- 不修改 codec registry 本身（`_IntCodec`/`_TextCodec`/... 已稳定）。
- 不动 `parse_int_literal`/`parse_float_literal`/`parse_text_literal`/`parse_bool_literal`（tokenizer 用）。
- 不动 `validate_compare_types`（现代 API，与 `validate_compare` 不同名）。
- 不动 `Column` 类本身；只删除其兼容加载分支。
- 不为 v1 格式 `.db` 文件提供 in-process migration（迁移需用户手工执行）；在 release notes 中提示。

## Decisions

### D1. 直接删除而非 deprecation warning
两条路径都已无生产调用方，引入 deprecation warning 只会留下 dead code。直接删除 + 一次性破坏性变更。语义更清晰。

### D2. spec 改写为"canonical codec registry 入口"
旧 "Python to DB and DB to Python conversion" requirement 描述了 `py_to_db`/`db_to_py` 契约。改写为：
- **Python → DB bytes**：`codec_for(type, params).encode_py(value)`
- **DB bytes → Python**：`codec_for(type, params).decode_bytes(buf, offset)`
并保留 NaN/Inf/overflow 拒绝、type-mismatch `CodecError` 等语义约束。

### D3. 调用方迁移策略：单点替换而非 wrapper
- `tests/unit/test_aggregation_executor.py`：唯一一处 `py_to_db(123, "TEXT")` 改写为 `codec_for("TEXT").validate(123)`。
- `tests/integration/test_catalog_overflow.py`：`cat.create_table(name, [("id", "INT")], ...)` 改写为 `cat.create_table(name, (Column(name="id", type="INT"),), ...)`。
- `src/tinydb/row_codec.py`：docstring 中 "Callers SHOULD pre-validate types via type_system.py_to_db" 改为 "Callers SHOULD pre-validate types via `codec_for(type, params).validate(value)`"。

不引入 wrapper 函数，避免再次形成 dual track。

### D4. fixture 删除而非迁移
v1 fixture 的存在意义是守护 v1 加载路径。路径删除后 fixture 立即变为不可加载内容（`_load_column` 不再接受 list-form → `InvalidDatabaseFile`）。删除 fixture 是逻辑上必然的；如果未来需要 v1 加载回归测试，可在新 change 中基于专门的迁移脚本重建。

### D5. 工作区：feature branch（非 worktree）
评估 H6+H7 影响面：
- H6：type_system.py 改 1 文件 + row_codec.py docstring 1 行 + 2 测试文件 + 1 fixture
- H7：catalog.py 改 1 文件 + 2 测试文件 + 1 fixture
无 subagent 并行修改需求，且本仓库已有 `feature/<date>/<name>` 分支命名约定（acid/aggregation/engine-v2 都用）。沿用 branch 模式即可，无需 worktree 隔离。

## Risks / Trade-offs

- **R1**：v1 格式 `.db` 文件在生产环境存在的可能性。若用户从 `tinydb-mvp` 升级而来，其 `.db` 文件 schema 字段为 `[[name, type], ...]` 数组形式，本 change 后 `Database.open()` 会 raise `InvalidDatabaseFile`。
  - **缓解**：release notes 显式提示"自 v0.4 起需手工迁移"；提供一次性 migration script `scripts/migrate_v1_to_v2.py`（out of scope，本 change 不实现）。
- **R2**：`tests/unit/test_type_system.py` 整文件覆盖 11 个旧函数，可能导致大量测试用例删除（预估 -50 个测试）。但这些测试验证的是旧 API 行为，删除旧 API 后其保护对象消失，属于正确删除。
- **R3**：row_codec.py 的 docstring 提示"Callers SHOULD pre-validate types" 是防御性建议，不强制任何代码路径。若 docstring 失效也不会触发 bug，但属于 API 文档准确性问题，应同步修正。
- **R4**：`_load_column` 删除 list-form 分支后，column-from-dict 路径若未来扩展新字段（如 `default` value），需要修改 `Column.from_dict`。这不是新风险，但需在 spec 中保留扩展点说明。

## Open Questions

- 是否需要在 `errors.py` 中新增专门的 `LegacySchemaFormatError` 异常类（取代通用的 `InvalidDatabaseFile`）？—— 当前决定：复用 `InvalidDatabaseFile`，错误信息明确说明"`[name, type]` array form is no longer supported; please migrate to v2 object format"。

## Migration Notes

若有外部项目依赖 `tinydb.type_system.encode_int` 等函数，破坏性变更在 major 版本升级时生效。仓库内：
- `setup.py` / `pyproject.toml` 中的版本号应在 archive 前 bump major（具体值待 archive 阶段决定）。
```

## openspec/changes/type-codec-and-catalog-cleanup/tasks.md

- Source: openspec/changes/type-codec-and-catalog-cleanup/tasks.md
- Lines: 1-36
- SHA256: a7a9150c76890c0dd7b2364d146bab0fb79c0e91ce5af94424b5e3bea5d1eb7c

```md
# Tasks: type-codec-and-catalog-cleanup

## 1. H6: type_system.py 双轨清理

- [ ] 1.1 删除 11 个旧函数：`encode_int`, `decode_int`, `encode_text`, `decode_text`, `encode_bool`, `decode_bool`, `encode_float`, `decode_float`, `py_to_db`, `db_to_py`, `validate_compare`
- [ ] 1.2 保留 codec registry（`_IntCodec`/`_TextCodec`/...）+ `codec_for`/`lookup`/`infer_literal_type`/`validate_compare_types`/`CodecError`
- [ ] 1.3 修订 `src/tinydb/row_codec.py` docstring：把 "Callers SHOULD pre-validate types via type_system.py_to_db" 改为指向 `codec_for(type, params).validate(value)`

## 2. H7: catalog.py 双序列化清理

- [ ] 2.1 删除 `_load_column` 的 list-form 分支（catalog.py）
- [ ] 2.2 删除 `Catalog.create_table` 对 `list[tuple[str, str]]` 形式的兼容分支；只接受 `tuple[Column, ...]`
- [ ] 2.3 修订 `Column` docstring：移除 "Legacy catalogs that stored schema as `[[name, type], ...]` are loaded with the SQL92 defaults" 的兼容说明

## 3. 测试与 fixture 同步

- [ ] 3.1 删除 `tests/unit/test_type_system.py`（整文件覆盖旧 API，删除）
- [ ] 3.2 修订 `tests/unit/test_aggregation_executor.py`：将 `py_to_db(123, "TEXT")` 改写为 `codec_for("TEXT").validate(123)`（期望 `CodecError`）
- [ ] 3.3 删除 `tests/integration/test_catalog_constraints.py` 中的 3 个回归测试：`test_catalog_loads_legacy_mvp_format`、`test_catalog_legacy_format_nullable_default_true`、`test_catalog_rejects_mixed_old_and_new_columns`
- [ ] 3.4 修订 `tests/integration/test_catalog_overflow.py`：所有 `cat.create_table(name, [("id", "INT")], ...)` 改写为 `cat.create_table(name, (Column(name="id", type="INT"),), ...)`
- [ ] 3.5 修订 `tests/integration/test_catalog_constraints.py` 中其他使用 `[("col", "TYPE")]` 的地方为 `Column(...)`
- [ ] 3.6 修订 `tests/integration/test_catalog.py` 的 2 处 list-form `[("x", "INT")]` 为 `Column(...)`（新发现，line 38 + line 54）
- [ ] 3.7 删除 fixture `tests/fixtures/legacy_mvp_schema.json`
- [ ] 3.8 删除 fixture `tests/fixtures/mixed_invalid_schema.json`

## 4. Spec 同步

- [ ] 4.1 archive 时合并 `openspec/changes/type-codec-and-catalog-cleanup/specs/type-system-basic/spec.md` → `openspec/specs/type-system-basic/spec.md`（替换 "Python to DB and DB to Python conversion" requirement）

## 5. 验证

- [ ] 5.1 `pytest tests/` 全绿；测试数从 713 减至 709 左右
- [ ] 5.2 `pyflakes src/tinydb/` clean
- [ ] 5.3 `grep -rn "encode_int\|py_to_db\|validate_compare\b\|db_to_py" src/` 无命中
- [ ] 5.4 coverage ≥ 93%
- [ ] 5.5 在 `openspec/changes/type-codec-and-catalog-cleanup/` 创建 verify report `docs/superpowers/reports/2026-07-21-type-codec-and-catalog-cleanup-verify.md`
- [ ] 5.6 archive：合并 delta spec → main spec，git mv 到 `archive/2026-07-21-type-codec-and-catalog-cleanup/`
```

## openspec/changes/type-codec-and-catalog-cleanup/specs/type-system-basic/spec.md

- Source: openspec/changes/type-codec-and-catalog-cleanup/specs/type-system-basic/spec.md
- Lines: 1-46
- SHA256: 7ba382dfd6c77f4dcc0d9036e2f5e775f05c4637a442a1ea36d7fb9c7034149d

```md
# type-system-basic delta

## MODIFIED Requirements

### Requirement: Python to DB and DB to Python conversion

The system SHALL provide explicit conversion between Python native objects and DB-typed values via the codec registry. The legacy `py_to_db`/`db_to_py` module-level helpers are removed; canonical entry points are `codec_for(type, params).encode_py(value)` for Python → DB bytes and `codec_for(type, params).decode_bytes(buf, offset)` for DB bytes → Python.

#### Scenario: Convert Python int to INT via codec registry
- **WHEN** converting Python `42` to DB type for an INT column via `codec_for("INT").encode_py(42)`
- **THEN** the function SHALL return bytes `b'\x00\x00\x00\x2a'` (8-byte big-endian)

#### Scenario: Convert Python str to TEXT via codec registry
- **WHEN** converting Python `'alice'` to DB type for a TEXT column via `codec_for("TEXT").encode_py('alice')`
- **THEN** the function SHALL return bytes `b'\x00\x05alice'` (length-prefixed UTF-8)

#### Scenario: Convert Python float to FLOAT via codec registry
- **WHEN** converting Python `2.5` to DB type for a FLOAT column via `codec_for("FLOAT").encode_py(2.5)`
- **THEN** the function SHALL return bytes `struct.pack('>f', 2.5)`

#### Scenario: Convert Python float NaN rejected via codec registry
- **WHEN** converting Python `float('nan')` to DB type for a FLOAT column via `codec_for("FLOAT").encode_py(float('nan'))`
- **THEN** the function SHALL raise `CodecError` with message containing `"NaN not allowed"`

#### Scenario: Convert Python bool to BOOL via codec registry
- **WHEN** converting Python `True` to DB type for a BOOL column via `codec_for("BOOL").encode_py(True)`
- **THEN** the function SHALL return `b'\x01'`

#### Scenario: Convert Python float to INT rejected via codec registry
- **WHEN** converting Python `2.5` to DB type for an INT column via `codec_for("INT").encode_py(2.5)`
- **THEN** the function SHALL raise `CodecError` with message indicating type mismatch

#### Scenario: Parametric type (VARCHAR) conversion via codec registry
- **WHEN** converting Python `'hello'` (5 chars) to DB type for a `VARCHAR(10)` column via `codec_for("VARCHAR", (10,)).encode_py('hello')`
- **THEN** the function SHALL return 2-byte length prefix `b'\x00\x05'` followed by UTF-8 bytes `b'hello'`

#### Scenario: Parametric type VARCHAR length exceeds limit rejected
- **WHEN** converting Python `'x' * 20` to DB type for a `VARCHAR(10)` column via `codec_for("VARCHAR", (10,)).encode_py('x' * 20)`
- **THEN** the function SHALL raise `CodecError` with message containing `"length"` and `"exceeds"`

#### Scenario: Legacy py_to_db helper removed from public API
- **WHEN** any module attempts to import `py_to_db` from `tinydb.type_system`
- **THEN** the import SHALL raise `ImportError` (function no longer exported)

#### Scenario: Legacy db_to_py helper removed from public API
- **WHEN** any module attempts to import `db_to_py` from `tinydb.type_system`
- **THEN** the import SHALL raise `ImportError` (function no longer exported)
```
