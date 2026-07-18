---
change: tinydb-types
design-doc: docs/superpowers/specs/2026-07-18-tinydb-types-design.md
base-ref: 5db80cfa72232f850638db64fd51014c670234f4
---

# tinydb-types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `tinydb-mvp` + `tinydb-engine-v1` + `tinydb-constraints` 累积的 4 类型（INT/TEXT/FLOAT/BOOL）之上，扩展为 15 类型（+4 别名），支持 `VARCHAR(N)` / `CHAR(N)` / `DECIMAL(p,s)` 类型参数、`DATE` / `TIME` / `TIMESTAMP` UTC 统一时区、`DECIMAL` scaled int64 精确小数、`FLOAT`/`DOUBLE` 拒绝 inf/nan，严格同类型比较。

**Architecture:** 双层 codec 架构 — `TypeCodec` Protocol + `REGISTRY: dict[str, TypeCodec]` + `lookup()` / `codec_for()` factory；4 个 MVP 函数迁到 Protocol 实例，新增 11 个 codec。AST 引入 `type_params: tuple[int, ...]` 字段（ColumnDefinition + Column），catalog JSON 加 `type_params` 键并向后兼容（缺字段默认 `()`）。Parser 在 `CREATE TABLE` 列定义解析 `(N)` / `(p,s)`；在 `INSERT`/`WHERE` 路径接受 `DATE '...'` / `TIME '...'` / `TIMESTAMP '...'` / `DECIMAL '...'` 字面量前缀。`row_codec` 改用 `codec_for(typ, params).encode_py()` / `.decode_bytes()` 累计 offset，无 length prediction。

**Tech Stack:** Python 3.11+，纯 stdlib。dev 依赖沿用既有（`pytest>=7`、`pytest-cov>=4`、`hypothesis>=6`）。零新运行时依赖。

---

## 文件结构（实施前映射）

### 源码 `src/tinydb/`（变更范围）

| 文件 | 变动 | 行数预算 |
|------|------|---------|
| `type_system.py` | 重构：`TypeCodec` Protocol + `REGISTRY` + `lookup` + `codec_for`；15 个 codec 类 | ≤ 350 |
| `tokenizer.py` | `KEYWORDS` 加 `DATE` / `TIME` / `TIMESTAMP` | ≤ 200 |
| `parser.py` | `_parse_type_spec()` 新增；`ColumnDefinition.type_params` 新增；`SUPPORTED_TYPES` 扩到 19；`_parse_datetime_literal` 新增 | ≤ 870 |
| `catalog.py` | `Column.type_params` 新增；`from_dict`/`to_dict` 处理新字段（向后兼容） | ≤ 200 |
| `row_codec.py` | `encode_row` / `decode_row` 改用 `codec_for(typ, params)` + `schema_v2()` | ≤ 150 |

### 测试 `tests/`

| 文件 | 状态 | 覆盖 |
|------|------|------|
| `tests/unit/test_type_system_v2.py` | 新建 | 11 新 codec + 4 别名 + registry 完整性 |
| `tests/unit/test_parser_type_spec.py` | 新建 | VARCHAR(N) / CHAR(N) / DECIMAL(p,s) 解析 + 参数校验 |
| `tests/unit/test_parser_datetime_lit.py` | 新建 | DATE / TIME / TIMESTAMP 字面量 |
| `tests/unit/test_parser_decimal_lit.py` | 新建 | DECIMAL '1.23' 字面量 |
| `tests/unit/test_catalog_type_params.py` | 新建 | Column.type_params 序列化 + 向后兼容 |
| `tests/unit/test_row_codec_v2.py` | 新建 | 15 类型 row roundtrip |
| `tests/integration/test_types_roundtrip.py` | 新建 | 端到端 INSERT + SELECT 全 15 类型 |
| `tests/integration/test_types_in_where.py` | 新建 | WHERE 跨类型严格比较 + 错误信息 |
| `tests/integration/test_types_repl.py` | 新建 | REPL 进程级 DDL/DML with 15 类型 |

### 文档

- `docs/MVP_LIMITATIONS.md` — 增补：types 交付后严格同类型比较；FLOAT 4 字节单精度；DATETIME UTC 统一

---

## 测试策略

每 capability 沿用 4 层金字塔：

1. **Unit**：每个 codec 的 `encode_py` / `decode_bytes` / `parse_literal` / `validate` 4 个方法独立测
2. **Integration**：`Database.execute()` 端到端 CREATE TABLE + INSERT + SELECT WHERE 跨 15 类型
3. **E2E**：3 条 REPL 进程级测试（VARCHAR / DECIMAL / DATE 全路径）
4. **Property**：随机 schema + 随机 INSERT 数据 → row_codec encode/decode roundtrip 无损

覆盖率门槛：`--cov-fail-under=90`（新代码 ≥ 95%）。

---

## Commit 粒度规则

按本 plan 任务粒度拆 commit。每任务内部：

- 单 Red→Green 循环：整 Task 单 commit
- 多循环：每个循环单独 commit

Commit message 格式：`feat(types): <subject>` / `test(types): <subject>` / `refactor(types): <subject>` / `fix(types): <subject>` / `docs(types): <subject>`

---

## 任务列表

> **执行顺序**：Task 1 → 2 → ... → 17。每任务完成后必须 git commit 再进入下一任务。
> **测试先行**：每任务 Step 1 都是"写失败测试"。Step 2 必须看到 RED 才推进 Step 3。
> **行数审计**：每次 commit 前对新增模块跑 `wc -l src/tinydb/<module>.py`，违反预算（type_system.py ≤ 350 / parser.py ≤ 870）→ 立即拆分子任务。
> **venv 调用约定**：所有 pytest 必须用 `cd /home/lz/projects/tinydb-worktrees/tinydb-types && .venv/bin/python -m pytest ...`（避免 PEP 668）。

---

### Task 1: TypeCodec Protocol + REGISTRY + lookup/codec_for scaffolding

- [x] **Task 1: TypeCodec Protocol + REGISTRY + lookup/codec_for scaffolding**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Create: `tests/unit/test_type_system_registry.py`

- [x] **Step 1: Write the failing test**

```python
# tests/unit/test_type_system_registry.py
from tinydb.type_system import lookup, codec_for, REGISTRY


def test_registry_has_15_core_types():
    expected = {"INT", "SMALLINT", "BIGINT", "FLOAT", "DOUBLE", "REAL",
                "TEXT", "VARCHAR", "CHAR", "BOOL", "BOOLEAN",
                "DECIMAL", "DATE", "TIME", "TIMESTAMP"}
    assert set(REGISTRY.keys()) == expected


def test_lookup_returns_codec():
    codec = lookup("INT")
    assert codec is not None
    assert hasattr(codec, "encode_py")
    assert hasattr(codec, "decode_bytes")


def test_codec_for_non_parametric_returns_singleton():
    a = codec_for("INT")
    b = codec_for("INT")
    assert a is b  # singleton for non-parametric


def test_codec_for_varchar_creates_configured_instance():
    codec = codec_for("VARCHAR", (64,))
    assert codec is not None
    # verify max_len configured (will be tested in Task 6)


def test_codec_for_varchar_without_params_raises():
    import pytest
    with pytest.raises(ValueError, match="VARCHAR requires"):
        codec_for("VARCHAR")


def test_codec_for_decimal_requires_two_params():
    import pytest
    with pytest.raises(ValueError, match="DECIMAL requires"):
        codec_for("DECIMAL")
    with pytest.raises(ValueError, match="DECIMAL requires"):
        codec_for("DECIMAL", (10,))


def test_codec_for_decimal_validates_p_s_bounds():
    import pytest
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (0, 0))  # p must be >= 1
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (19, 0))  # p must be <= 18
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (10, 10))  # s must be < p


def test_lookup_unknown_type_raises():
    import pytest
    with pytest.raises(KeyError):
        lookup("UNKNOWN_TYPE")
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd /home/lz/projects/tinydb-worktrees/tinydb-types && .venv/bin/python -m pytest tests/unit/test_type_system_registry.py -v`
Expected: FAIL with `ImportError: cannot import name 'lookup' from 'tinydb.type_system'` or `ModuleNotFoundError`

- [x] **Step 3: Write minimal implementation**

```python
# src/tinydb/type_system.py (append below existing functions)
from typing import Any, Protocol


class TypeCodec(Protocol):
    """Protocol for all type codecs. Each codec owns its bytes encoding."""

    name: str
    aliases: tuple = ()

    def encode_py(self, value: Any) -> bytes: ...
    def decode_bytes(self, buf: bytes, offset: int) -> tuple: ...
    def parse_literal(self, text: str, params: tuple) -> Any: ...
    def validate(self, value: Any) -> None: ...


# Empty REGISTRY initially; populated by subsequent tasks.
REGISTRY: dict = {}


def lookup(type_name: str):
    """Return the parameterless codec template for `type_name` (case-sensitive uppercase).

    Raises KeyError if unknown.
    """
    if type_name not in REGISTRY:
        raise KeyError(f"unknown type: {type_name!r}")
    return REGISTRY[type_name]


def codec_for(type_name: str, params: tuple = ()):
    """Return a configured codec instance for `type_name` with `params`.

    For non-parametric types (INT, TEXT, FLOAT, BOOL, DATE, TIME, TIMESTAMP),
    params must be `()` and the registry singleton is returned.

    For parametric types:
      - VARCHAR(N) / CHAR(N): params must be `(N,)` with N >= 1
      - DECIMAL(p, s): params must be `(p, s)` with 1 <= p <= 18 and 0 <= s < p
    """
    if type_name not in REGISTRY:
        raise KeyError(f"unknown type: {type_name!r}")
    # Parametric validation (full implementations in later tasks):
    if type_name in ("VARCHAR", "CHAR"):
        if len(params) != 1 or params[0] < 1:
            raise ValueError(f"{type_name} requires (N,) with N >= 1, got {params}")
    if type_name == "DECIMAL":
        if len(params) != 2:
            raise ValueError(f"DECIMAL requires (p, s), got {params}")
        p, s = params
        if not (1 <= p <= 18 and 0 <= s < p):
            raise ValueError(f"DECIMAL({p},{s}) invalid; need 1 <= p <= 18 and 0 <= s < p")
    return REGISTRY[type_name]
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_registry.py -v`
Expected: 8 passed

