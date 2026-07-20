# codec-exception-consistency

## Why

2026-07-20 完成 code review of `type-codec-and-catalog-cleanup`（commit `15518f4`），8-angle review 发现 6 个 CONFIRMED 问题（全部经 1-vote verifier 验证）。这些问题与"codec 异常类型统一"和"catalog 严格校验"两个主题紧密相关，需要在一个独立 change 中批量修复，否则 codec exception 契约会持续不一致，`Catalog.create_table` 静默接受 legacy 2-tuple 会让用户在 INSERT 时才遇到 `AttributeError`。

具体问题：
1. **F1 (security/correctness)**：`Catalog.create_table` 的 `cols: tuple[Column, ...] = tuple(schema)` 无 isinstance 守卫，legacy 2-tuple `[("id", "INT")]` 静默存储为 `(("id", "INT"),)`，首次 `col.name` 访问抛 `AttributeError: 'tuple' object has no attribute 'name'`，错误现场远离 root cause。
2. **F2 (codec 异常契约)**：`_VarcharCodec._check` (line 312) 和 `_CharCodec.encode_py` (line 337) 长度越界抛 plain `TypeError`；现有测试 `test_varchar_codec_..._exceeds_max` 锁定 `pytest.raises(TypeError)`，破坏"所有 codec 异常为 `CodecError`"契约。属于 verify 报告 DV7 偏差的延后修复。
3. **F3 (codec 异常契约)**：`_IntCodec.encode_py` 越界抛 plain `OverflowError` (line 195)，而其 `validate()` (line 214) 抛 `CodecError`，相同条件不同异常类，破坏新契约。
4. **F4 (UX)**：`_load_column` (catalog.py:99-103) 的 `InvalidDatabaseFile` 信息对所有非 dict 输入都说"legacy [name, type] arrays are no longer supported"，对 int / None / `MappingProxyType` 等非 legacy form 输入产生误导性提示。
5. **F5 (dead code / comment drift)**：type_system.py:127, 174 两行 stale section-divider 注释 `# TypeCodec registry; legacy helpers above stay for backward compatibility.` 指向已删除的 `encode_int` / `py_to_db` 等函数。
6. **F6 (DRY / altitude)**：`_IntCodec.encode_py` 和 `_FloatCodec.encode_py` 的 isinstance + bool/finite 检查复制了 `validate()` 已有的逻辑。F3 + F6 合并修复：让 `encode_py` 调 `self.validate(value)`，一并解决 F3 的 OverflowError 不一致（因为 `validate()` 抛 `CodecError`）。

## What Changes

- **F1 (BREAKING for hidden misuse)**：`Catalog.create_table` 加 isinstance 守卫，拒绝非 `Column` 元素；调用方收到即时 `TypeError` 而非延迟 `AttributeError`。
- **F2**：`_VarcharCodec._check` 和 `_CharCodec.encode_py` 改抛 `CodecError`；更新 2 个 `pytest.raises(TypeError)` 为 `pytest.raises(CodecError)`。
- **F3 + F6**：`_IntCodec.encode_py` 和 `_FloatCodec.encode_py` 删除重复 isinstance 逻辑，改为调用 `self.validate(value)`（与 `_IntCodec.parse_literal:202-205` 现有 pattern 一致）；F3 异常类型不一致问题作为副带修复解决（`validate()` 抛 `CodecError`）。`_IntCodec.encode_py` 越界异常从 `OverflowError` 改为 `CodecError`（来自 `validate`）；同步更新 `test_int_codec_overflow_raises` 的 `pytest.raises(OverflowError)` → `pytest.raises(CodecError)`。
- **F4**：`_load_column` 错误信息分两种情况：(a) 输入是 list/tuple → 保留 legacy form 提示；(b) 输入是其他非 dict（int/None/str/MappingProxyType）→ 改为通用 "expected Column.to_dict() object form"。
- **F5**：删除 `type_system.py:127, 174` 两行 stale section-divider 注释。

## Capabilities

### New Capabilities

（无新增 capability）

### Modified Capabilities

（无修改 capability；本次 change 不动 spec，所有 6 个 fix 都在 implementation 层，spec 已通过 type-codec-cleanup 锁定 codec exception 契约）

## Impact

- 源码：`src/tinydb/type_system.py`（-12 行 / +4 行）、`src/tinydb/catalog.py`（+8 行）
- 测试：`tests/unit/test_type_system_v2.py`（+0/-2，更新 2 个 `OverflowError` → `CodecError`）+（+4，4 个新 RED test 守护 F2/F3）；`tests/unit/test_catalog_type_params.py`（+1，1 个新 RED test 守护 F1）
- 预期测试：683 + 5 = 688 tests pass
- 调用方迁移点：
  - `_VarcharCodec` / `_CharCodec` 长度越界 → 异常类型从 `TypeError` 变 `CodecError`。`except CodecError` 现在能捕获（之前漏）。
  - `_IntCodec.encode_py` 越界 → 从 `OverflowError` 变 `CodecError`。`except CodecError` 现在能捕获。
  - `Catalog.create_table` 传 2-tuple → 立即抛 `TypeError`（之前延迟到 `AttributeError`）。
  - `_load_column` 错误信息：legacy form 提示保留；其他非 dict 输入信息更准确。

## Out of Scope

- 不动 codec 行为语义（仅统一异常类型 + DRY）
- 不引入新 capability
- 不动 `row_codec.py` / `executor.py` / `index_manager.py`（已通过 `codec.validate()` 预校验，encode_py 守卫为 defense-in-depth）
- 不动 `Column` / `TableInfo` / `Catalog.from_bytes` 接口
- 不为 v1 格式 .db 文件提供 migration script（与 type-codec-cleanup DV2 同一未决项）
