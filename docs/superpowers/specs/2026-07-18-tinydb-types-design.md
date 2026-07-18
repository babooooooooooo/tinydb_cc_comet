---
comet_change: tinydb-types
role: technical-design
canonical_spec: openspec
status: final
---

# Design: tinydb-types

> **关联文档**：[proposal.md](../../../../openspec/changes/tinydb-types/proposal.md) · [design.md](../../../../openspec/changes/tinydb-types/design.md) · [tasks.md](../../../../openspec/changes/tinydb-types/tasks.md)
> **Brainstorm checkpoint**：[brainstorm-summary.md](../../../../openspec/changes/tinydb-types/.comet/handoff/brainstorm-summary.md)
> **Date**：2026-07-18
> **承接 change 名**：`tinydb-types`

本文档落实六轮澄清与 A–F 段设计裁决，提供实现级技术方案供 build 阶段 implementer 直接对照。

---

## 1. Context

`tinydb-mvp` 与后续 engine-v1 / constraints 累积的 4 类型（INT/TEXT/FLOAT/BOOL）只是教学切片。SQL 用户立刻会撞到三类卡点：

1. `name VARCHAR(64)` / `created_at TIMESTAMP` 直接 parse error
2. `SMALLINT` / `BIGINT` 缺失让"超出 INT 范围"成为无解
3. `DECIMAL(p,s)` 缺失导致浮点钱算错
4. `FLOAT` 接受 `NaN` / `Infinity` → 后续聚合路径 NaN 传染

本 change 在不动存储页格式、不动 SQL 行为的前提下，把 4 类型扩到 15 类型（+别名）。**前置依赖**：无（独立 change）。**后续**：可能被 `tinydb-engine-v2` 的 `IndexManager` 复用 codec 接口（按需）。

---

## 2. Goals / Non-Goals

### Goals

- **15 个类型**（11 新 + 4 旧）：VARCHAR / CHAR / DECIMAL / DOUBLE / REAL / BOOLEAN / SMALLINT / BIGINT / DATE / TIME / TIMESTAMP + INT / TEXT / FLOAT / BOOL
- **类型参数**：`VARCHAR(N)` / `CHAR(N)` / `DECIMAL(p,s)` 在 AST 显式存 `type_params: tuple[int, ...]`
- **严格同类型比较**：不隐式 widening；`INT ≠ SMALLINT ≠ BIGINT`、`VARCHAR ≠ TEXT`、`DOUBLE ≠ FLOAT`、`DATE ≠ TIMESTAMP`
- **FLOAT 4 字节单精度**：FLOAT 与 REAL 都用 IEEE 754 single（`>f`）；DOUBLE 用 double（`>d`）
- **CHAR PAD SPACE**：`CHAR(5)` 写 `'ab'` 存 `'ab   '`；读取保留 padding
- **DATETIME UTC 统一**：DATE 存 days since UTC epoch；TIME 存 seconds since midnight UTC；TIMESTAMP 存 seconds since UTC epoch
- **FLOAT/DOUBLE 拒绝 inf/nan**：所有 inf/nan literal 与 runtime value 一律抛 `ValueError`
- **DECIMAL 用 scaled int64**：`DECIMAL(p,s)` 内部 = `int(value * 10^s)`；`p ∈ [1,18]`、`s ∈ [0, p)`
- **向后兼容**：旧 catalog JSON 缺 `type_params` 字段默认 `()`；旧 `py_to_db` / `db_to_py` / `parse_*_literal` 签名不变
- **模块行数预算**：`type_system.py ≤ 350 行`（紧张但可达；超限拆 `type_system/` package）

### Non-Goals（本期明确不做）

- 不引入 BLOB / JSON / UUID / INET 等扩展类型
- 不引入 NULLABLE 类型（受 `tinydb-constraints` 决定）
- 不引入时区（`TIMESTAMP WITH TIME ZONE`）
- 不引入 Type cast（`CAST(x AS TYPE)` 后续可选）
- 不引入 `INTERVAL` 类型
- 不引入复合索引（`tinydb-engine-v2` 范围）
- 不引入 hash index / 倒排索引 / 全文索引
- 不引入 ANALYZE / 索引统计信息

---