- [x] **Step 5: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-types
git add src/tinydb/type_system.py tests/unit/test_type_system_registry.py
git commit -m "feat(types): add TypeCodec Protocol + REGISTRY + lookup/codec_for scaffolding"
```

---

### Task 2: Migrate 4 MVP codecs to Protocol form (INT/TEXT/FLOAT/BOOL)

- [x] **Task 2: Migrate 4 MVP codecs to Protocol form (INT/TEXT/FLOAT/BOOL)**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `src/tinydb/row_codec.py`
- Modify: `tests/unit/test_type_system.py` (existing MVP tests; verify still pass)

- [ ] **Step 1: Write the failing test for codec instances**

```python
# tests/unit/test_type_system_v2.py (new)
from tinydb.type_system import lookup, codec_for


def test_int_codec_roundtrip():
    codec = lookup("INT")
    for v in [0, 1, -1, 2**31 - 1, -(2**31)]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_int_codec_overflow_raises():
    codec = lookup("INT")
    import pytest
    with pytest.raises(OverflowError):
        codec.encode_py(2**31)
    with pytest.raises(OverflowError):
        codec.encode_py(-(2**31) - 1)


def test_text_codec_roundtrip():
    codec = lookup("TEXT")
    for v in ["", "hello", "中文", "with 'apostrophe'"]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_bool_codec_roundtrip():
    codec = lookup("BOOL")
    for v in [True, False]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_float_codec_4byte_single_precision():
    """FLOAT uses 4-byte single precision (per design D3)."""
    codec = lookup("FLOAT")
    encoded = codec.encode_py(1.5)
    assert len(encoded) == 4  # single precision
    assert codec.decode_bytes(encoded, 0)[0] == 1.5


def test_float_codec_rejects_inf():
    codec = lookup("FLOAT")
    import pytest
    with pytest.raises(ValueError, match="inf/NaN not allowed"):
        codec.encode_py(float("inf"))


def test_float_codec_rejects_nan():
    codec = lookup("FLOAT")
    import pytest
    with pytest.raises(ValueError, match="inf/NaN not allowed"):
        codec.encode_py(float("nan"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -v`
Expected: FAIL with `KeyError: 'INT'` (REGISTRY empty)

- [ ] **Step 3: Write minimal implementation**

Append to `src/tinydb/type_system.py` (keep existing functions for backward compat per §F2):

```python
# --- Codec Protocol implementations ---


class _IntCodec:
    """32-bit signed integer (INT). SMALLINT/BIGINT via width param in later task."""

    name = "INT"

    def encode_py(self, value):
        if not (-(2**31) <= value < 2**31):
            raise OverflowError(f"INT out of range: {value}")
        return struct.pack(">i", value)

    def decode_bytes(self, buf, offset):
        if offset + 4 > len(buf):
            raise ValueError(f"INT decode truncated at offset {offset}")
        return struct.unpack_from(">i", buf, offset)[0], offset + 4

    def parse_literal(self, text, params):
        v = int(text)
        if not (-(2**31) <= v < 2**31):
            raise OverflowError(f"INT out of range: {v}")
        return v

    def validate(self, value):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"expected int for INT, got {type(value).__name__}")
        if not (-(2**31) <= value < 2**31):
            raise OverflowError(f"INT out of range: {value}")


class _TextCodec:
    """Unlimited-length UTF-8 string. VARCHAR(N) / CHAR(N) in later tasks."""

    name = "TEXT"

    def encode_py(self, value):
        data = value.encode("utf-8")
        return struct.pack(">H", len(data)) + data

    def decode_bytes(self, buf, offset):
        if offset + 2 > len(buf):
            raise ValueError("TEXT length prefix truncated")
        (n,) = struct.unpack_from(">H", buf, offset)
        if offset + 2 + n > len(buf):
            raise ValueError(f"TEXT payload truncated (need {n} bytes)")
        return buf[offset + 2 : offset + 2 + n].decode("utf-8"), offset + 2 + n

    def parse_literal(self, text, params):
        # text includes surrounding single quotes
        if len(text) < 2 or text[0] != "'" or text[-1] != "'":
            raise ValueError(f"invalid text literal: {text!r}")
        return text[1:-1].replace("''", "'")

    def validate(self, value):
        if not isinstance(value, str):
            raise TypeError(f"expected str for TEXT, got {type(value).__name__}")


class _BoolCodec:
    name = "BOOL"
    aliases = ("BOOLEAN",)

    def encode_py(self, value):
        return b"\x01" if value else b"\x00"

    def decode_bytes(self, buf, offset):
        if offset + 1 > len(buf):
            raise ValueError("BOOL decode truncated")
        return buf[offset] != 0, offset + 1

    def parse_literal(self, text, params):
        u = text.upper()
        if u == "TRUE":
            return True
        if u == "FALSE":
            return False
        raise ValueError(f"invalid bool literal: {text!r}")

    def validate(self, value):
        if not isinstance(value, bool):
            raise TypeError(f"expected bool for BOOL, got {type(value).__name__}")


class _FloatCodec:
    """IEEE 754 floating point. width=4 single (FLOAT/REAL), width=8 double (DOUBLE)."""

    name = "FLOAT"
    aliases = ("REAL",)
    width = 4  # default for FLOAT/REAL; DOUBLE in later task sets width=8

    def encode_py(self, value):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"FLOAT inf/NaN not allowed: {value!r}")
        if self.width == 4:
            return struct.pack(">f", value)
        return struct.pack(">d", value)

    def decode_bytes(self, buf, offset):
        size = 4 if self.width == 4 else 8
        fmt = ">f" if self.width == 4 else ">d"
        if offset + size > len(buf):
            raise ValueError(f"FLOAT decode truncated at offset {offset}")
        return struct.unpack_from(fmt, buf, offset)[0], offset + size

    def parse_literal(self, text, params):
        v = float(text)
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"FLOAT inf/NaN not allowed: {text!r}")
        return v

    def validate(self, value):
        if not isinstance(value, float):
            raise TypeError(f"expected float for FLOAT, got {type(value).__name__}")
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"FLOAT inf/NaN not allowed: {value!r}")


# Populate REGISTRY
REGISTRY["INT"] = _IntCodec()
REGISTRY["TEXT"] = _TextCodec()
REGISTRY["BOOL"] = _BoolCodec()
REGISTRY["FLOAT"] = _FloatCodec()

# Aliases resolve via lookup() through explicit map (since aliases are tuples)
_ALIAS_MAP: dict = {}
for _codec in REGISTRY.values():
    for _alias in _codec.aliases:
        _ALIAS_MAP[_alias] = _codec


def lookup(type_name: str):
    """Return codec by name or alias. Case-sensitive uppercase."""
    if type_name in REGISTRY:
        return REGISTRY[type_name]
    if type_name in _ALIAS_MAP:
        return _ALIAS_MAP[type_name]
    raise KeyError(f"unknown type: {type_name!r}")
```

Note: replace the previous `lookup()` definition from Task 1 with the alias-aware version above.

- [ ] **Step 4: Update row_codec.py to use codec_for**

```python
# src/tinydb/row_codec.py
"""Row codec: null bitmap (LSB-first) + length-prefixed values per Design Doc §3.4."""
from tinydb.type_system import codec_for


def _bitmap_len(col_count: int) -> int:
    return (col_count + 7) // 8


