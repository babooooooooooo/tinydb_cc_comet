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