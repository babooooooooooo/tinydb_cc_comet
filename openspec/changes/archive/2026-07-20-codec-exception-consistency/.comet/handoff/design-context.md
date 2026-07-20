# Comet Design Handoff

- Change: codec-exception-consistency
- Phase: design
- Mode: compact
- Context hash: 44b7851f7e12ab2c47d67965c66ef0ac473c8b941f8a0eac75f8f7518c1fd681

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/codec-exception-consistency/proposal.md

- Source: openspec/changes/codec-exception-consistency/proposal.md
- Lines: 1-50
- SHA256: a58ddc8c17feb408e69c8a9919b22448183c9ddd3dc9550800ffc66ce8e80d2c

```md
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

```

## openspec/changes/codec-exception-consistency/design.md

- Source: openspec/changes/codec-exception-consistency/design.md
- Lines: 1-63
- SHA256: d9056265e1686bc0a08575f4c1292b89bf6cd44d44351179b0361a0ebd6c9528

```md
# Design: codec-exception-consistency

## Context

2026-07-20 code review of `type-codec-and-catalog-cleanup` (commit `15518f4`) 6 个 CONFIRMED 问题分两类：
- **异常契约统一**（F2/F3/F6）：codec 各方法的异常类型应统一为 `CodecError`，但实际存在 `OverflowError` (Int 越界)、`TypeError` (VARCHAR 长度越界)、`struct.error` (Int 类型错，已在 type-codec-cleanup 修复) 三种不一致。
- **catalog 严格校验**（F1/F4/F5）：`_load_column` 错误信息误导、`create_table` 静默接受非法输入、stale 注释指向已删函数。

## Goals / Non-Goals

**Goals:**
1. 修复 6 个 CONFIRMED review findings。
2. 保持 683 tests 全绿 + 5 个新 RED→GREEN 测试守护新契约。
3. 不破坏现有 `except CodecError` 行为（让更多 case 进入该 handler）。

**Non-Goals:**
- 不动业务逻辑、不重构 codec 实现。
- 不引入新 capability。
- 不为 v1 格式 .db 文件提供 migration（与 type-codec-cleanup DV2 同）。

## Decisions

### D1. encode_py 调 self.validate() 而非重复 isinstance
- `_IntCodec.parse_literal` (line 202-205) 已有 `self.validate(v)` pattern。
- `_IntCodec.validate` (line 207-214) 和 `_FloatCodec.validate` (line 292-300) 已实现完整 isinstance + range + NaN/Inf 检查。
- 复制 isinstance 逻辑会导致两处 source of truth，未来修改 validate 时容易遗漏 encode_py。
- DRY：让 encode_py 调 `self.validate(value)`，范围检查自动从 validate 拿到。
- 一并解决 F3：validate 抛 `CodecError`，encode_py 也跟着抛 `CodecError`。

### D2. _VarcharCodec / _CharCodec 改 CodecError
- DV7 在 type-codec-cleanup 验证阶段被识别为预存偏差，本 change 范围内一并修复。
- 与 D1 同方向：codec 异常应统一为 `CodecError`。
- 现有 2 个测试 `test_varchar_codec_..._exceeds_max` 和 `test_char_codec_..._exceeds_max` 更新为 `pytest.raises(CodecError)`。

### D3. Catalog.create_table 加 isinstance 守卫
- 当前 `tuple(schema)` 静默接受任何 iterable，含 legacy 2-tuple、字符串等。
- 加 `if not all(isinstance(c, Column) for c in cols): raise TypeError(...)`，错误信息直接说明"expected Column instances, got <type>"。
- 与 `_load_column` 的"严格拒绝非预期输入"形成对称：read 路径已在 type-codec-cleanup 收紧，write 路径在本次 change 收紧。

### D4. _load_column 错误信息分类
- 检测 `isinstance(item, list)`：保留 legacy form 提示（这是设计上最可能的失误）。
- 其他非 dict：改为通用 "expected Column.to_dict() object form"（不暗示 list-form）。
- MappingProxyType 等 dict-like 仍会进入"非 dict"分支（因为 `isinstance(MappingProxyType, dict) == False`），提示信息更准确。

### D5. 删除 stale section-divider 注释
- `type_system.py:127, 174` 两行注释指向已删函数。
- 直接删除，不替换为新注释（codec 现在是文件唯一内容，无需 breadcrumb）。

## Risks / Trade-offs

- **R1**：`Catalog.create_table` 加守卫后，任何依赖 2-tuple 静默接受的外部调用方会立即失败（之前是延迟到 INSERT 时 `AttributeError`）。 — 缓解：release notes 显式提示；type-codec-cleanup 已删除 list-form 接受路径，外部调用方需迁移。
- **R2**：`CodecError` 多继承 `TypeError`，所以 `except TypeError` 仍能捕获（向后兼容）；但反过来 `except CodecError` 现在能捕获以前漏掉的 case（VARCHAR 长度越界、Int 越界）。 — 影响面：用户代码若依赖 `except TypeError` 特定子类，会受影响；但 `CodecError` 本身已 IS-A `TypeError`，无破坏性。
- **R3**：`test_int_codec_overflow_raises` 测试期望从 `OverflowError` 改为 `CodecError`。这是一个小破坏性，但只影响内部 test contract，不影响生产代码（生产代码用 `except CodecError` 兜底）。 — 缓解：测试同步更新即可。

## Open Questions

- 是否需要保留 `_IntCodec.encode_py` 越界时 `CodecError` 的同时，让 `OverflowError` 也被抛出（多重抛）以兼容 `except OverflowError` 的旧代码？ — 当前决定：单抛 `CodecError`。`CodecError` IS-A `OverflowError`，`except OverflowError` 仍能捕获。无破坏性。

## Migration Notes

- 调用方若依赖 `Catalog.create_table(name, [("col", "TYPE")], ...)` 的 2-tuple 形式：升级前需迁移到 `Column` 对象（type-codec-cleanup DV2 同主题）。
- 调用方若依赖 VARCHAR/CHAR 长度越界抛 `TypeError`：升级后会抛 `CodecError`（IS-A `TypeError`），`except TypeError` 仍捕获。`except CodecError` 现在能捕获。
- 调用方若依赖 `IntCodec.encode_py` 越界抛 `OverflowError`：同 IS-A 关系，无破坏性。

```