## 3. Architecture Overview

### 模块边界

```
┌───────────────────┐    ┌──────────────────┐    ┌────────────────┐
│   tokenizer       │───▶│   parser         │───▶│   executor     │
│  KEYWORDS+3       │    │ ColumnDefinition │    │ ColumnDef→Col  │
│  DATETIME lit     │    │  + type_params   │    │  codec dispatch│
└───────────────────┘    └──────────────────┘    └────────────────┘
        │                       │                       │
        ▼                       ▼                       ▼
   type_system            type_system               type_system
   (pure fns)             (registry)                (registry)
                                │                       │
                                ▼                       ▼
                        ┌──────────────────┐    ┌────────────────┐
                        │   parser AST     │    │   catalog      │
                        │  (frozen)        │    │  Column (froz) │
                        │ + type_params    │    │ + type_params  │
                        └──────────────────┘    └────────────────┘
```

- `type_system` 持有 `TypeCodec` Protocol + `REGISTRY: dict[str, TypeCodec]` + `lookup(name)`
- `parser` 持有 frozen `ColumnDefinition(name, type, type_params, nullable, unique, primary_key)`，**绝不**引入 `catalog`
- `catalog` 持有 frozen `Column(name, type, type_params, nullable, unique, primary_key)`；物理 JSON object `{name, type, type_params, nullable, unique, primary_key}`
- `executor` 在 CREATE TABLE 阶段把 `tuple[ColumnDefinition, ...]` 显式映射为 `tuple[Column, ...]`；其它阶段用 codec 直接 dispatch
- `row_codec` 用 `lookup(type).encode_py(val)` 与 `codec.decode_bytes(buf, offset)` 拿到累计 offset；不做 length prediction

---

## 4. Key Decisions

### D1 — AST 表示：`type_params` 独立字段

```python
@dataclass(frozen=True)
class ColumnDefinition:
    name: str
    type: str                       # "VARCHAR" / "DECIMAL" / "INT"（不带参数）
    type_params: tuple[int, ...] = ()
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False
```

`VARCHAR(64)` 解析为 `type="VARCHAR", type_params=(64,)`；`DECIMAL(10,2)` 为 `(10, 2)`；无参类型为 `()`。

**理由**：
- catalog 序列化清晰（独立字段，无 string-split 复杂度）
- 不动 frozen 结构；只新增字段（向后兼容）
- 与既有 `nullable` / `unique` / `primary_key` 字段模式一致

**拒绝方案**：
- 字符串嵌入（`type="VARCHAR(64)"`）→ 需要 splitter；caller 拿不到纯类型名
- `ColumnType` dataclass → 改动面大；所有 caller 都要改

### D2 — Codec 架构：Protocol-based Registry

```python
class TypeCodec(Protocol):
    name: str
    aliases: tuple[str, ...] = ()
    def encode_py(self, value: Any) -> bytes: ...
    def decode_bytes(self, buf: bytes, offset: int) -> tuple[Any, int]: ...
    def parse_literal(self, text: str, params: tuple[int, ...]) -> Any: ...
    def validate(self, value: Any) -> None: ...

REGISTRY: dict[str, TypeCodec] = { ... 15 entries ... }

def lookup(type_name: str) -> TypeCodec:
    """Return the parameterless codec template (for non-parametric types)."""

def codec_for(type_name: str, params: tuple[int, ...] = ()) -> TypeCodec:
    """Return a configured codec instance for the given type and params.

    For non-parametric types (INT, TEXT, FLOAT, BOOL, DATE, TIME, TIMESTAMP),
    params must be `()` and the registry singleton is returned.

    For parametric types:
      - VARCHAR(N) / CHAR(N): params must be `(N,)` with N >= 1
      - DECIMAL(p, s): params must be `(p, s)` with 1 <= p <= 18 and 0 <= s < p

    Returns a fresh instance per call (codecs may cache, but contract is
    that mutating returned codec does not affect REGISTRY state).
    """
```

**理由**：design.md 已选 Protocol 路径；15 个 codec 走 Protocol + registry 比 15 组模块函数干净得多；新增类型只需注册。

