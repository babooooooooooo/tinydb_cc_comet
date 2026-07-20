---
comet_change: codec-exception-consistency
role: technical-design
canonical_spec: openspec
status: draft
---

# Design: codec-exception-consistency

> **关联文档**：[proposal.md](../../openspec/changes/codec-exception-consistency/proposal.md) · [design.md](../../openspec/changes/codec-exception-consistency/design.md) · [tasks.md](../../openspec/changes/codec-exception-consistency/tasks.md)

## 背景

2026-07-20 code review of `type-codec-and-catalog-cleanup` (commit `15518f4`) 发现 6 个 CONFIRMED 问题，需要在一个独立 change 中批量修复（参见 proposal.md）。

## 修复矩阵

| Finding | 文件 | 修复 | 影响面 |
|---------|------|------|--------|
| F1 | src/tinydb/catalog.py:154 | `Catalog.create_table` 加 isinstance 守卫 | 调用方：拒绝非 Column 输入 |
| F2 | src/tinydb/type_system.py:312, 337 | `_VarcharCodec._check` / `_CharCodec.encode_py` 改 `CodecError` | 现有 `test_varchar_codec_..._exceeds_max` 等 2 测试 |
| F3 + F6 | src/tinydb/type_system.py:190-196 + 277-282 | `encode_py` 调 `self.validate(value)` | 现有 `test_int_codec_overflow_raises` |
| F4 | src/tinydb/catalog.py:99-103 | `_load_column` 错误信息按 list/non-dict 分类 | 现有 `test_load_column_rejects_legacy_list_form_with_helpful_message` |
| F5 | src/tinydb/type_system.py:127, 174 | 删除 2 行 stale 注释 | 无 |

## 详细技术设计

### D1. encode_py 调 self.validate() (F3+F6)

**Before**:
```python
def encode_py(self, value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise CodecError(f"expected int for {self.name}, got {type(value).__name__}")
    fmt, lo, hi = self._spec
    if not (lo <= value < hi):
        raise OverflowError(f"{self.name} out of range: {value}")
    return struct.pack(fmt, value)
```

**After**:
```python
def encode_py(self, value):
    self.validate(value)  # delegates isinstance + range check, raises CodecError
    fmt, lo, hi = self._spec
    return struct.pack(fmt, value)
```

**Key insight**: `_IntCodec.parse_literal` (line 202-205) 已有 `self.validate(v)` 模式。本次 refactor 统一所有 3 个方法（`encode_py` / `decode_bytes` / `parse_literal`）都通过 `validate` 走统一路径。

**Trade-off**: `decode_bytes` 没有走 `validate`（语义不同：bytes → Python 已经是受信任输入）。保持现状。

### D2. _VarcharCodec / _CharCodec 改 CodecError (F2)

**Before**:
```python
def _check(self, n: int) -> None:
    if n > self.max_len:
        raise TypeError(f"VARCHAR({self.max_len}) length {n} exceeds max")
```

**After**:
```python
def _check(self, n: int) -> None:
    if n > self.max_len:
        raise CodecError(f"VARCHAR({self.max_len}) length {n} exceeds max")
```

### D3. Catalog.create_table 加 isinstance 守卫 (F1)

**Before**:
```python
cols: tuple[Column, ...] = tuple(schema)
```

**After**:
```python
schema_list = list(schema)
if schema_list and not all(isinstance(c, Column) for c in schema_list):
    bad = next(c for c in schema_list if not isinstance(c, Column))
    raise TypeError(
        f"create_table expects Column instances, got {type(bad).__name__}: {bad!r}"
    )
cols: tuple[Column, ...] = tuple(schema_list)
```

### D4. _load_column 错误信息分类 (F4)

**Before**:
```python
def _load_column(item) -> Column:
    if not isinstance(item, dict):
        raise InvalidDatabaseFile(
            f"unrecognized column entry: {item!r} "
            "(expected Column.to_dict() object form; legacy [name, type] arrays "
            "are no longer supported — please migrate to v2 object format)"
        )
    return Column.from_dict(item)
```

**After**:
```python
def _load_column(item) -> Column:
    if isinstance(item, list):
        raise InvalidDatabaseFile(
            f"unrecognized column entry: {item!r} "
            "(legacy [name, type] arrays are no longer supported — "
            "please migrate to v2 object format)"
        )
    if not isinstance(item, dict):
        raise InvalidDatabaseFile(
            f"unrecognized column entry: {item!r} "
            "(expected Column.to_dict() object form)"
        )
    return Column.from_dict(item)
```

### D5. 删除 stale 注释 (F5)

直接删除 2 行注释，无替换。

## 风险

参见 design.md R1/R2/R3。

## 验证

- 683 baseline + 5 new RED→GREEN = 688 tests
- coverage ≥ 93%
- pyflakes clean
- 现有 `except CodecError` handler 覆盖面扩大（VARCHAR/CHAR 长度越界、Int 越界均进入 `CodecError` handler）
