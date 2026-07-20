# Tasks: codec-exception-consistency

## 1. F3+F6: encode_py → self.validate() refactor

- [x] 1.1 RED test `test_int_codec_encode_py_overflow_raises_codec_error`：assert `pytest.raises(CodecError, match="INT out of range")` for `codec_for("INT").encode_py(2**33)` （现有 `test_int_codec_overflow_raises` 期望 `OverflowError`）
- [x] 1.2 GREEN fix `_IntCodec.encode_py`：删除 isinstance 检查，改为 `self.validate(value)`；验证 `test_int_codec_encode_py_rejects_float_with_codec_error` 仍 pass
- [x] 1.3 GREEN fix `_FloatCodec.encode_py`：同样改为 `self.validate(value)`
- [x] 1.4 更新 `test_int_codec_overflow_raises`：`pytest.raises(OverflowError)` → `pytest.raises(CodecError)`

## 2. F2: VARCHAR / CHAR 改 CodecError

- [x] 2.1 RED test `test_varchar_codec_overflow_raises_codec_error`：assert `pytest.raises(CodecError, match="length 11 exceeds max")` for `codec_for("VARCHAR", (10,)).encode_py("x" * 11)`
- [x] 2.2 GREEN fix `_VarcharCodec._check`：改 `raise TypeError(...)` → `raise CodecError(...)`
- [x] 2.3 更新 `tests/unit/test_type_system_v2.py` 中相关 VARCHAR 测试（line 251 area）`pytest.raises(TypeError)` → `pytest.raises(CodecError)`
- [x] 2.4 RED test `test_char_codec_overflow_raises_codec_error`：同 pattern for `_CharCodec`
- [x] 2.5 GREEN fix `_CharCodec.encode_py`：改 `raise TypeError(...)` → `raise CodecError(...)`
- [x] 2.6 更新 CHAR 长度越界测试：`pytest.raises(TypeError)` → `pytest.raises(CodecError)`

## 3. F1: Catalog.create_table isinstance 守卫

- [x] 3.1 RED test `test_create_table_rejects_legacy_2tuple_with_type_error` in `tests/integration/test_catalog.py`：assert `pytest.raises(TypeError, match="expected Column")` for `cat.create_table("t", [("id", "INT")], ...)`
- [x] 3.2 RED test `test_create_table_rejects_string_iterable`：assert `pytest.raises(TypeError)` for `cat.create_table("t", "INT", ...)`（防止 str 被 split 成字符）
- [x] 3.3 GREEN fix `Catalog.create_table`：`if not all(isinstance(c, Column) for c in cols): raise TypeError(f"create_table expects Column instances, got {type(cols[0]).__name__ if cols else type(cols).__name__}")`

## 4. F4: _load_column 错误信息分类

- [x] 4.1 修改 `_load_column`：将单一 `isinstance(item, dict)` 检查改为 `if isinstance(item, list): raise InvalidDatabaseFile("...legacy [name, type] arrays...")` + `if not isinstance(item, dict): raise InvalidDatabaseFile("...expected Column.to_dict() object form...")`
- [x] 4.2 更新 `test_load_column_rejects_legacy_list_form_with_helpful_message` 确保新信息仍含 "legacy" substring
- [x] 4.3 新增 `test_load_column_rejects_non_dict_non_list_with_generic_message` in `tests/unit/test_catalog_type_params.py`：assert `pytest.raises(InvalidDatabaseFile, match="expected Column.to_dict()")` for `_load_column(42)` / `_load_column(None)`

## 5. F5: 删除 stale section-divider 注释

- [x] 5.1 删除 `src/tinydb/type_system.py:127` 注释
- [x] 5.2 删除 `src/tinydb/type_system.py:174` 注释

## 6. 验证

- [x] 6.1 `pytest tests/ -q` 全绿（实际 689 tests pass：683 baseline + 6 new RED→GREEN）
- [x] 6.2 `pyflakes src/tinydb/` clean
- [x] 6.3 coverage ≥ 93%（93.30%，从 93.27% baseline 上升）
- [x] 6.4 archive：合并 spec（本 change 无 delta spec，跳过）+ git mv 到 `archive/2026-07-20-codec-exception-consistency/`（在 verify 阶段处理）