## openspec/changes/codec-exception-consistency/tasks.md

- Source: openspec/changes/codec-exception-consistency/tasks.md
- Lines: 1-44
- SHA256: 1258a7b6df31c4d7db542cecf358e132025b723d650aad33e4387ac82cc10466

```md
# Tasks: codec-exception-consistency

## 1. F3+F6: encode_py → self.validate() refactor

- [x] 1.1 RED test `test_int_codec_encode_py_overflow_raises_codec_error`：assert `pytest.raises(CodecError, match="INT out of range")` for `codec_for("INT").encode_py(2**33)` （现有 `test_int_codec_overflow_raises` 期望 `OverflowError`）
- [x] 1.2 GREEN fix `_IntCodec.encode_py`：删除 isinstance 检查，改为 `self.validate(value)`；验证 `test_int_codec_encode_py_rejects_float_with_codec_error` 仍 pass
- [x] 1.3 GREEN fix `_FloatCodec.encode_py`：同样改为 `self.validate(value)`
- [x] 1.4 更新 `test_int_codec_overflow_raises`：`pytest.raises(OverflowError)` → `pytest.raises(CodecError)`
- [x] 1.5 RED test `test_int_codec_encode_py_uses_validate_path`（可选 sanity check）：检查 encode_py 不再独立做 isinstance（保证 DRY）

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

- [x] 6.1 `pytest tests/ -q` 全绿（预期 688 tests pass：683 + 5 new RED→GREEN）
- [x] 6.2 `pyflakes src/tinydb/` clean
- [x] 6.3 coverage ≥ 93%（持平或上升）
- [x] 6.4 `grep -rnE "\bvalidate_compare\b|\bencode_int\b|\bpy_to_db\b|\bdb_to_py\b" src/ tests/` 无命中
- [x] 6.5 创建 verify report `docs/superpowers/reports/2026-07-20-codec-exception-consistency-verify.md`
- [x] 6.6 archive：合并 spec（本 change 无 delta spec，跳过）+ git mv 到 `archive/2026-07-20-codec-exception-consistency/`

```
