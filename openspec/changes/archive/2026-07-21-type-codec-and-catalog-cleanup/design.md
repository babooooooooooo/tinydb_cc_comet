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