**拒绝方案**：
- 模块函数 + `_ENCODERS` dict → type_system.py 后期难维护；扩到 30+ 类型时函数数量爆炸
- 纯 Codec 类（无 Protocol）→ 多态需要 `isinstance` 检查；duck typing 不彻底

### D3 — REAL / FLOAT 4 字节单精度

| 类型 | 字节 | IEEE 754 | struct 格式 |
|---|---|---|---|
| FLOAT | 4 | single | `>f` |
| REAL | 4 | single | `>f`（alias FLOAT） |
| DOUBLE | 8 | double | `>d` |
| DOUBLE PRECISION | 8 | double | `>d`（alias DOUBLE） |

**理由**：SQL92 标准 REAL = single precision。

**风险**：MVP 既有 `encode_float` 用 `>d`（double），需要改 `>f`。后果是约 7 位有效数字限制（vs double 的 ~15 位）。build 阶段需摸底既有 FLOAT 测试，必要时调整测试期望值。

**拒绝方案**：
- 保留 FLOAT 8 字节 → 偏离 SQL92；MVP 测试不受影响但语义错

### D4 — CHAR PAD SPACE

`CHAR(5)` 写 `'ab'` → 内部存 `'ab   '`（右侧空格填到 5 字节）；读取保留 padding。

**理由**：SQL92 默认 PAD SPACE 语义。

**副作用**：`CHAR(5)='ab'` 与 `CHAR(5)='ab   '` 视为不等（严格同字节比较）。MVP 阶段不主动 RTRIM。

**拒绝方案**：
- 仅长度限制（不填充）→ 与 VARCHAR 行为雷同；CHAR 失去独立价值
- STRIP 后存为 VARCHAR → 偏离 SQL92；写后读出可能改变数据

### D5 — DATETIME UTC 统一

| 类型 | 存储 | 范围 | 输入格式 |
|---|---|---|---|
| DATE | `>i` (4B signed BE) days since UTC epoch | 1970-01-01 ± 2^31 天 | `DATE 'YYYY-MM-DD'` |
| TIME | `>I` (4B unsigned BE) seconds since midnight UTC | 0..86399 | `TIME 'HH:MM:SS'` |
| TIMESTAMP | `>q` (8B signed BE) seconds since UTC epoch | UTC epoch ± 2^63 秒 | `TIMESTAMP 'YYYY-MM-DD HH:MM:SS'` |

**理由**：单一时区最简；跨时区数据由 caller 在应用层处理（不透明但可控）。

**拒绝方案**：
- naive datetime 对象 → 跨时区数据会出错
- UTC + 原始 offset → codec 复杂度上升一档；本 change 不引入

### D6 — 严格同类型比较

```python
def validate_compare(col_value, col_type, col_params,
                     lit_value, lit_type, lit_params):
    if col_type != lit_type or col_params != lit_params:
        raise TypeError(...)
    if col_type in ("FLOAT", "DOUBLE"):
        if math.isnan(col_value) or math.isinf(col_value):
            raise ValueError(f"{col_type} inf/NaN not allowed")
        if math.isnan(lit_value) or math.isinf(lit_value):
            raise ValueError(f"{col_type} literal inf/NaN not allowed")
```

**理由**：与 MVP 严格类型规则一致；不隐式 widening。

**影响**：跨类型比较需要显式 CAST（本 change 不实现）；用户写 `WHERE varchar_col = 'foo'`（TEXT literal）会因 VARCHAR vs TEXT 抛 `TypeError`。build 阶段在 README 显式标注。

---

## 5. Storage Encoding 表

