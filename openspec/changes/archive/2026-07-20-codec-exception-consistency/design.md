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
