# Proposal: tinydb-types

> **范围声明**：本 change 把 MVP 的 4 种类型（INT/TEXT/FLOAT/BOOL）扩到 SQL92 子集：`VARCHAR(N)` / `CHAR(N)` / `DECIMAL(p,s)` / `DOUBLE` / `REAL` / `BOOLEAN` 别名 / `SMALLINT` / `BIGINT` / `DATE` / `TIME` / `TIMESTAMP`，并修复 `FLOAT` / `DOUBLE` 接受 `inf` / `NaN` 的 bug。**不动存储页格式**，仅在 `type_system.py` / `tokenizer.py` / `parser.py` 上叠加；不影响 SQL92 全部子集（如 `BLOB` / `JSON` / `NULLABLE TYPES` 不做）。

## Why

MVP 暴露给用户的 4 类型限制看似教学清晰，但实际造成了两个真实卡点：
1. 写 `name VARCHAR(64)` / `created_at TIMESTAMP` → 立刻 parse error
2. `FLOAT` 接受 NaN/inf → `WHERE amount = 'NaN'` 后续聚合路径上 NaN 会传染（NaN != 任何值）

而且 `SMALLINT` / `BIGINT` 缺失让"超出 INT 范围"成为无解。`DECIMAL(p,s)` 不支持导致浮点钱算错。

## What Changes

- **新增** 类型：`VARCHAR(N)` / `CHAR(N)` / `DECIMAL(p, s)` / `DOUBLE` / `REAL` / `BOOLEAN` / `SMALLINT` / `BIGINT` / `DATE` / `TIME` / `TIMESTAMP`
- **新增** 别名：`DOUBLE PRECISION` ↔ `DOUBLE`、`REAL` ↔ `FLOAT`（保持 FLOAT 已有路径）、`BOOLEAN` ↔ `BOOL`、`INT` 仍别名 `INTEGER`
- **新增** 字面量：`DATE 'YYYY-MM-DD'` / `TIME 'HH:MM:SS'` / `TIMESTAMP 'YYYY-MM-DD HH:MM:SS'` / `DECIMAL 字面量`（标准 1.23）
- **新增** range check：
  - `SMALLINT`：[-32768, 32767]
  - `INT`：[-2^31, 2^31-1]
  - `BIGINT`：[-2^63, 2^63-1]
- **新增** `FLOAT` / `DOUBLE` 拒绝 `inf` / `nan` 字面量（关键词 `Infinity` / `NaN` 或 Python float('inf')/float('nan')）
- **新增** `DECIMAL` 编码：scaled int64；`DECIMAL(p,s)` 内部值 = int(value * 10^s)；解码时除回去
- **新增** VARCHAR 长度校验：插入超长字符串抛 `TypeError`（与 MVP TEXT 不一致处；显式行为变化）
- **修改** `type_system.py::SUPPORTED_TYPES` 扩展为上述 11 + 4 旧 = 15 类型
- **修改** `parser.py` 列定义语法支持 `(N)`、`(p, s)` 参数

## Capabilities

### New Capabilities

- `type-varchar-char`：可变长 / 定长字符串类型；长度上限强校验
- `type-decimal`：精确小数；scaled int64 存储；值域严格
- `type-date-time-timestamp`：日期 / 时间 / 时间戳字面量解析与 roundtrip
- `type-int-aliases-bigint-smallint`：4 个整数宽度（SMALLINT/INT/BIGINT）+ DOUBLE/REAL/BOOLEAN 别名
- `type-float-rejects-inf-nan`：显式拒绝 inf / nan 字面量或 roundtrip

### Modified Capabilities

- `type-system-basic`（来自 MVP）：4 类型扩展为 15 类型；严格类型规则不变（解析期仍 strict）

## Impact

- 受影响文件：
  - `src/tinydb/type_system.py`（+~200 行新 codec）
  - `src/tinydb/tokenizer.py`（+~10 行关键字 + 字面量识别）
  - `src/tinydb/parser.py`（+~40 行列类型参数解析）
- 模块行数：
  - `type_system.py` ≤ 350 行（从 150 上调）
  - `parser.py` ≤ 870 行
- 测试：单元 ~50（每个类型 roundtrip + range + 字面量）、集成 ~10
- 不引入依赖；不破坏外部 API；不破坏存储页格式

## Out of Scope

- `BLOB` / `JSON` / `UUID` / `INET` 等扩展类型 → 永久 out
- `NULLABLE` 列类型（受 `tinydb-constraints` 决定）→ 不在本 change
- 时区（`TIMESTAMP WITH TIME ZONE`）→ 永久 out
- 浮点 `DECIMAL` 类型与 Python `Decimal` 库兼容（用自实现 scaled int64，无依赖）
- `INTERVAL` 类型 → 永久 out
- Type cast（`CAST(x AS TYPE)`）→ 后续可选