| 类型 | 字节形态 | type_params | 例 | 校验 |
|---|---|---|---|---|
| SMALLINT | `>h` (2B signed BE) | `()` | -32768..32767 | range check |
| INT | `>i` (4B signed BE) | `()` | -2^31..2^31-1 | range check |
| BIGINT | `>q` (8B signed BE) | `()` | -2^63..2^63-1 | range check |
| FLOAT | `>f` (4B IEEE 754 single) | `()` | ~7 位有效数字 | reject inf/nan |
| DOUBLE | `>d` (8B IEEE 754 double) | `()` | ~15 位有效数字 | reject inf/nan |
| TEXT | `>H` length + UTF-8 | `()` | 不限长度 | — |
| VARCHAR(N) | `>H` length + UTF-8 | `(N,)` | len ≤ N | 超长抛 TypeError |
| CHAR(N) | `>H` length + UTF-8（PAD SPACE） | `(N,)` | 右侧空格填到 N | 超长抛 TypeError |
| BOOL | 1 byte (`\x00`/`\x01`) | `()` | True/False | — |
| DECIMAL(p,s) | `>q` (8B signed BE) scaled int64 | `(p, s)` | `int(value * 10^s)` | scaled 溢出抛 OverflowError |
| DATE | `>i` (4B signed BE) days since UTC epoch | `()` | 1970-01-01 = 0 | — |
| TIME | `>I` (4B unsigned BE) seconds since midnight UTC | `()` | 0..86399 | 范围检查 |
| TIMESTAMP | `>q` (8B signed BE) seconds since UTC epoch | `()` | 1970-01-01 = 0 | — |

**CHAR padding 实现**：
```python
class CharCodec:
    name = "CHAR"
    def encode_py(self, value: str) -> bytes:
        n = self.params[0]
        if len(value) > n:
            raise TypeError(f"CHAR({n}) length {len(value)} exceeds max")
        padded = value + " " * (n - len(value))
        return struct.pack(">H", n) + padded.encode("utf-8")
```

**DECIMAL scaled 边界**：
- `DECIMAL(p,s)` value 范围：`[-(10^(p-s)), 10^(p-s) - 10^-s]`
- scaled int64 范围：`±9.22e18`（scaled value 上界）
- `DECIMAL(18,0)` 范围：`±10^18`；scaled = value；fits int64
- `DECIMAL(18,6)` 范围：`±10^12`；scaled = value * 10^6；fits int64

---

## 6. Parser & Tokenizer 扩展

### 6.1 Parser grammar

```
column_def   = IDENT type_spec [constraints]
type_spec    = IDENT [ '(' INT_LITERAL (',' INT_LITERAL)? ')' ]
```

### 6.2 Parser 改动（`src/tinydb/parser.py`）

- `SUPPORTED_TYPES = {"INT", "TEXT", "FLOAT", "BOOL"}` → 扩到 15 + 4 别名 = 19 项
- `_parse_create_table` 中新增 `_parse_type_spec()` 方法（伪码见 brainstorm summary）
- `_parse_primary`（或新增 `_parse_datetime_literal`）处理 `DATE / TIME / TIMESTAMP` 前缀
- `ColumnDefinition` dataclass 加 `type_params: tuple[int, ...] = ()`
- 参数合理性校验：VARCHAR/CHAR 必须 1 参数；DECIMAL 必须 2 参数；DECIMAL 必须 `1 ≤ p ≤ 18` 且 `0 ≤ s < p`

### 6.3 Tokenizer 改动（`src/tinydb/tokenizer.py`）

- `KEYWORDS` 集合加 `DATE / TIME / TIMESTAMP`（aggregation worktree 已用此模式）
- 字面量识别逻辑保持：parser 在看到 `DATE/TIME/TIMESTAMP` 关键字后立即调 `expect("TEXT", ...)` 拿紧随的字面量字符串

### 6.4 字面量 → 类型 assign（INSERT 路径）

| schema 列 | literal | 行为 |
|---|---|---|
| `DECIMAL(10,2)` | `1.23` (FLOAT literal) | FLOAT vs DECIMAL 类型不匹配 → 抛 TypeError（用户需写 `DECIMAL '1.23'`） |
| `DECIMAL(10,2)` | `DECIMAL '1.23'` (新 token) | OK |
| `DATE` | `DATE '2026-07-16'` | OK → DateCodec.parse_literal → days |
| `DATE` | `'2026-07-16'` (TEXT literal) | DATE vs TEXT 不匹配 → 抛 TypeError |
| `VARCHAR(64)` | `'foo'` (TEXT literal) | VARCHAR vs TEXT 不匹配 → 抛 TypeError |
| `INT` | `123` (INT literal) | OK |
| `INT` | `'123'` (TEXT literal) | INT vs TEXT 不匹配 → 抛 TypeError |

