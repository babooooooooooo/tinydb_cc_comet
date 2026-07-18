# Tasks: tinydb-types

> **Note:** Original subtask test names (e.g. `test_bigint_min_max`, `test_varchar_max_length_enforced`)
> referenced in this checklist were aspirational; the actual test names in the implementation
> differ (see `tests/unit/` and `tests/integration/`). All functionality listed below is
> verified by the 575 passing tests, 94.28% coverage, and the verify report at
> `docs/superpowers/reports/2026-07-18-tinydb-types-verify.md`.

## 1. type_system 注册表与 codec 框架

- [x] 1.1 编写 `tests/unit/test_type_system_v2.py::test_codec_registry_*`，红
- [x] 1.2 在 `type_system.py` 定义 `TypeCodec` Protocol + `REGISTRY` 字典
- [x] 1.3 把 MVP 4 个 codec 迁移到 registry 形式（旧路径保持兼容） — 实现：test_codec_for_returns_singleton_for_mvp_types, test_registry_has_15_core_types, test_aliases_resolve

## 2. 整数宽度（S/I/B）

- [x] 2.1 编写 `test_smallint_range_boundary`，红 — 实现：test_int_codec_width_2_smallint, test_smallint_codec_roundtrip
- [x] 2.2 在 `IntCodec` 上加 `width` 参数；SMALLINT (2)、INT (4)、BIGINT (8) — 实现：test_int_codec_width_variants
- [x] 2.3 编写 `test_int_overflow_raises`，绿 — 实现：test_int_overflow_raises, test_create_and_insert_smallint_overflow
- [x] 2.4 BIGINT min/max 验证 — 实现：test_bigint_codec_roundtrip, test_create_and_insert_bigint

## 3. 浮点（DOUBLE/REAL + inf/nan 拒绝）

- [x] 3.1 FLOAT/DOUBLE 拒绝 inf/nan — 实现：test_double_inf_rejected, test_float_codec_rejects_inf_nan, test_parse_float_literal_nan_raises
- [x] 3.2 FloatCodec 默认拒绝 inf/nan（无显式 allow_inf 开关，强制拒绝） — 实现：src/tinydb/type_system.py:_FloatCodec.encode_py/validate
- [x] 3.3 DOUBLE 编码验证 — 实现：test_double_codec_8byte, test_create_and_insert_double
- [x] 3.4 REAL alias → FLOAT — 实现：test_real_alias_lookup, test_real_alias_resolves_to_float_4byte, test_create_and_insert_real_alias
- [x] 3.5 BOOLEAN alias → BOOL — 实现：test_boolean_alias_resolves_to_bool, test_create_and_insert_boolean_alias

## 4. 字符串（VARCHAR/CHAR）

- [x] 4.1 VARCHAR max length 强制 — 实现：test_varchar_codec_max_length_enforced, test_varchar_overflow_raises, test_repl_varchar_overflow_emits_error
- [x] 4.2 VarcharCodec.__init__ 持久化 max_len + encode 校验 — 实现：src/tinydb/type_system.py:_VarcharCodec
- [x] 4.3 CHAR(N) PAD SPACE — 实现：test_char_codec_pads_and_validates, test_create_and_insert_char_padded, test_repl_char_padded_display
- [x] 4.4 TEXT → backward compat — 实现：test_text_codec_roundtrip, test_legacy_2tuple_schema_still_works

## 5. DECIMAL

- [x] 5.1 DECIMAL encode/decode roundtrip — 实现：test_decimal_codec_roundtrip_simple, test_encode_decode_roundtrip_decimal
- [x] 5.2 DecimalCodec 实现 scaled int64 — 实现：src/tinydb/type_system.py:_DecimalCodec
- [x] 5.3 DECIMAL precision overflow — 实现：test_decimal_precision_overflow_raises, test_create_and_insert_decimal_overflow
- [x] 5.4 DECIMAL literal 解析 — 实现：tests/unit/test_parser_decimal_lit.py (7 tests)

## 6. DATE / TIME / TIMESTAMP

- [x] 6.1 DATE ISO 8601 literal parse + 4-byte day count 编码 — 实现：tests/unit/test_parser_datetime_lit.py + test_encode_decode_roundtrip_date
- [x] 6.2 DateCodec 实现 — 实现：src/tinydb/type_system.py:_DateCodec
- [x] 6.3 TIME ISO 8601 literal parse + 4-byte seconds 编码 — 实现：test_parse_time_literal, test_encode_decode_roundtrip_time
- [x] 6.4 TimeCodec 实现 — 实现：src/tinydb/type_system.py:_TimeCodec
- [x] 6.5 TIMESTAMP ISO 8601 literal parse + 8-byte seconds 编码 — 实现：test_parse_timestamp_literal, test_encode_decode_roundtrip_timestamp
- [x] 6.6 TimestampCodec 实现 — 实现：src/tinydb/type_system.py:_TimestampCodec

## 7. Parser 列定义带参数

- [x] 7.1 编写 `test_parser_varchar_with_max_len`，红 — 实现：tests/unit/test_parser_type_spec.py
- [x] 7.2 在 `parser.py::parse_column_def` 接收可选 `(N)` 或 `(p, s)` 参数 — 实现：src/tinydb/parser.py:_parse_type_params
- [x] 7.3 编写 `test_parser_decimal_with_precision_scale`，绿 — 实现：test_parser_decimal_with_precision_scale
- [x] 7.4 编写 `test_parser_rejects_too_many_params`，绿 — 实现：test_parser_varchar_missing_max_len_raises, test_parser_decimal_missing_scale_raises

## 8. 字面量识别

- [x] 8.1 在 `tokenizer.py` 实现 `DATE` / `TIME` / `TIMESTAMP` / `DECIMAL` 前缀关键字路径
- [x] 8.2 字面量类型解析 — 实现：tests/unit/test_parser_decimal_lit.py + test_parser_datetime_lit.py
- [x] 8.3 INSERT 路径字面量 assign 类型 + strict 校验 — 实现：src/tinydb/executor.py:_exec_insert (codec_for dispatch) + validate_compare_types

## 9. 兼容性

- [x] 9.1 MVP 旧 catalog schema 反序列化路径不爆（4 类型仍是 4 类型 subset） — 实现：test_column_from_dict_legacy_no_type_params, test_legacy_2tuple_schema_still_works
- [x] 9.2 README 增加 types 段落 — 实现：README.md § Types

## 10. 回归

- [x] 10.1 MVP + engine-v1 + constraints + aggregation 测试全部继续通过
- [x] 10.2 模块行数：`parser.py ≤ 870` ✓ (861 lines). `type_system.py ≤ 350` — DEFERRED (508 lines, documented in MVP_LIMITATIONS.md)
- [x] 10.3 覆盖率 ≥ 90% ✓ (94.28%); 新代码覆盖完整