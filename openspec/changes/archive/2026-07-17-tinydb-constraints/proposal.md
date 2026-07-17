# Proposal: tinydb-constraints

> **范围声明**：本 change 在 `tinydb-mvp` 之上引入列级约束 `NOT NULL` / `UNIQUE` / `PRIMARY KEY` 的解析与执行期强制。仅改 parser/executor 入口校验路径，**不动存储格式**（PRIMARY KEY 暂不强制建索引，依赖 `tinydb-engine-v2` 才能高效化），不引入事务，不引入新类型。

## Why

在真实应用里，没有约束的数据库等同没有边界。没有 `NOT NULL` 时 `INSERT INTO t(name) VALUES ()` 一句报 parse error 是 MVP 的误导——用户以为 schema 错，实际是 parser 拿到空列名。`UNIQUE` / `PRIMARY KEY` 不强制时数据复制 bug 会让"看上去正常"的表开始吐脏数据。

UNIQUE 强制不加索引会引入 O(n) 扫描，本次 change 接受这个性能代价：MVP 表规模 < 1000 行的教学场景里 100ms 内的 O(n) 完全可接受；性能化（建索引）放到 `tinydb-engine-v2`。

## What Changes

- **新增** parser：`CREATE TABLE t(id INT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE)` 解析
- **新增** executor INSERT 路径：每行写入前对每个列跑约束校验（NULL 校验 + 全表 UNIQUE 校验）
- **新增** executor INSERT 失败类型：`ConstraintViolation`（含 `kind: null | unique | duplicate_pk`，列名，原值）
- **修改** `catalog.py::TableInfo` schema 表示：`Column(name, type, nullable, unique, primary_key)` 元组
- **新增** `parser.py` 关键字：`PRIMARY`、`KEY`、`UNIQUE`、`NULL`（仅列约束上下文）
- **修改** `INSERT INTO t VALUES (NULL)` 现有抛 ParseError 行为改为抛 ConstraintViolation
- **新增** REPL/CLI 错误信息分级：`ERROR: ConstraintViolation(kind='null', column='name')`

## Capabilities

### New Capabilities

- `schema-column-constraints`：CREATE TABLE 列定义支持 `NOT NULL` / `UNIQUE` / `PRIMARY KEY`；这些子句可任意组合（多列 UNIQUE = 复合唯一键；多列 PRIMARY KEY = 复合 PK）
- `constraint-execution-enforcement`：INSERT 路径触发约束校验；违反抛 `ConstraintViolation`；失败行不写入

### Modified Capabilities

- `storage-engine`：`Catalog` schema 元数据从 `dict[name, type]` 升级为 `dict[name, Column]`，向后兼容路径（nullable 默认为 `False`）
- `sql-minimal-parser`：INSERT parser 上下文对 `NULL` 字面量由"不识别"改为识别（限 INSERT 上下文）

## Impact

- 受影响文件：`src/tinydb/parser.py`（+~50 行）、`src/tinydb/executor.py`（+~100 行）、`src/tinydb/catalog.py`（+~30 行）
- 模块行数预算：
  - `parser.py` ≤ 750 行（与 engine-v1 一致上调预算）
  - `executor.py` ≤ 620 行
  - `catalog.py` ≤ 130 行
- 测试新增：单元 ~25、集成 ~12、e2e golden ~8
- 不引入新依赖；不破坏外部 API
- 性能影响：每条 INSERT 增加 O(n) UNIQUE 扫描；不引入索引

## Out of Scope（本 change 明确不做）

- `CHECK` / `FOREIGN KEY` / `DEFAULT` → 永久 out（复杂度过高，不在 MVP 衍生范围）
- UNIQUE 高效执行（建 B-tree 索引）→ 留 `tinydb-engine-v2`
- PRIMARY KEY 强制 NOT NULL + UNIQUE 的 SQL92 标准语义组合不在 parser 内强制，仅在 executor 内运行时校验（保证 parser 不爆炸）
- ALTER TABLE ADD CONSTRAINT → 永久 out
- 事务隔离下约束延迟校验 → 留 `tinydb-acid`
- UPDATE 路径下约束校验 → 留后续 change（与 `tinydb-engine-v1` 路线 merge 时引入）