**DECIMAL literal token**：parser 在看到 `DECIMAL` 关键字后立即 `expect("TEXT", ...)`，产出新 token 类型 `DECIMAL_LITERAL(value)`。type_system 新增 `parse_decimal_literal`。

---

## 7. Catalog 兼容性

### 7.1 新 JSON 格式

```json
{"columns": [
  {"name": "id", "type": "INT", "type_params": [],
   "nullable": false, "unique": false, "primary_key": true},
  {"name": "name", "type": "VARCHAR", "type_params": [64],
   "nullable": true, "unique": false, "primary_key": false}
]}
```

### 7.2 向后兼容

- `Column.from_dict(d)` 在 `d` 缺 `type_params` 键时默认 `type_params = ()`
- `Column.to_dict()` 始终包含 `type_params`（即使空 tuple 输出 `[]`）
- 旧 list-of-tuple 格式 `[name, type]` 仍支持（已有 `_load_column` 双格式 loader）

### 7.3 测试矩阵

| 测试 | 旧 JSON | 新 JSON |
|---|---|---|
| `test_column_from_dict_legacy_no_type_params` | `{name, type, nullable, unique, primary_key}` 缺 type_params | — |
| `test_column_from_dict_with_type_params` | — | `{name, type, type_params: [64], ...}` |
| `test_column_from_dict_decimal_2tuple` | — | `{name, type, type_params: [10, 2]}` |
| `test_column_to_dict_includes_type_params` | — | to_dict 输出含 `type_params: []` |

---

## 8. row_codec 改动

### 8.1 encode_row

```python
def encode_row(values: list, schema: list[tuple[str, str, tuple]]) -> bytes:
    if len(values) != len(schema):
        raise ValueError(...)
    blen = _bitmap_len(len(schema))
    bitmap = bytearray(blen)
    parts: list[bytes] = []
    for i, (val, (_name, typ, params)) in enumerate(zip(values, schema)):
        if val is None:
            bitmap[i // 8] |= 1 << (i % 8)
            continue
        codec = codec_for(typ, params)  # factory, no shared state mutation
        parts.append(codec.encode_py(val))
    return bytes(bitmap) + b"".join(parts)
```

### 8.2 decode_row

```python
def decode_row(buf: bytes, schema: list[tuple[str, str, tuple]]) -> list:
    col_count = len(schema)
    bitmap = buf[:_bitmap_len(col_count)]
    offset = _bitmap_len(col_count)
    values = []
    for i, (_name, typ, params) in enumerate(schema):
        if bitmap[i // 8] & (1 << (i % 8)):
            values.append(None)
            continue
        codec = codec_for(typ, params)
        v, offset = codec.decode_bytes(buf, offset)
        values.append(v)
    return values
```

**关键**：
- 每个 codec 的 `decode_bytes(buf, offset) -> (value, new_offset)` 返回累计 offset；row_codec 无需 length prediction
- `codec_for(typ, params)` 是 factory，每次返回新实例或缓存实例（实现可选）；绝不修改 REGISTRY 状态

### 8.3 schema 投影

`catalog.TableInfo.schema` 现有方法返回 `list[tuple[str, str]]`（`[(name, type), ...]`）。需要扩展为 `list[tuple[str, str, tuple]]` 或新方法 `schema_v2()` 返回 `(name, type, type_params)` 三元组。

**决策**：保持 `schema()` 不变（向后兼容 row_codec 旧 caller）；row_codec 改用新 `schema_v2()` 内部方法。

---

## 9. Test Strategy

### 9.1 单元测试（`tests/unit/`）

```
test_type_system_v2.py        # 11 个新 codec 的单元测试
test_parser_type_spec.py      # VARCHAR(N) / DECIMAL(p,s) 解析
test_parser_datetime_lit.py   # DATE / TIME / TIMESTAMP 字面量解析
test_catalog_type_params.py   # Column.type_params 序列化 + 向后兼容
```

