# Design: tinydb-types

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

MVP `SUPPORTED_TYPES = {"INT", "TEXT", "FLOAT", "BOOL"}` 是教学切片。SQL92 类型系统丰富得多；本 change 把切片扩到教学切片之上"真正可用"的范围。

## Goals / Non-Goals

**Goals：**
- 11 个新类型（VARCHAR / CHAR / DECIMAL / DOUBLE / REAL / BOOLEAN / SMALLINT / BIGINT / DATE / TIME / TIMESTAMP）+ 4 个旧类型
- strict typing 规则不变
- FLOAT / DOUBLE 拒绝 inf / nan
- DECIMAL 用 scaled int64；round trip 精确
- DATE / TIME / TIMESTAMP 用 ISO 8601 字面量
- 不动存储页格式
- 不动 SQL 行为（SELECT/WHERE/UPDATE 路径不变）

**Non-Goals：**
- 不引入 BLOB / JSON / 区间类型
- 不引入时区
- 不引入索引化的类型

## Architecture

### type_system 模块化

```python
class TypeCodec(Protocol):
    type_name: str
    def encode_py(self, value: Any) -> bytes: ...
    def decode_bytes(self, data: bytes) -> Any: ...
    def parse_literal(self, text: str) -> Any: ...
    def validate_range(self, value: Any) -> None: ...
```

注册表：

```python
REGISTRY = {
    "INT":       IntCodec(),
    "SMALLINT":  IntCodec(width=2, signed=True),
    "BIGINT":    IntCodec(width=8, signed=True),
    "FLOAT":     FloatCodec(allow_inf=False, allow_nan=False),
    "DOUBLE":    FloatCodec(allow_inf=False, allow_nan=False, alias="DOUBLE PRECISION"),
    "REAL":      FloatCodec(...),  # alias FLOAT
    "TEXT":      TextCodec(),
    "VARCHAR":   VarcharCodec(max_len=None),  # N 来自 schema
    "CHAR":      CharCodec(max_len=None),
    "BOOL":      BoolCodec(),
    "BOOLEAN":   BoolCodec(),     # alias BOOL
    "DECIMAL":   DecimalCodec(precision=None, scale=None),
    "DATE":      DateCodec(),
    "TIME":      TimeCodec(),
    "TIMESTAMP": TimestampCodec(),
}
```

### 列定义语法

```
type_spec   = base_type [ '(' param (',' param)? ')' ]
base_type   = IDENT
param       = INT_LITERAL
```

例：
- `VARCHAR(64)` → RegisteredType("VARCHAR", max_len=64)
- `DECIMAL(10, 2)` → precision=10, scale=2

### Codec 细节

| 类型 | 编码 | 字面量 | 校验 |
|------|------|--------|------|
| SMALLINT | 2-byte big-endian signed | `123` | range check |
| INT | 4-byte big-endian signed | `123` | range check |
| BIGINT | 8-byte big-endian signed | `123` | range check |
| FLOAT | struct.pack('>f', v) | `1.5` / `1.5e3` | reject inf/nan |
| DOUBLE | struct.pack('>d', v) | `1.5` | reject inf/nan |
| TEXT | length-prefixed UTF-8 | `'...'` | — |
| VARCHAR(N) | length-prefixed UTF-8 | `'...'` | len ≤ N |
| CHAR(N) | length-prefixed, padded | `'...'` | len ≤ N |
| BOOL | 1 byte | `TRUE` / `FALSE` | — |
| DECIMAL(p,s) | int64 of (value * 10^s) | `1.23` | value fits p,s |
| DATE | 4-byte day count from epoch | `DATE '2026-07-16'` | YYYY-MM-DD parse |
| TIME | 4-byte second count | `TIME '14:30:00'` | HH:MM:SS parse |
| TIMESTAMP | 8-byte second count | `TIMESTAMP '2026-07-16 14:30:00'` | parse |

### Tokenizer 字面量

| 字面量 | 解析结果 |
|--------|----------|
| `123` | INT literal（小数点存在时 1 token = float literal） |
| `1.23` | DECIMAL/FLOAT literal；根据列上下文定 codec |
| `'foo'` | TEXT / VARCHAR / CHAR literal |
| `TRUE` / `FALSE` | BOOL literal |
| `DATE '2026-07-16'` | DATE literal（带 `DATE` 关键字作为前缀） |
| `TIME '14:30:00'` | TIME literal |
| `TIMESTAMP '...'` | TIMESTAMP literal |

## Decisions

### D1: DECIMAL 用 int64 scaled 实现

- 选项 A：scaled int64 ← 选 A
- 选项 B：Python `Decimal`（mpdecimal）绑定
- 理由：教学项目零运行时依赖；scaled int64 完全够 18 位整数精度

### D2: VARCHAR 与 TEXT 行为差异

- 选项 A：VARCHAR(N) 强校验，TEXT 不限 ← 选 A
- 选项 B：VARCHAR = TEXT
- 理由：D2 是 SQL92 标准语义；用户写 VARCHAR 表达"长度约束意图"

### D3: DATE/TIME/TIMESTAMP 字面量仅 ISO 8601

- 选项 A：仅 `'YYYY-MM-DD'` / `'HH:MM:SS'` 等标准格式 ← 选 A
- 选项 B：宽松格式
- 理由：标准格式解析稳定；宽松格式是后续可选增量

### D4: FLOAT/DOUBLE 拒绝 inf/nan 的口径

- 选项 A：拒绝所有 inf/nan（含 `Infinity`/`NaN` 关键字） ← 选 A
- 选项 B：仅 `math.inf`/`math.nan`
- 理由：绝对禁止最容易解释；用户写 `inf` 是 bug

### D5: type_system.py 从 150 上调到 350 行

- 11 个新 codec 必然涨；预算调整；模块仍保持纯函数 + registry 形式

## Risks

- **R1**：parser 解析 `(64)` vs `(10, 2)` vs 无参数 → 显式 grammar 三分支
- **R2**：DECIMAL 精度溢出（scaled value > 2^63）→ 边界 unit 测
- **R3**：VARCHAR 与 TEXT 行为差异让旧用户文案错 → README 显式标注
- **R4**：FLOAT 拒绝 inf 后旧测试 `test_float_inf_rejected` 等仍能通过；inf 路径从未在 MVP 启用过
- **R5**：与 engine-v1/constraints/aggregation 路径冲突 → 反向测试

## Test Plan

- 单元 `tests/unit/test_type_system_v2.py`：11 个新 codec 各 roundtrip + range + 字面量；inf/nan 拒绝
- 单元 `tests/unit/test_parser_type_spec.py`：列定义带 `(N)` / `(p,s)` / 无参数
- 集成 `tests/integration/test_types_roundtrip.py`：跨 row_codec encode/decode 全 15 类型
- 集成 `tests/integration/test_types_in_select.py`：SELECT WHERE date_col = DATE '2026-07-16' 等典型 BI 路径
- 反向：MVP + 后续 change 全部测试不回归
