# Tasks: tinydb-types

## 1. type_system 注册表与 codec 框架

- [x] 1.1 编写 `tests/unit/test_type_system_v2.py::test_codec_registry_*`，红
- [x] 1.2 在 `type_system.py` 定义 `TypeCodec` Protocol + `REGISTRY` 字典
- [x] 1.3 把 MVP 4 个 codec 迁移到 registry 形式（旧路径保持兼容）

## 2. 整数宽度（S/I/B）

- [ ] 2.1 编写 `test_smallint_range_boundary`，红
- [ ] 2.2 在 `IntCodec` 上加 `width` 参数；SMALLINT (2)、INT (4)、BIGINT (8)
- [ ] 2.3 编写 `test_int_overflow_raises`，绿
- [ ] 2.4 编写 `test_bigint_min_max`，绿

## 3. 浮点（DOUBLE/REAL + inf/nan 拒绝）

- [ ] 3.1 编写 `test_float_rejects_inf_nan_literally`，红
- [ ] 3.2 在 `FloatCodec` 上加 `allow_inf` / `allow_nan` 开关；默认 False
- [ ] 3.3 编写 `test_double_alias_accepted`，绿
- [ ] 3.4 编写 `test_real_alias_treated_as_float`，绿
- [ ] 3.5 编写 `test_boolean_alias_accepted`，绿

## 4. 字符串（VARCHAR/CHAR）

- [ ] 4.1 编写 `test_varchar_max_length_enforced`，红
- [ ] 4.2 在 `VarcharCodec` 持久化 max_len；encode 时校验
- [ ] 4.3 编写 `test_char_pads_and_validates`，绿
- [ ] 4.4 编写 `test_text_alias_still_works_for_backward_compat`，绿

## 5. DECIMAL

- [ ] 5.1 编写 `test_decimal_encode_scale_roundtrip`，红
- [ ] 5.2 在 `DecimalCodec` 实现 scaled int64 编码
- [ ] 5.3 编写 `test_decimal_precision_overflow_raises`，绿
- [ ] 5.4 编写 `test_decimal_literal_parsed_in_context`，绿

## 6. DATE / TIME / TIMESTAMP

- [ ] 6.1 编写 `test_date_iso_literal_parse_roundtrip`，红
- [ ] 6.2 在 `DateCodec` 实现 ISO 8601 解析 + 4-byte day count 编码
- [ ] 6.3 编写 `test_time_iso_literal_parse_roundtrip`，绿
- [ ] 6.4 在 `TimeCodec` 实现
- [ ] 6.5 编写 `test_timestamp_iso_literal_parse_roundtrip`，绿
- [ ] 6.6 在 `TimestampCodec` 实现

## 7. Parser 列定义带参数

- [ ] 7.1 编写 `test_parser_varchar_with_max_len`，红
- [ ] 7.2 在 `parser.py::parse_column_def` 接收可选 `(N)` 或 `(p, s)` 参数
- [ ] 7.3 编写 `test_parser_decimal_with_precision_scale`，绿
- [ ] 7.4 编写 `test_parser_rejects_too_many_params`，绿

## 8. 字面量识别

- [ ] 8.1 在 `tokenizer.py` 实现 `DATE` / `TIME` / `TIMESTAMP` 前缀关键字路径
- [ ] 8.2 编写 `test_literal_type_resolution_against_schema`，红
- [ ] 8.3 在 INSERT 路径根据 schema 把不带前缀的字面量 assign 类型（已有 strict 校验；本次扩到 15 类型）

## 9. 兼容性

- [ ] 9.1 MVP 旧 catalog schema 反序列化路径不爆（4 类型仍是 4 类型 subset）
- [ ] 9.2 README 增加 types 段落

## 10. 回归

- [ ] 10.1 MVP + engine-v1 + constraints + aggregation + engine-v2 + acid 测试全部继续通过（acid 仍在并行时序上）
- [ ] 10.2 模块行数：`type_system.py ≤ 350`、`parser.py ≤ 870`
- [ ] 10.3 覆盖率 ≥ 90%；新代码 100%