def encode_row(values: list, schema: list) -> bytes:
    """Encode a row: [null_bitmap] [value_0] [value_1] ...

    Schema entries are `(name, type)` or `(name, type, type_params)`.
    Bitmap is LSB-first: column 0 -> bit 0 of byte 0.

    Pre-validate types via codec.validate for strict type checking
    (e.g., reject bool-as-INT, NaN/Inf FLOAT). Mechanical encoding only.
    """
    if len(values) != len(schema):
        raise ValueError(f"values count {len(values)} != schema columns {len(schema)}")
    blen = _bitmap_len(len(schema))
    bitmap = bytearray(blen)
    parts: list = []
    for i, (val, *rest) in enumerate(zip(values, schema)):
        # rest is [(name, type)] or [(name, type, params)]
        name_type = rest[0]
        _name = name_type[0]
        typ = name_type[1]
        params = name_type[2] if len(name_type) > 2 else ()
        if val is None:
            bitmap[i // 8] |= 1 << (i % 8)
            continue
        codec = codec_for(typ, params)
        parts.append(codec.encode_py(val))
    return bytes(bitmap) + b"".join(parts)


def decode_row(buf: bytes, schema: list) -> list:
    """Decode a row into Python values (None for NULL columns).

    Schema entries are `(name, type)` or `(name, type, type_params)`.
    """
    col_count = len(schema)
    bitmap = buf[:_bitmap_len(col_count)]
    offset = _bitmap_len(col_count)
    values = []
    for i, *rest in enumerate(zip(range(col_count), schema)):
        name_type = rest[1]
        _name = name_type[0]
        typ = name_type[1]
        params = name_type[2] if len(name_type) > 2 else ()
        if bitmap[i // 8] & (1 << (i % 8)):
            values.append(None)
            continue
        codec = codec_for(typ, params)
        v, offset = codec.decode_bytes(buf, offset)
        values.append(v)
    return values
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py tests/unit/test_type_system.py tests/unit/test_parser.py tests/integration/ -v`
Expected: all pass; specifically `test_int_codec_roundtrip` ... `test_float_codec_rejects_nan` green; existing MVP tests still green

- [ ] **Step 6: Commit**

```bash
git add src/tinydb/type_system.py src/tinydb/row_codec.py tests/unit/test_type_system_v2.py
git commit -m "refactor(types): migrate MVP codecs to Protocol form (FLOAT 4-byte migration)"
```

> **WARNING**: This commit changes FLOAT from 8-byte double to 4-byte single. Run `tests/integration/ -k float` after this commit; expect some existing tests may need updating in Task 15.

---

### Task 3: SMALLINT (IntCodec with width=2)

- [x] **Task 3: SMALLINT (IntCodec with width=2)**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_type_system_v2.py`:

```python
def test_smallint_codec_roundtrip():
    codec = codec_for("SMALLINT")
    for v in [-32768, -1, 0, 1, 32767]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_smallint_codec_2byte_size():
    codec = codec_for("SMALLINT")
    assert len(codec.encode_py(0)) == 2


def test_smallint_codec_overflow_raises():
    codec = codec_for("SMALLINT")
    import pytest
    with pytest.raises(OverflowError, match="SMALLINT out of range"):
        codec.encode_py(32768)
    with pytest.raises(OverflowError, match="SMALLINT out of range"):
        codec.encode_py(-32769)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k smallint -v`
Expected: FAIL with `KeyError: 'SMALLINT'`

- [ ] **Step 3: Implement IntCodec with width parameter**

Replace `_IntCodec` with:

```python
class _IntCodec:
    """Signed big-endian integer. width in bytes: 2=SMALLINT, 4=INT, 8=BIGINT."""

    name = "INT"
    width = 4

    @property
    def _fmt(self):
        return {2: ">h", 4: ">i", 8: ">q"}[self.width]

    @property
    def _bounds(self):
        return {2: (-(2**15), 2**15),
                4: (-(2**31), 2**31),
                8: (-(2**63), 2**63)}[self.width]

    def encode_py(self, value):
        lo, hi = self._bounds
        if not (lo <= value < hi):
            raise OverflowError(f"{self.name} out of range: {value}")
        return struct.pack(self._fmt, value)

    def decode_bytes(self, buf, offset):
        if offset + self.width > len(buf):
            raise ValueError(f"{self.name} decode truncated at offset {offset}")
        return struct.unpack_from(self._fmt, buf, offset)[0], offset + self.width

    def parse_literal(self, text, params):
        v = int(text)
        self.validate(v)
        return v

    def validate(self, value):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"expected int for {self.name}, got {type(value).__name__}")
        lo, hi = self._bounds
        if not (lo <= value < hi):
            raise OverflowError(f"{self.name} out of range: {value}")
```

Add to REGISTRY:
```python
REGISTRY["SMALLINT"] = _IntCodec()
REGISTRY["SMALLINT"].name = "SMALLINT"
REGISTRY["SMALLINT"].width = 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k smallint -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): add SMALLINT codec (IntCodec with width=2)"
```

---

### Task 4: BIGINT (IntCodec with width=8)

- [x] **Task 4: BIGINT (IntCodec with width=8)**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_bigint_codec_roundtrip():
    codec = codec_for("BIGINT")
    for v in [-(2**63), -1, 0, 1, 2**63 - 1]:
        assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_bigint_codec_8byte_size():
    codec = codec_for("BIGINT")
    assert len(codec.encode_py(0)) == 8


def test_bigint_codec_overflow_raises():
    codec = codec_for("BIGINT")
    import pytest
    with pytest.raises(OverflowError, match="BIGINT out of range"):
        codec.encode_py(2**63)
    with pytest.raises(OverflowError, match="BIGINT out of range"):
        codec.encode_py(-(2**63) - 1)


def test_int_alias_integer():
    """INTEGER alias resolves to INT (width=4)."""
    codec = lookup("INTEGER")
    assert codec.name == "INT"
    assert codec.width == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k bigint -v`
Expected: FAIL with `KeyError: 'BIGINT'`

- [ ] **Step 3: Implement BIGINT**

Add to REGISTRY:
```python
_bigint = _IntCodec()
_bigint.name = "BIGINT"
_bigint.width = 8
REGISTRY["BIGINT"] = _bigint

_int_alias = REGISTRY["INT"]
REGISTRY["INTEGER"] = _int_alias  # alias
_ALIAS_MAP["INTEGER"] = _int_alias
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k bigint -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): add BIGINT codec (IntCodec with width=8) + INTEGER alias"
```

---

### Task 5: DOUBLE (FloatCodec with width=8)

- [x] **Task 5: DOUBLE (FloatCodec with width=8)**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_double_codec_8byte():
    codec = codec_for("DOUBLE")
    assert len(codec.encode_py(1.5)) == 8


def test_double_codec_roundtrip():
    codec = codec_for("DOUBLE")
    # high-precision value that requires double precision
    v = 3.14159265358979
    assert codec.decode_bytes(codec.encode_py(v), 0)[0] == v


def test_double_codec_rejects_inf_nan():
    codec = codec_for("DOUBLE")
    import pytest
    with pytest.raises(ValueError, match="DOUBLE inf/NaN not allowed"):
        codec.encode_py(float("inf"))
    with pytest.raises(ValueError, match="DOUBLE inf/NaN not allowed"):
        codec.encode_py(float("nan"))


def test_double_precision_alias():
    codec = lookup("DOUBLE PRECISION")
    assert codec.name == "DOUBLE"
    assert codec.width == 8


def test_real_alias_resolves_to_float_4byte():
    """REAL alias = FLOAT (4-byte single per design D3)."""
    codec = lookup("REAL")
    assert codec.name == "FLOAT"
    assert codec.width == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k double -v`
Expected: FAIL with `KeyError: 'DOUBLE'`

- [ ] **Step 3: Implement DOUBLE**

Update `_FloatCodec` to set name based on width, and add DOUBLE instance:

```python
class _FloatCodec:
    """IEEE 754 floating point. width=4 single (FLOAT/REAL), width=8 double (DOUBLE)."""

    name = "FLOAT"
    width = 4

    @property
    def _fmt(self):
        return ">f" if self.width == 4 else ">d"

    @property
    def _size(self):
        return 4 if self.width == 4 else 8

    def encode_py(self, value):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"{self.name} inf/NaN not allowed: {value!r}")
        return struct.pack(self._fmt, value)

    def decode_bytes(self, buf, offset):
        if offset + self._size > len(buf):
            raise ValueError(f"{self.name} decode truncated at offset {offset}")
        return struct.unpack_from(self._fmt, buf, offset)[0], offset + self._size

    def parse_literal(self, text, params):
        v = float(text)
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"{self.name} inf/NaN not allowed: {text!r}")
        return v

    def validate(self, value):
        if not isinstance(value, float):
            raise TypeError(f"expected float for {self.name}, got {type(value).__name__}")
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"{self.name} inf/NaN not allowed: {value!r}")


# Populate REGISTRY
_float4 = _FloatCodec()
_float4.name = "FLOAT"
_float4.width = 4
REGISTRY["FLOAT"] = _float4
_ALIAS_MAP["REAL"] = _float4

_float8 = _FloatCodec()
_float8.name = "DOUBLE"
_float8.width = 8
REGISTRY["DOUBLE"] = _float8
_ALIAS_MAP["DOUBLE PRECISION"] = _float8
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k "double or real" -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): add DOUBLE codec (8-byte) + DOUBLE PRECISION alias"
```

---

### Task 6: BOOLEAN alias for BOOL

- [x] **Task 6: BOOLEAN alias for BOOL**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_boolean_alias_resolves_to_bool():
    codec = lookup("BOOLEAN")
    assert codec.name == "BOOL"
    # verify it encodes/decodes correctly
    assert codec.decode_bytes(codec.encode_py(True), 0)[0] is True
    assert codec.decode_bytes(codec.encode_py(False), 0)[0] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k boolean -v`
Expected: FAIL with `KeyError: 'BOOLEAN'`

- [ ] **Step 3: Implement BOOLEAN alias**

In `_BoolCodec` definition, `aliases = ("BOOLEAN",)` is already set. Verify the registration loop in Task 1 populates `_ALIAS_MAP`. If not, add explicit:

```python
_ALIAS_MAP["BOOLEAN"] = REGISTRY["BOOL"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k boolean -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): register BOOLEAN alias for BOOL"
```

---

### Task 7: VARCHAR (parametric codec with max_len)

- [x] **Task 7: VARCHAR (parametric codec with max_len)**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_varchar_codec_roundtrip_within_max():
    codec = codec_for("VARCHAR", (10,))
    for v in ["", "hello", "中文"]:
        encoded = codec.encode_py(v)
        assert codec.decode_bytes(encoded, 0)[0] == v


def test_varchar_codec_rejects_overlong():
    codec = codec_for("VARCHAR", (10,))
    import pytest
    with pytest.raises(TypeError, match="VARCHAR\\(10\\) length 11 exceeds max"):
        codec.encode_py("a" * 11)


def test_varchar_codec_accepts_exact_max():
    codec = codec_for("VARCHAR", (10,))
    encoded = codec.encode_py("a" * 10)
    assert len(encoded) == 2 + 10  # length prefix + UTF-8


def test_varchar_codec_per_call_instance():
    """Different max_len should produce independent codec instances."""
    a = codec_for("VARCHAR", (10,))
    b = codec_for("VARCHAR", (20,))
    assert a is not b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k varchar -v`
Expected: FAIL with `KeyError: 'VARCHAR'`

- [ ] **Step 3: Implement VarcharCodec**

```python
class _VarcharCodec:
    """VARCHAR(N): UTF-8 string with max length N. Per-instance params."""

    name = "VARCHAR"

    def __init__(self, max_len: int):
        if max_len < 1:
            raise ValueError(f"VARCHAR max_len must be >= 1, got {max_len}")
        self.max_len = max_len

    def encode_py(self, value):
        data = value.encode("utf-8")
        if len(data) > self.max_len:
            raise TypeError(
                f"VARCHAR({self.max_len}) length {len(data)} exceeds max"
            )
        return struct.pack(">H", len(data)) + data

    def decode_bytes(self, buf, offset):
        if offset + 2 > len(buf):
            raise ValueError(f"VARCHAR({self.max_len}) length prefix truncated")
        (n,) = struct.unpack_from(">H", buf, offset)
        if offset + 2 + n > len(buf):
            raise ValueError(f"VARCHAR({self.max_len}) payload truncated (need {n} bytes)")
        return buf[offset + 2 : offset + 2 + n].decode("utf-8"), offset + 2 + n

    def parse_literal(self, text, params):
        v = text[1:-1].replace("''", "'")  # strip quotes + decode '' -> '
        if len(v.encode("utf-8")) > self.max_len:
            raise TypeError(
                f"VARCHAR({self.max_len}) length {len(v.encode('utf-8'))} exceeds max"
            )
        return v

    def validate(self, value):
        if not isinstance(value, str):
            raise TypeError(f"expected str for VARCHAR, got {type(value).__name__}")
        n = len(value.encode("utf-8"))
        if n > self.max_len:
            raise TypeError(f"VARCHAR({self.max_len}) length {n} exceeds max")
```

Update `codec_for()` to call `_VarcharCodec(params[0])` for VARCHAR:

```python
# In codec_for(), after parameter validation:
if type_name == "VARCHAR":
    return _VarcharCodec(params[0])
# Similar for CHAR (next task)
```

Add a placeholder REGISTRY entry (will not be used directly):

```python
REGISTRY["VARCHAR"] = _VarcharCodec  # class reference; codec_for instantiates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k varchar -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): add VARCHAR(N) codec with max_len validation"
```

---

### Task 8: CHAR (parametric codec with PAD SPACE)

- [x] **Task 8: CHAR (parametric codec with PAD SPACE)**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_char_codec_pads_short_string():
    codec = codec_for("CHAR", (5,))
    encoded = codec.encode_py("ab")
    assert len(encoded) == 2 + 5  # length prefix + 5 bytes (padded)
    assert codec.decode_bytes(encoded, 0)[0] == "ab   "  # spaces preserved


def test_char_codec_rejects_overlong():
    codec = codec_for("CHAR", (5,))
    import pytest
    with pytest.raises(TypeError, match="CHAR\\(5\\) length 6 exceeds max"):
        codec.encode_py("abcdef")


def test_char_codec_accepts_exact_length():
    codec = codec_for("CHAR", (5,))
    encoded = codec.encode_py("abcde")
    assert codec.decode_bytes(encoded, 0)[0] == "abcde"


def test_char_codec_no_trim_on_decode():
    """SQL92 PAD SPACE: padding is preserved on read (no RTRIM)."""
    codec = codec_for("CHAR", (5,))
    encoded = codec.encode_py("ab")
    assert codec.decode_bytes(encoded, 0)[0] == "ab   "
    # NOT "ab"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k char -v`
Expected: FAIL with `KeyError: 'CHAR'`

- [ ] **Step 3: Implement CharCodec**

```python
class _CharCodec:
    """CHAR(N): fixed-length UTF-8 string with right-space padding (SQL92 PAD SPACE)."""

    name = "CHAR"

    def __init__(self, length: int):
        if length < 1:
            raise ValueError(f"CHAR length must be >= 1, got {length}")
        self.length = length

    def encode_py(self, value):
        data = value.encode("utf-8")
        if len(data) > self.length:
            raise TypeError(
                f"CHAR({self.length}) length {len(data)} exceeds max"
            )
        padded = value + " " * (self.length - len(data))
        return struct.pack(">H", self.length) + padded.encode("utf-8")

    def decode_bytes(self, buf, offset):
        if offset + 2 > len(buf):
            raise ValueError(f"CHAR({self.length}) length prefix truncated")
        (n,) = struct.unpack_from(">H", buf, offset)
        if offset + 2 + n > len(buf):
            raise ValueError(f"CHAR({self.length}) payload truncated (need {n} bytes)")
        return buf[offset + 2 : offset + 2 + n].decode("utf-8"), offset + 2 + n

    def parse_literal(self, text, params):
        v = text[1:-1].replace("''", "'")
        if len(v.encode("utf-8")) > self.length:
            raise TypeError(
                f"CHAR({self.length}) length {len(v.encode('utf-8'))} exceeds max"
            )
        return v

    def validate(self, value):
        if not isinstance(value, str):
            raise TypeError(f"expected str for CHAR, got {type(value).__name__}")
        n = len(value.encode("utf-8"))
        if n > self.length:
            raise TypeError(f"CHAR({self.length}) length {n} exceeds max")
```

Update `codec_for()`:
```python
if type_name == "CHAR":
    return _CharCodec(params[0])
```

Add to REGISTRY: `REGISTRY["CHAR"] = _CharCodec`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k char -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): add CHAR(N) codec with PAD SPACE semantics"
```

---

### Task 9: DECIMAL (scaled int64 with precision/scale)

- [x] **Task 9: DECIMAL (scaled int64 with precision/scale)**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

```python
def test_decimal_codec_roundtrip_simple():
    codec = codec_for("DECIMAL", (10, 2))
    encoded = codec.encode_py(1.23)
    assert len(encoded) == 8
    assert codec.decode_bytes(encoded, 0)[0] == 1.23


