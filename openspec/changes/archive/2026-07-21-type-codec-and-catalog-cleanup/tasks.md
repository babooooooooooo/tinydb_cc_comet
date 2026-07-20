# Tasks: type-codec-and-catalog-cleanup

## 1. H6: type_system.py 双轨清理

- [x] 1.1 删除 11 个旧函数：`encode_int`, `decode_int`, `encode_text`, `decode_text`, `encode_bool`, `decode_bool`, `encode_float`, `decode_float`, `py_to_db`, `db_to_py`, `validate_compare`
- [x] 1.2 保留 codec registry（`_IntCodec`/`_TextCodec`/...）+ `codec_for`/`lookup`/`infer_literal_type`/`validate_compare_types`/`CodecError`
- [x] 1.3 修订 `src/tinydb/row_codec.py` docstring：把 "Callers SHOULD pre-validate types via type_system.py_to_db" 改为指向 `codec_for(type, params).validate(value)`

## 2. H7: catalog.py 双序列化清理

- [x] 2.1 删除 `_load_column` 的 list-form 分支（catalog.py）
- [x] 2.2 删除 `Catalog.create_table` 对 `list[tuple[str, str]]` 形式的兼容分支；只接受 `tuple[Column, ...]`
- [x] 2.3 修订 `Column` docstring：移除 "Legacy catalogs that stored schema as `[[name, type], ...]` are loaded with the SQL92 defaults" 的兼容说明

## 3. 测试与 fixture 同步

- [x] 3.1 删除 `tests/unit/test_type_system.py`（整文件覆盖旧 API，删除）
- [x] 3.2 修订 `tests/unit/test_aggregation_executor.py`：将 `py_to_db(123, "TEXT")` 改写为 `codec_for("TEXT").validate(123)`（期望 `CodecError`）
- [x] 3.3 删除 `tests/integration/test_catalog_constraints.py` 中的 3 个回归测试：`test_catalog_loads_legacy_mvp_format`、`test_catalog_legacy_format_nullable_default_true`、`test_catalog_rejects_mixed_old_and_new_columns`
- [x] 3.4 修订 `tests/integration/test_catalog_overflow.py`：所有 `cat.create_table(name, [("id", "INT")], ...)` 改写为 `cat.create_table(name, (Column(name="id", type="INT"),), ...)`
- [x] 3.5 修订 `tests/integration/test_catalog_constraints.py` 中其他使用 `[("col", "TYPE")]` 的地方为 `Column(...)`
- [x] 3.6 修订 `tests/integration/test_catalog.py` 的 2 处 list-form `[("x", "INT")]` 为 `Column(...)`（新发现，line 38 + line 54）
- [x] 3.7 删除 fixture `tests/fixtures/legacy_mvp_schema.json`
- [x] 3.8 删除 fixture `tests/fixtures/mixed_invalid_schema.json`
- [x] 3.9 删除 `tests/unit/test_catalog_type_params.py::test_column_legacy_2tuple_format_still_works`（H7 删除 list-form 后该测试失效）
- [x] 3.10 修订 `tests/unit/test_catalog_type_params.py` 模块 docstring：移除"Old 2-tuple [name, type] format still loads via _load_column"
- [x] 3.11 修订 `tests/unit/test_validate_compare_types.py` 模块 docstring：移除 legacy `validate_compare` 引用
- [x] 3.12 修订 `tests/unit/test_engine_v1_executor.py` 模块 docstring：`py_to_db` 引用替换为 codec registry `validate_compare_types`

## 4. Spec 同步

- [x] 4.1 archive 时合并 `openspec/changes/type-codec-and-catalog-cleanup/specs/type-system-basic/spec.md` → `openspec/specs/type-system-basic/spec.md`（替换 "Python to DB and DB to Python conversion" requirement）— *archive 阶段执行*

## 5. 验证

- [x] 5.1 `pytest tests/` 全绿（678 tests pass）
- [x] 5.2 `pyflakes src/tinydb/` clean
- [x] 5.3 `grep -rn "encode_int\|py_to_db\|validate_compare\b\|db_to_py" src/` 无命中
- [x] 5.4 coverage ≥ 93%（实际 93.23%）
- [x] 5.5 在 `docs/superpowers/reports/` 创建 verify report — *verify 阶段执行*
- [x] 5.6 archive：合并 delta spec → main spec，git mv 到 `archive/2026-07-21-type-codec-and-catalog-cleanup/` — *archive 阶段执行*