每 codec 至少覆盖：
- `test_codec_registry_complete`（15 个 core + 4 别名）
- 数值类型：`test_int_width_roundtrip` / `test_int_range_boundary_{min,max}`
- 浮点：`test_float_single_precision` / `test_double_roundtrip` / `test_float_rejects_inf_nan`
- 字符串：`test_varchar_max_len_enforced` / `test_varchar_alias_text`
- CHAR：`test_char_pads_to_length` / `test_char_rejects_overflow`
- DECIMAL：`test_decimal_scaled_int64_roundtrip` / `test_decimal_precision_overflow` / `test_decimal_rejects_p_less_than_s`
- DATETIME：`test_date_iso_parse_roundtrip` / `test_time_iso_parse_roundtrip` / `test_timestamp_iso_parse_roundtrip` / `test_datetime_rejects_unparseable`
- 别名：`test_double_alias_double_precision` / `test_real_alias_float` / `test_int_alias_integer`

### 9.2 集成测试（`tests/integration/`）

```
test_types_roundtrip.py       # 15 类型 insert → select 全 roundtrip
test_types_in_where.py        # SELECT WHERE 跨 15 类型比较
test_types_repl.py            # REPL 端到端
```

### 9.3 反向回归

```bash
# 在 tinydb-types worktree 中
.venv/bin/python -m pytest --cov=tinydb --cov-fail-under=90 -q

# 预期：390+ passed（既有）+ 30+ 新测试 passed；coverage ≥ 90%
```

### 9.4 FLOAT 4-byte 迁移专项

```bash
.venv/bin/python -m pytest -k FLOAT -v
```

若有失败：
- 若测试本就用 `pytest.approx` 等近似比较 → 通过
- 若测试硬编码精确值 → 调整测试期望值到单精度可表示的形式（如 `3.1415927`）+ 加注释说明精度限制
- 若测试明确要求双精度 → 标记 `xfail(reason="FLOAT 4-byte precision migration")` 或迁移到 DOUBLE 列

### 9.5 覆盖率目标

- 新代码 100% line coverage（codec 与 parser 是核心）
- 全局 coverage ≥ 90%（与既有 threshold 一致）
- 模块行数：`type_system.py ≤ 350`、`parser.py ≤ 870`

---

## 10. Risks & Mitigations

| ID | Risk | Mitigation |
|---|---|---|
| R1 | parser 解析 `(N)` vs `(p,s)` vs 无参 → 显式 grammar 三分支 + 参数数量校验 | §6.2 |
| R2 | DECIMAL 精度溢出（scaled value > 2^63）→ 边界 unit 测 | §9.1 |
| R3 | VARCHAR vs TEXT 行为差异让旧用户文案错 → README 显式标注 | §11 |
| R4 | FLOAT 拒绝 inf/nan 后旧测试 `test_float_inf_rejected` 等仍能通过；inf 路径从未在 MVP 启用过 | §9.4 |
| R5 | 与 engine-v1/constraints/aggregation 路径冲突 → 反向测试全跑 | §9.3 |
| R6 | row_codec schema 投影（`schema()` vs `schema_v2()`）混淆 → 旧 caller 不动；row_codec 用新方法 | §8.3 |
| R7 | module 行数超 350 → 拆 `type_system/` package（YAGNI 后置） | §2 goals |
| R8 | DATE/TIME/TIMESTAMP round-trip 在闰秒/夏令时边界出错 → UTC 统一存储，无 DST 复杂度 | D5 |

---

## 11. Documentation Updates

- `README.md` 新增"Type System"段：15 类型表 + 类型参数语法 + 严格同类型比较规则 + FLOAT 4 字节精度提示
- `docs/superpowers/specs/2026-07-18-tinydb-types-design.md`（本文档）
- `openspec/changes/tinydb-types/proposal.md` 标注已实现的 D1-D6 决策
- `openspec/changes/tinydb-types/tasks.md` 同步调整 §10 行数预算到 `type_system.py ≤ 350`

---

## 12. Out of Scope 详单

明确不在本 change 范围：

- BLOB / JSON / UUID / INET 等扩展类型
- NULLABLE 类型
- 时区（`TIMESTAMP WITH TIME ZONE`）
- Type cast（`CAST(x AS TYPE)`）
- INTERVAL 类型
- 复合索引 / 倒排索引 / hash index
- ANALYZE / 索引统计信息
- ALTER TABLE / DROP CONSTRAINT / CHECK / FOREIGN KEY / DEFAULT
- 与 engine-v1 / constraints / aggregation 的功能新增耦合（独立交付）