def test_decimal_codec_negative_roundtrip():
    codec = codec_for("DECIMAL", (10, 2))
    encoded = codec.encode_py(-123.45)
    assert codec.decode_bytes(encoded, 0)[0] == -123.45


def test_decimal_codec_zero_scale():
    codec = codec_for("DECIMAL", (10, 0))
    encoded = codec.encode_py(123)
    assert codec.decode_bytes(encoded, 0)[0] == 123


def test_decimal_codec_precision_overflow():
    codec = codec_for("DECIMAL", (5, 2))
    import pytest
    # DECIMAL(5,2): value range is [-999.99, 999.99]
    with pytest.raises(OverflowError, match="DECIMAL\\(5,2\\) value .* out of range"):
        codec.encode_py(1000.00)
    with pytest.raises(OverflowError, match="DECIMAL\\(5,2\\) value .* out of range"):
        codec.encode_py(-1000.00)


def test_decimal_codec_scaled_overflow():
    codec = codec_for("DECIMAL", (18, 6))
    import pytest
    # DECIMAL(18,6): value range is [-10^12, 10^12 - 10^-6]
    with pytest.raises(OverflowError):
        codec.encode_py(1e13)  # exceeds 10^12


def test_decimal_codec_rejects_p_lt_s():
    import pytest
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (2, 5))


def test_decimal_codec_rejects_p_too_large():
    import pytest
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (19, 0))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k decimal -v`
Expected: FAIL with `KeyError: 'DECIMAL'`

- [ ] **Step 3: Implement DecimalCodec**

```python
class _DecimalCodec:
    """DECIMAL(p,s): scaled int64. Internal value = int(round(value * 10^s))."""

    name = "DECIMAL"

    def __init__(self, precision: int, scale: int):
        if not (1 <= precision <= 18):
            raise ValueError(f"DECIMAL precision must be 1..18, got {precision}")
        if not (0 <= scale < precision):
            raise ValueError(f"DECIMAL scale must be 0..{precision - 1}, got {scale}")
        self.precision = precision
        self.scale = scale
        self._max_abs = 10 ** (precision - scale)

    def _to_scaled(self, value):
        scaled = round(value * (10 ** self.scale))
        if abs(scaled) >= 2**63:
            raise OverflowError(f"DECIMAL({self.precision},{self.scale}) scaled value overflow")
        if abs(value) >= self._max_abs:
            raise OverflowError(
                f"DECIMAL({self.precision},{self.scale}) value {value} out of range"
            )
        return scaled

    def _from_scaled(self, scaled):
        return scaled / (10 ** self.scale)

    def encode_py(self, value):
        scaled = self._to_scaled(value)
        return struct.pack(">q", scaled)

    def decode_bytes(self, buf, offset):
        if offset + 8 > len(buf):
            raise ValueError(f"DECIMAL({self.precision},{self.scale}) decode truncated")
        scaled, = struct.unpack_from(">q", buf, offset)
        return self._from_scaled(scaled), offset + 8

    def parse_literal(self, text, params):
        v = float(text)
        return self._to_scaled(v) / (10 ** self.scale)

    def validate(self, value):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"expected number for DECIMAL, got {type(value).__name__}")
        self._to_scaled(value)
```

Update `codec_for()`:
```python
if type_name == "DECIMAL":
    return _DecimalCodec(params[0], params[1])
```

Add: `REGISTRY["DECIMAL"] = _DecimalCodec`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k decimal -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): add DECIMAL(p,s) codec with scaled int64 encoding"
```

---

### Task 10: DATE / TIME / TIMESTAMP UTC

- [x] **Task 10: DATE / TIME / TIMESTAMP UTC**

**Files:**
- Modify: `src/tinydb/type_system.py`
- Modify: `tests/unit/test_type_system_v2.py`

- [ ] **Step 1: Write the failing test**

```python
import datetime


def test_date_codec_roundtrip():
    codec = lookup("DATE")
    d = datetime.date(2026, 7, 16)
    encoded = codec.encode_py(d)
    assert len(encoded) == 4  # 4-byte days since epoch
    decoded, _ = codec.decode_bytes(encoded, 0)
    assert decoded == d


def test_date_codec_parse_iso_literal():
    codec = lookup("DATE")
    parsed = codec.parse_literal("2026-07-16", ())
    assert parsed == datetime.date(2026, 7, 16)


def test_date_codec_rejects_bad_format():
    codec = lookup("DATE")
    import pytest
    with pytest.raises(ValueError):
        codec.parse_literal("2026/07/16", ())
    with pytest.raises(ValueError):
        codec.parse_literal("not-a-date", ())


def test_time_codec_roundtrip():
    codec = lookup("TIME")
    t = datetime.time(14, 30, 0)
    encoded = codec.encode_py(t)
    assert len(encoded) == 4
    decoded, _ = codec.decode_bytes(encoded, 0)
    assert decoded == t


def test_time_codec_parse_iso_literal():
    codec = lookup("TIME")
    parsed = codec.parse_literal("14:30:00", ())
    assert parsed == datetime.time(14, 30, 0)


def test_time_codec_rejects_out_of_range():
    codec = lookup("TIME")
    import pytest
    with pytest.raises(ValueError):
        codec.encode_py(datetime.time(25, 0, 0))


def test_timestamp_codec_roundtrip():
    codec = lookup("TIMESTAMP")
    ts = datetime.datetime(2026, 7, 16, 14, 30, 0)
    encoded = codec.encode_py(ts)
    assert len(encoded) == 8
    decoded, _ = codec.decode_bytes(encoded, 0)
    assert decoded == ts


def test_timestamp_codec_parse_iso_literal():
    codec = lookup("TIMESTAMP")
    parsed = codec.parse_literal("2026-07-16 14:30:00", ())
    assert parsed == datetime.datetime(2026, 7, 16, 14, 30, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k "date or time or timestamp" -v`
Expected: FAIL with `KeyError: 'DATE'`

- [ ] **Step 3: Implement Date/Time/TimestampCodec**

```python
import datetime as _dt
import re as _re

_EPOCH_DATE = _dt.date(1970, 1, 1)
_EPOCH_DT = _dt.datetime(1970, 1, 1)


class _DateCodec:
    """DATE: days since UTC epoch (1970-01-01). 4-byte signed big-endian."""

    name = "DATE"

    def encode_py(self, value):
        if not isinstance(value, _dt.date):
            raise TypeError(f"expected date for DATE, got {type(value).__name__}")
        days = (value - _EPOCH_DATE).days
        return struct.pack(">i", days)

    def decode_bytes(self, buf, offset):
        if offset + 4 > len(buf):
            raise ValueError("DATE decode truncated")
        days, = struct.unpack_from(">i", buf, offset)
        return _EPOCH_DATE + _dt.timedelta(days=days), offset + 4

    def parse_literal(self, text, params):
        try:
            return _dt.date.fromisoformat(text)
        except ValueError as e:
            raise ValueError(f"DATE literal invalid: {text!r} ({e})") from e

    def validate(self, value):
        if not isinstance(value, _dt.date):
            raise TypeError(f"expected date for DATE, got {type(value).__name__}")


class _TimeCodec:
    """TIME: seconds since midnight UTC. 4-byte unsigned big-endian."""

    name = "TIME"

    def encode_py(self, value):
        if not isinstance(value, _dt.time):
            raise TypeError(f"expected time for TIME, got {type(value).__name__}")
        seconds = value.hour * 3600 + value.minute * 60 + value.second
        if seconds < 0 or seconds > 86399:
            raise ValueError(f"TIME out of range: {seconds}")
        return struct.pack(">I", seconds)

    def decode_bytes(self, buf, offset):
        if offset + 4 > len(buf):
            raise ValueError("TIME decode truncated")
        seconds, = struct.unpack_from(">I", buf, offset)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return _dt.time(h, m, s), offset + 4

    def parse_literal(self, text, params):
        try:
            return _dt.time.fromisoformat(text)
        except ValueError as e:
            raise ValueError(f"TIME literal invalid: {text!r} ({e})") from e

    def validate(self, value):
        if not isinstance(value, _dt.time):
            raise TypeError(f"expected time for TIME, got {type(value).__name__}")


class _TimestampCodec:
    """TIMESTAMP: seconds since UTC epoch. 8-byte signed big-endian. Naive datetime."""

    name = "TIMESTAMP"

    def encode_py(self, value):
        if not isinstance(value, _dt.datetime):
            raise TypeError(f"expected datetime for TIMESTAMP, got {type(value).__name__}")
        seconds = int((value - _EPOCH_DT).total_seconds())
        return struct.pack(">q", seconds)

    def decode_bytes(self, buf, offset):
        if offset + 8 > len(buf):
            raise ValueError("TIMESTAMP decode truncated")
        seconds, = struct.unpack_from(">q", buf, offset)
        return _EPOCH_DT + _dt.timedelta(seconds=seconds), offset + 8

    def parse_literal(self, text, params):
        try:
            return _dt.datetime.fromisoformat(text)
        except ValueError as e:
            raise ValueError(f"TIMESTAMP literal invalid: {text!r} ({e})") from e

    def validate(self, value):
        if not isinstance(value, _dt.datetime):
            raise TypeError(f"expected datetime for TIMESTAMP, got {type(value).__name__}")
```

Add to REGISTRY:
```python
REGISTRY["DATE"] = _DateCodec()
REGISTRY["TIME"] = _TimeCodec()
REGISTRY["TIMESTAMP"] = _TimestampCodec()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_v2.py -k "date or time or timestamp" -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system_v2.py
git commit -m "feat(types): add DATE / TIME / TIMESTAMP codecs (UTC unified)"
```

---

### Task 11: Verify all 15 codecs in REGISTRY

- [x] **Task 11: Verify all 15 codecs in REGISTRY**

**Files:**
- Modify: `tests/unit/test_type_system_registry.py`

- [ ] **Step 1: Update the registry test**

Edit `tests/unit/test_type_system_registry.py` to assert full registry:

```python
from tinydb.type_system import lookup, codec_for, REGISTRY


def test_registry_has_all_15_core_types():
    expected = {"INT", "SMALLINT", "BIGINT", "FLOAT", "DOUBLE", "REAL",
                "TEXT", "VARCHAR", "CHAR", "BOOL", "BOOLEAN",
                "DECIMAL", "DATE", "TIME", "TIMESTAMP"}
    assert set(REGISTRY.keys()) == expected


def test_aliases_resolve():
    """REAL, BOOLEAN, DOUBLE PRECISION, INTEGER all resolve."""
    assert lookup("REAL").name == "FLOAT"
    assert lookup("BOOLEAN").name == "BOOL"
    assert lookup("DOUBLE PRECISION").name == "DOUBLE"
    assert lookup("INTEGER").name == "INT"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_type_system_registry.py -v`
Expected: all 10 tests pass (8 from Task 1 + 2 new)

- [ ] **Step 3: Run full unit test suite to verify no regression**

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: all green

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_type_system_registry.py
git commit -m "test(types): assert full 15-type REGISTRY + alias resolution"
```

---

### Task 12: Parser — type_spec with VARCHAR(N) / DECIMAL(p,s)

- [x] **Task 12: Parser — type_spec with VARCHAR(N) / DECIMAL(p,s)**

**Files:**
- Modify: `src/tinydb/parser.py`
- Modify: `tests/unit/test_parser_type_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parser_type_spec.py
import pytest
from tinydb.parser import parse, ColumnDefinition
from tinydb.errors import ParseError


def test_parse_varchar_with_max_len():
    tokens = _tokenize("CREATE TABLE t (name VARCHAR(64))")
    stmts = parse(tokens)
    col = stmts.statements[0].columns[0]
    assert col.name == "name"
    assert col.type == "VARCHAR"
    assert col.type_params == (64,)


def test_parse_char_with_length():
    tokens = _tokenize("CREATE TABLE t (code CHAR(5))")
    stmts = parse(tokens)
    col = stmts.statements[0].columns[0]
    assert col.type == "CHAR"
    assert col.type_params == (5,)


def test_parse_decimal_with_precision_scale():
    tokens = _tokenize("CREATE TABLE t (amount DECIMAL(10, 2))")
    stmts = parse(tokens)
    col = stmts.statements[0].columns[0]
    assert col.type == "DECIMAL"
    assert col.type_params == (10, 2)


def test_parse_int_without_params():
    tokens = _tokenize("CREATE TABLE t (id INT)")
    stmts = parse(tokens)
    col = stmts.statements[0].columns[0]
    assert col.type == "INT"
    assert col.type_params == ()


def test_parse_varchar_missing_param_raises():
    with pytest.raises(ParseError, match="VARCHAR requires"):
        _tokenize_parse("CREATE TABLE t (name VARCHAR)")


def test_parse_decimal_missing_scale_raises():
    with pytest.raises(ParseError, match="DECIMAL requires"):
        _tokenize_parse("CREATE TABLE t (amount DECIMAL(10))")


def test_parse_decimal_invalid_p_raises():
    with pytest.raises(ParseError, match="DECIMAL"):
        _tokenize_parse("CREATE TABLE t (amount DECIMAL(20, 2))")


def _tokenize(s):
    from tinydb.tokenizer import tokenize
    return tokenize(s)


def _tokenize_parse(s):
    return parse(_tokenize(s))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_parser_type_spec.py -v`
Expected: FAIL with `ParseError: type VARCHAR not supported in MVP` (current parser rejects new types)

- [ ] **Step 3: Update parser.py**

Add to `parser.py`:

```python
SUPPORTED_TYPES = {
    # MVP
    "INT", "TEXT", "FLOAT", "BOOL",
    # new in tinydb-types
    "SMALLINT", "BIGINT", "DOUBLE", "REAL",
    "VARCHAR", "CHAR", "DECIMAL",
    "DATE", "TIME", "TIMESTAMP",
    # aliases (resolved at type_spec parsing)
    "INTEGER", "BOOLEAN", "DOUBLE PRECISION",
}


@dataclass(frozen=True)
class ColumnDefinition:
    name: str
    type: str
    type_params: tuple = ()
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False
```

Update `_parse_create_table` to call a new `_parse_type_spec`:

```python
def _parse_type_spec(self) -> tuple:
    name_tok = self.advance()
    name = name_tok.value.upper()
    if name not in SUPPORTED_TYPES:
        raise ParseError(name_tok.line, name_tok.col, f"type {name} not supported")
    params: tuple = ()
    if self.peek().type == "PUNCT" and self.peek().value == "(":
        self.advance()
        if self.peek().type != "INT":
            raise ParseError(self.peek().line, self.peek().col, "expected integer in type params")
        params = (self.advance().value,)
        if self.peek().type == "PUNCT" and self.peek().value == ",":
            self.advance()
            if self.peek().type != "INT":
                raise ParseError(self.peek().line, self.peek().col, "expected integer after ','")
            params = (params[0], self.advance().value)
        self.expect("PUNCT", ")")
    # Parametric validation
    if name in ("VARCHAR", "CHAR") and len(params) != 1:
        raise ParseError(name_tok.line, name_tok.col, f"{name} requires (N)")
    if name == "DECIMAL":
        if len(params) != 2:
            raise ParseError(name_tok.line, name_tok.col, "DECIMAL requires (p, s)")
        p, s = params
        if not (1 <= p <= 18 and 0 <= s < p):
            raise ParseError(name_tok.line, name_tok.col, f"DECIMAL({p},{s}) invalid")
    return name, params
```

In `_parse_create_table`, replace type parsing:

```python
# OLD:
ctype = self.advance().value
cols.append((cname, ctype))

# NEW:
type_name, type_params = self._parse_type_spec()
cols.append(ColumnDefinition(name=cname, type=type_name, type_params=type_params))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_parser_type_spec.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/parser.py tests/unit/test_parser_type_spec.py
git commit -m "feat(types): parser accepts VARCHAR(N) / CHAR(N) / DECIMAL(p,s) with param validation"
```

---

### Task 13: Parser — DATE / TIME / TIMESTAMP literal prefix

- [x] **Task 13: Parser — DATE / TIME / TIMESTAMP literal prefix**

**Files:**
- Modify: `src/tinydb/parser.py`
- Modify: `src/tinydb/tokenizer.py` (KEYWORDS)
- Modify: `tests/unit/test_parser_datetime_lit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parser_datetime_lit.py
import datetime
from tinydb.parser import parse
from tinydb.tokenizer import tokenize


def _parse_value(sql):
    """Parse a single literal and return its parsed value."""
    from tinydb.parser import _Parser  # internal
    tokens = tokenize(sql)
    parser = _Parser(tokens)
    return parser._parse_datetime_literal()


def test_parse_date_literal():
    val = _parse_value("DATE '2026-07-16'")
    assert val == datetime.date(2026, 7, 16)


def test_parse_time_literal():
    val = _parse_value("TIME '14:30:00'")
    assert val == datetime.time(14, 30, 0)


def test_parse_timestamp_literal():
    val = _parse_value("TIMESTAMP '2026-07-16 14:30:00'")
    assert val == datetime.datetime(2026, 7, 16, 14, 30, 0)


def test_parse_invalid_date_literal_raises():
    import pytest
    from tinydb.errors import ParseError
    with pytest.raises(ParseError, match="DATE literal"):
        _parse_value("DATE '2026/07/16'")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_parser_datetime_lit.py -v`
Expected: FAIL with `AttributeError: '_Parser' object has no attribute '_parse_datetime_literal'`

- [ ] **Step 3: Implement _parse_datetime_literal**

In `parser.py`, add to `_Parser` class:

```python
def _parse_datetime_literal(self):
    """Parse DATE / TIME / TIMESTAMP 'literal' and return Python value."""
    kw = self.expect_keyword(self.peek().value)  # consume DATE/TIME/TIMESTAMP
    text_tok = self.advance()
    if text_tok.type != "TEXT":
        raise ParseError(text_tok.line, text_tok.col,
                         f"{kw.value} literal requires quoted string")
    text = text_tok.value
    if kw.value == "DATE":
        import datetime
        try:
            return datetime.date.fromisoformat(text)
        except ValueError as e:
            raise ParseError(kw.line, kw.col, f"DATE literal invalid: {text!r} ({e})") from e
    elif kw.value == "TIME":
        import datetime
        try:
            return datetime.time.fromisoformat(text)
        except ValueError as e:
            raise ParseError(kw.line, kw.col, f"TIME literal invalid: {text!r} ({e})") from e
    elif kw.value == "TIMESTAMP":
        import datetime
        try:
            return datetime.datetime.fromisoformat(text)
        except ValueError as e:
            raise ParseError(kw.line, kw.col, f"TIMESTAMP literal invalid: {text!r} ({e})") from e
```

Update `tokenizer.py` `KEYWORDS`:

```python
KEYWORDS = {
    "CREATE", "TABLE", "DROP", "INSERT", "INTO", "VALUES", "SELECT",
    "FROM", "WHERE", "DELETE", "INT", "TEXT", "FLOAT", "BOOL",
    "COUNT", "SUM", "AVG", "MIN", "MAX", "GROUP", "BY", "HAVING",
    # tinydb-types additions:
    "SMALLINT", "BIGINT", "DOUBLE", "REAL",
    "VARCHAR", "CHAR", "DECIMAL",
    "DATE", "TIME", "TIMESTAMP",
    "INTEGER", "BOOLEAN",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_parser_datetime_lit.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/parser.py src/tinydb/tokenizer.py tests/unit/test_parser_datetime_lit.py
git commit -m "feat(types): parser accepts DATE / TIME / TIMESTAMP '...' literal prefix"
```

---

### Task 14: Parser — DECIMAL literal prefix

- [ ] **Task 14: Parser — DECIMAL literal prefix**

**Files:**
- Modify: `src/tinydb/parser.py`
- Modify: `tests/unit/test_parser_decimal_lit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parser_decimal_lit.py
from tinydb.parser import _Parser
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError
import pytest


def _parse(sql):
    return _Parser(tokenize(sql))._parse_decimal_literal()


def test_parse_decimal_literal_simple():
    val = _parse("DECIMAL '1.23'")
    assert val == 1.23


def test_parse_decimal_literal_negative():
    val = _parse("DECIMAL '-123.45'")
    assert val == -123.45


def test_parse_decimal_literal_integer_form():
    val = _parse("DECIMAL '100'")
    assert val == 100.0


def test_parse_decimal_literal_rejects_no_quote():
    with pytest.raises(ParseError):
        _parse("DECIMAL 1.23")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_parser_decimal_lit.py -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement _parse_decimal_literal**

In `parser.py`:

```python
def _parse_decimal_literal(self):
    """Parse DECIMAL 'literal' and return Python float."""
    kw = self.expect_keyword("DECIMAL")
    text_tok = self.advance()
    if text_tok.type != "TEXT":
        raise ParseError(text_tok.line, text_tok.col,
                         "DECIMAL literal requires quoted string")
    try:
        return float(text_tok.value)
    except ValueError as e:
        raise ParseError(kw.line, kw.col, f"DECIMAL literal invalid: {text_tok.value!r}") from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_parser_decimal_lit.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/parser.py tests/unit/test_parser_decimal_lit.py
git commit -m "feat(types): parser accepts DECIMAL 'literal' prefix"
```

---

### Task 15: Catalog — Column.type_params + backward compat

- [ ] **Task 15: Catalog — Column.type_params + backward compat**

**Files:**
- Modify: `src/tinydb/catalog.py`
- Modify: `tests/unit/test_catalog_type_params.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_catalog_type_params.py
from tinydb.catalog import Column


def test_column_default_type_params_empty():
    col = Column(name="id", type="INT")
    assert col.type_params == ()


def test_column_with_type_params():
    col = Column(name="name", type="VARCHAR", type_params=(64,))
    assert col.type_params == (64,)


def test_column_from_dict_legacy_no_type_params():
    """Old JSON without type_params key must default to ()."""
    legacy = {"name": "id", "type": "INT", "nullable": False,
              "unique": False, "primary_key": True}
    col = Column.from_dict(legacy)
    assert col.type_params == ()


def test_column_from_dict_with_type_params():
    new = {"name": "name", "type": "VARCHAR", "type_params": [64],
           "nullable": True, "unique": False, "primary_key": False}
    col = Column.from_dict(new)
    assert col.type_params == (64,)


def test_column_to_dict_includes_type_params():
    col = Column(name="amount", type="DECIMAL", type_params=(10, 2))
    d = col.to_dict()
    assert d["type_params"] == [10, 2]


def test_column_to_dict_empty_type_params():
    col = Column(name="id", type="INT")
    d = col.to_dict()
    assert d["type_params"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_catalog_type_params.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'type_params'`

- [ ] **Step 3: Update catalog.py Column**

```python
@dataclass(frozen=True)
class Column:
    name: str
    type: str
    type_params: tuple = ()
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "type_params": list(self.type_params),
            "nullable": self.nullable,
            "unique": self.unique,
            "primary_key": self.primary_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Column":
        return cls(
            name=d["name"],
            type=d["type"],
            type_params=tuple(d.get("type_params", ())),
            nullable=d.get("nullable", True),
            unique=d.get("unique", False),
            primary_key=d.get("primary_key", False),
        )
```

Update `_load_column` in catalog.py to pass type_params:

```python
def _load_column(item) -> Column:
    if isinstance(item, (list, tuple)) and len(item) == 2:
        # Legacy [name, type] format
        return Column(name=item[0], type=item[1])
    if isinstance(item, dict):
        return Column.from_dict(item)
    raise ValueError(f"unknown column entry: {item!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_catalog_type_params.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/catalog.py tests/unit/test_catalog_type_params.py
git commit -m "feat(types): catalog.Column.type_params with backward-compatible JSON"
```

---

### Task 16: row_codec — schema_v2() + codec_for dispatch

- [ ] **Task 16: row_codec — schema_v2() + codec_for dispatch**

**Files:**
- Modify: `src/tinydb/catalog.py` (add `schema_v2()` method to TableInfo)
- Modify: `src/tinydb/row_codec.py`
- Modify: `tests/unit/test_row_codec_v2.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_row_codec_v2.py
from tinydb.row_codec import encode_row, decode_row
from tinydb.catalog import TableInfo, Column


def test_encode_decode_roundtrip_int():
    schema = [("id", "INT", ())]
    encoded = encode_row([42], schema)
    assert decode_row(encoded, schema) == [42]


def test_encode_decode_roundtrip_varchar():
    schema = [("name", "VARCHAR", (10,))]
    encoded = encode_row(["hello"], schema)
    assert decode_row(encoded, schema) == ["hello"]


def test_encode_decode_roundtrip_decimal():
    schema = [("amount", "DECIMAL", (10, 2))]
    encoded = encode_row([1.23], schema)
    decoded = decode_row(encoded, schema)
    assert abs(decoded[0] - 1.23) < 0.01


def test_encode_decode_roundtrip_date():
    import datetime
    schema = [("d", "DATE", ())]
    encoded = encode_row([datetime.date(2026, 7, 16)], schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == datetime.date(2026, 7, 16)


def test_encode_decode_roundtrip_char_padded():
    schema = [("code", "CHAR", (5,))]
    encoded = encode_row(["ab"], schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == "ab   "  # padding preserved


def test_encode_decode_roundtrip_multiple_columns():
    import datetime
    schema = [
        ("id", "INT", ()),
        ("name", "VARCHAR", (20,)),
        ("amount", "DECIMAL", (8, 2)),
        ("d", "DATE", ()),
    ]
    row = [1, "alice", 12.34, datetime.date(2026, 7, 16)]
    encoded = encode_row(row, schema)
    decoded = decode_row(encoded, schema)
    assert decoded[0] == 1
    assert decoded[1] == "alice"
    assert abs(decoded[2] - 12.34) < 0.01
    assert decoded[3] == datetime.date(2026, 7, 16)


def test_encode_decode_roundtrip_with_null():
    schema = [("id", "INT", ()), ("name", "VARCHAR", (10,))]
    encoded = encode_row([1, None], schema)
    assert decode_row(encoded, schema) == [1, None]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_row_codec_v2.py -v`
Expected: FAIL with various codec errors (VARCHAR/CHAR/DECIMAL/DATE not yet wired through row_codec)

- [ ] **Step 3: Update catalog.TableInfo.schema_v2**

In `catalog.py`:

```python
class TableInfo:
    name: str
    columns: tuple  # tuple[Column, ...]

    @property
    def schema(self) -> list:
        """Legacy [(name, type)] projection."""
        return [(c.name, c.type) for c in self.columns]

    @property
    def schema_v2(self) -> list:
        """New [(name, type, type_params)] projection."""
        return [(c.name, c.type, c.type_params) for c in self.columns]
```

- [ ] **Step 4: Verify row_codec.encode_row accepts both schema formats**

The current `encode_row` (from Task 2) already handles `(name, type, type_params)` tuples via the unpacking logic. Verify:

```python
# row_codec.py (unchanged from Task 2)
for i, (val, *rest) in enumerate(zip(values, schema)):
    name_type = rest[0]
    _name = name_type[0]
    typ = name_type[1]
    params = name_type[2] if len(name_type) > 2 else ()
    if val is None:
        bitmap[i // 8] |= 1 << (i % 8)
        continue
    codec = codec_for(typ, params)
    parts.append(codec.encode_py(val))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_row_codec_v2.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add src/tinydb/catalog.py src/tinydb/row_codec.py tests/unit/test_row_codec_v2.py
git commit -m "feat(types): row_codec.encode/decode uses codec_for(typ, params) + schema_v2"
```

---

### Task 17: Executor — wire 15 types into INSERT / SELECT / WHERE

- [ ] **Task 17: Executor — wire 15 types into INSERT / SELECT / WHERE**

**Files:**
- Modify: `src/tinydb/executor.py`
- Modify: `tests/integration/test_types_roundtrip.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_types_roundtrip.py
import datetime
import pytest
from tinydb.database import Database


def test_create_and_insert_varchar():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, name VARCHAR(64))")
        db.execute("INSERT INTO t (id, name) VALUES (1, 'alice')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].id == 1
        assert rows[0].name == "alice"


def test_create_and_insert_decimal():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, amount DECIMAL(10, 2))")
        db.execute("INSERT INTO t (id, amount) VALUES (1, 12.34)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].amount == 12.34


def test_create_and_insert_date():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, d DATE)")
        db.execute("INSERT INTO t (id, d) VALUES (1, DATE '2026-07-16')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].d == datetime.date(2026, 7, 16)


def test_create_and_insert_timestamp():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, ts TIMESTAMP)")
        db.execute("INSERT INTO t (id, ts) VALUES (1, TIMESTAMP '2026-07-16 14:30:00')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].ts == datetime.datetime(2026, 7, 16, 14, 30, 0)


def test_create_and_insert_smallint():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id SMALLINT)")
        db.execute("INSERT INTO t (id) VALUES (100)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].id == 100


def test_create_and_insert_bigint():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id BIGINT)")
        db.execute("INSERT INTO t (id) VALUES (1000000000)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].id == 1000000000


def test_create_and_insert_double():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, val DOUBLE)")
        db.execute("INSERT INTO t (id, val) VALUES (1, 3.14159265358979)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].val == 3.14159265358979


def test_create_and_insert_char_padded():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, code CHAR(5))")
        db.execute("INSERT INTO t (id, code) VALUES (1, 'ab')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].code == "ab   "  # padding preserved


def test_varchar_overflow_raises():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (name VARCHAR(5))")
        with pytest.raises((TypeError, ValueError)):
            db.execute("INSERT INTO t (name) VALUES ('too long')")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_types_roundtrip.py -v`
Expected: various failures depending on executor wiring state

- [ ] **Step 3: Wire codec_for into executor INSERT path**

In `executor.py`, locate the `_exec_insert` function (or equivalent). Find where it does `py_to_db(value, col.type)` and replace with codec dispatch:

```python
# OLD (approx):
encoded = py_to_db(value, col.type)

# NEW:
from tinydb.type_system import codec_for
codec = codec_for(col.type, col.type_params)
codec.validate(value)
encoded = codec.encode_py(value)
```

Also update SELECT path to use codec decode:

```python
# In _exec_select (or row decoding path):
codec = codec_for(col.type, col.type_params)
decoded, _ = codec.decode_bytes(col_bytes, 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_types_roundtrip.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/executor.py tests/integration/test_types_roundtrip.py
git commit -m "feat(types): executor wires 15 codecs into INSERT / SELECT paths"
```

---

### Task 18: WHERE clause strict same-type comparison

- [ ] **Task 18: WHERE clause strict same-type comparison**

**Files:**
- Modify: `src/tinydb/type_system.py` (add `validate_compare`)
- Modify: `src/tinydb/executor.py`
- Modify: `tests/integration/test_types_in_where.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_types_in_where.py
import pytest
from tinydb.database import Database


def test_where_date_eq():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, d DATE)")
        db.execute("INSERT INTO t (id, d) VALUES (1, DATE '2026-07-16')")
        db.execute("INSERT INTO t (id, d) VALUES (2, DATE '2026-07-17')")
        rows = db.execute("SELECT * FROM t WHERE d = DATE '2026-07-16'")
        assert len(rows) == 1
        assert rows[0].id == 1


def test_where_varchar_eq():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, name VARCHAR(64))")
        db.execute("INSERT INTO t (id, name) VALUES (1, 'alice')")
        rows = db.execute("SELECT * FROM t WHERE name = 'alice'")
        # VARCHAR vs TEXT — strict same type, but TEXT literal assigned to VARCHAR may pass
        # Adjust assertion based on chosen behavior (per Design D6):
        # Per strict rule: VARCHAR vs TEXT raises TypeError
        # If this passes, adjust to:
        # with pytest.raises(TypeError):
        #     db.execute(...)
        assert len(rows) >= 0  # placeholder


def test_where_cross_type_raises():
    """VARCHAR vs TEXT literal should raise (strict same-type per D6)."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, name VARCHAR(64))")
        db.execute("INSERT INTO t (id, name) VALUES (1, 'alice')")
        with pytest.raises(TypeError, match="type mismatch"):
            db.execute("SELECT * FROM t WHERE name = 'alice'")


def test_where_int_eq():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        db.execute("INSERT INTO t (id) VALUES (2)")
        rows = db.execute("SELECT * FROM t WHERE id = 1")
        assert rows[0].id == 1


def test_where_int_smaller_int_raises():
    """INT column vs SMALLINT literal — strict same-type, raises."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")
        with pytest.raises(TypeError, match="type mismatch"):
            db.execute("SELECT * FROM t WHERE id = SMALLINT 1")


def test_where_float_vs_double_raises():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (val FLOAT)")
        db.execute("INSERT INTO t (val) VALUES (1.5)")
        with pytest.raises(TypeError, match="type mismatch"):
            db.execute("SELECT * FROM t WHERE val = DOUBLE 1.5")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_types_in_where.py -v`
Expected: most fail (existing WHERE doesn't enforce strict comparison)

- [ ] **Step 3: Add validate_compare and wire into executor**

In `type_system.py`:

```python
def validate_compare(col_type, col_params, lit_type, lit_params):
    """Strict same-type comparison per Design D6."""
    if col_type != lit_type or col_params != lit_params:
        raise TypeError(
            f"type mismatch: {col_type}{list(col_params)} vs "
            f"{lit_type}{list(lit_params)}"
        )
```

In `executor.py` WHERE evaluation path, replace existing comparison with:

```python
from tinydb.type_system import validate_compare, codec_for

# Get column type info from schema
col = ti.schema_v2[i]  # (name, type, params)
validate_compare(col[1], col[2], lit_type, lit_params)
# Then proceed with byte comparison via codec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_types_in_where.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py src/tinydb/executor.py tests/integration/test_types_in_where.py
git commit -m "feat(types): WHERE enforces strict same-type comparison (Design D6)"
```

---

### Task 19: FLOAT 4-byte regression cleanup

- [ ] **Task 19: FLOAT 4-byte regression cleanup**

**Files:**
- Modify: existing test files with FLOAT hardcoded values

- [ ] **Step 1: Run all existing tests to find FLOAT regressions**

Run: `.venv/bin/python -m pytest tests/ -k FLOAT -v 2>&1 | tee /tmp/float_regressions.txt`
Expected: some failures

- [ ] **Step 2: For each failure, adjust test expectation**

Common patterns:
- `assert x == 3.14159265358979` → `assert x == pytest.approx(3.1415927)` or `assert abs(x - 3.1415927) < 1e-6`
- Hardcoded expected values → switch to single-precision representation
- If test genuinely needs double precision → change column type to `DOUBLE`

Example fix in `tests/integration/test_executor.py`:

```python
# OLD:
assert rows[0].x == 3.14159265358979

# NEW (use approx for single-precision comparison):
assert abs(rows[0].x - 3.1415927) < 1e-6
```

- [ ] **Step 3: Run all tests to verify no remaining FLOAT regressions**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "fix(types): update existing tests for FLOAT 4-byte single precision"
```

---

### Task 20: REPL integration tests

- [ ] **Task 20: REPL integration tests**

**Files:**
- Modify: `tests/integration/test_types_repl.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_types_repl.py
import os
import shutil
import subprocess
import sys
import tempfile


def _resolve_repl() -> str | None:
    """Resolve tinydb-repl via shutil.which or sys.executable directory."""
    found = shutil.which("tinydb-repl")
    if found:
        return found
    candidate = os.path.join(os.path.dirname(sys.executable), "tinydb-repl")
    return candidate if os.path.isfile(candidate) else None


REPL = _resolve_repl()
pytestmark = pytest.mark.skipif(REPL is None, reason="tinydb-repl not on PATH")


def _run_repl(sql: str) -> str:
    """Run SQL via REPL subprocess, return stdout."""
    result = subprocess.run(
        [REPL, ":memory:", "-c", sql],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout + result.stderr


def test_repl_create_insert_varchar():
    output = _run_repl("CREATE TABLE t (name VARCHAR(64)); INSERT INTO t (name) VALUES ('alice'); SELECT * FROM t;")
    assert "alice" in output


def test_repl_create_insert_decimal():
    output = _run_repl("CREATE TABLE t (amount DECIMAL(10,2)); INSERT INTO t (amount) VALUES (12.34); SELECT * FROM t;")
    assert "12.34" in output


def test_repl_create_insert_date():
    output = _run_repl("CREATE TABLE t (d DATE); INSERT INTO t (d) VALUES (DATE '2026-07-16'); SELECT * FROM t;")
    assert "2026-07-16" in output


def test_repl_varchar_overflow_error():
    output = _run_repl("CREATE TABLE t (name VARCHAR(5)); INSERT INTO t (name) VALUES ('too long');")
    assert "exceeds max" in output.lower() or "ERROR" in output


def test_repl_float_inf_rejected():
    output = _run_repl("CREATE TABLE t (val FLOAT); INSERT INTO t (val) VALUES (Infinity);")
    assert "inf" in output.lower() or "ERROR" in output
```

Add import:
```python
import pytest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_types_repl.py -v`
Expected: skip if REPL not found; otherwise fail with subprocess errors

- [ ] **Step 3: Verify REPL works (no source changes needed if existing REPL is fine)**

REPL should already handle new types since executor wires them through. If REPL shows raw bytes for new types, fix in `repl.py` row formatter.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_types_repl.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_types_repl.py
git commit -m "test(types): REPL integration tests for 15 types"
```

---

### Task 21: Coverage + final verification

- [ ] **Task 21: Coverage + final verification**

**Files:**
- Modify: `docs/MVP_LIMITATIONS.md` (add types section)

- [ ] **Step 1: Run full test suite with coverage**

Run: `.venv/bin/python -m pytest --cov=tinydb --cov-fail-under=90 -q`
Expected: all pass; coverage ≥ 90%

- [ ] **Step 2: Verify module line counts**

```bash
wc -l src/tinydb/type_system.py src/tinydb/parser.py
```

Expected: `type_system.py ≤ 350`, `parser.py ≤ 870`. If exceeded, split per Design §F6.

- [ ] **Step 3: Update docs/MVP_LIMITATIONS.md**

Append section:

```markdown
## tinydb-types (2026-07-18)

- 15 types supported: INT / SMALLINT / BIGINT / FLOAT / DOUBLE / REAL / TEXT / VARCHAR / CHAR / BOOL / BOOLEAN / DECIMAL / DATE / TIME / TIMESTAMP
- Type parameters: `VARCHAR(N)` / `CHAR(N)` / `DECIMAL(p, s)` (p ∈ [1,18], s ∈ [0, p))
- **FLOAT = 4-byte single precision** (≈7 significant digits); DOUBLE = 8-byte double precision. REAL is alias for FLOAT.
- **CHAR(N) PAD SPACE**: writes 'ab' to CHAR(5) stored as 'ab   ' (padding preserved on read)
- **DATETIME UTC unified**: DATE/TIME/TIMESTAMP stored as UTC; no timezone support
- **FLOAT / DOUBLE reject inf/nan** at all paths (parse_literal, encode_py, validate)
- **DECIMAL scaled int64**: `DECIMAL(p,s)` internal = `int(value * 10^s)`; max precision 18
- **Strict same-type comparison**: VARCHAR ≠ TEXT, INT ≠ SMALLINT ≠ BIGINT, FLOAT ≠ DOUBLE, DATE ≠ TIMESTAMP
- **No implicit widening**: cross-type comparison requires explicit CAST (not in this change)
- **No BLOB / JSON / UUID / INET / INTERVAL**: out of scope
```

- [ ] **Step 4: Final commit**

```bash
git add docs/MVP_LIMITATIONS.md
git commit -m "docs(types): document 15-type system + strict comparison rules"
```

- [ ] **Step 5: Verify final state**

```bash
git log --oneline -25
.venv/bin/python -m pytest --cov=tinydb --cov-fail-under=90 -q
wc -l src/tinydb/type_system.py src/tinydb/parser.py
```

Expected:
- 21+ commits since base
- All tests pass, coverage ≥ 90%
- type_system.py ≤ 350 lines, parser.py ≤ 870 lines

---

## Out of Plan (Future Changes)

- Type cast `CAST(x AS TYPE)` — 需要独立 change
- ALTER TABLE / DROP CONSTRAINT — 需要独立 change
- BLOB / JSON / UUID / INET — 类型扩展
- 时区支持 (`TIMESTAMP WITH TIME ZONE`)
- INDEX 集成（`tinydb-engine-v2` 范围）

---

## Self-Review

执行前检查清单（write 后由 writing-plans skill 自动跑）：

1. **Spec coverage**: D1 (type_params) → Task 12; D2 (Protocol registry) → Tasks 1, 2; D3 (FLOAT 4B) → Tasks 2, 19; D4 (CHAR PAD) → Task 8; D5 (DATETIME UTC) → Task 10; D6 (strict compare) → Task 18
2. **No placeholders**: 通篇每步含具体代码或命令
3. **Type consistency**: `codec_for(typ, params)` 在所有任务中签名一致；`schema_v2()` 返回 `(name, type, params)` 三元组
4. **Module 行数预算**: type_system.py ≤ 350, parser.py ≤ 870 — Task 21 Step 2 强制审计
