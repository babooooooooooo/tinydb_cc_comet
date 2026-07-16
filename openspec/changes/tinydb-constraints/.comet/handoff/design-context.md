# Comet Design Handoff

- Change: tinydb-constraints
- Phase: design
- Mode: compact
- Context hash: 099a0719c5c7b13b3cf721198808245f4160c0ea7df40620c2259a5fe7df5033

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tinydb-constraints/proposal.md

- Source: openspec/changes/tinydb-constraints/proposal.md
- Lines: 1-51
- SHA256: c9f557b277849aaeac0180994ab4c99ca03ab1c3c94757826d3d4e677b0f17b1

```md
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

```

## openspec/changes/tinydb-constraints/design.md

- Source: openspec/changes/tinydb-constraints/design.md
- Lines: 1-132
- SHA256: c97bdc4938a047ffed2f7d92ab96046c9c3ee2b85620ec4d8eba8cdbfd59ee05

[TRUNCATED]

```md
# Design: tinydb-constraints

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

MVP 的列类型只是元标签；现 schema 表达为 `dict[str, str]`（name -> "INT" / "TEXT" 等），约束信息完全丢失。本 change 把列升级为带约束的 dataclass，并在 INSERT executor 上插入运行时校验。

## Goals / Non-Goals

**Goals：**
- CREATE TABLE 列定义支持 `NOT NULL` / `UNIQUE` / `PRIMARY KEY`
- INSERT 路径触发约束校验；失败抛 `ConstraintViolation`
- PRIMARY KEY ≈ UNIQUE + NOT NULL（运行期合并检查；语法上独立关键字，便于人读）
- UNIQUE 复合键：列出多个 UNIQUE 列表示复合唯一键；INSERT 时按行内 UNIQUE 列构造 tuple 校验
- 错误信息含列名 + 原值 + kind
- 不破坏 MVP 已写表的 schema（这些表假定所有列 NOT NULL，新约束默认与之一致）

**Non-Goals（本期不做）：**
- 不引入索引（UNIQUE 校验走全表 O(n) 扫描）
- 不引入事务（约束校验失败半成品由 `tinydb-acid` 处理）
- 不引入 ALTER TABLE / DROP CONSTRAINT
- 不引入 CHECK / FOREIGN KEY / DEFAULT

## Architecture

### Catalog 升级

```python
@dataclass(frozen=True)
class Column:
    name: str
    type: str                    # "INT" / "TEXT" / "FLOAT" / "BOOL"
    nullable: bool = True        # SQL92 默认 NULL；MVP 旧数据假定 False；引擎侧强制 INSERT 时校验
    unique: bool = False
    primary_key: bool = False

@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: tuple[Column, ...]
    root_page_id: int
    next_page_id: int
```

向后兼容：MVP 旧表反序列化时所有列 nullable=True、unique=False、primary_key=False；INSERT 路径在缺失显式 NULL 时继续以"必须非 NULL"运行（即旧行为不变）。

### Parser 改造

```
column_def  = IDENT type constraint*
constraint  = 'NOT' 'NULL'
            | 'PRIMARY' 'KEY'
            | 'UNIQUE'
type        = 'INT' | 'TEXT' | 'FLOAT' | 'BOOL'
```

要点：`NULL` 在其他上下文仍是保留字报错；parser 进入 column_def 后仅当下一 token 是 `NOT` 或 `NULL` 才识别 `NULL`。具体边界：
- `CREATE TABLE t(x INT NULL)` ← 接受
- `... = NULL`（INSERT 上下文）← 接受为字面量 `Literal(None)`
- `INSERT NULL INTO` ← 仍报错（NULL 不在 INSERT 子句首位置出现路径）

### Executor INSERT 校验顺序

```python
def execute_insert(stmt, ...):
    table = catalog.get(stmt.table)
    column_set = zip(stmt.columns, stmt.values)

    # 1. 列存在性 + NOT NULL
    for col, val in column_set:
        if val is None and not col.nullable:
            raise ConstraintViolation(kind="null", column=col.name)

    # 2. UNIQUE / PRIMARY KEY（合并去重）
    unique_groups = unique_groups_for(table)  # 收集 SINGLE + COMPOSITE
    for group in unique_groups:
        key = tuple(val for col, val in column_set if col.name in group)
        existing = scan_table(table)
        if any(tuple(r[c] for c in group) == key for r in existing):

```

Full source: openspec/changes/tinydb-constraints/design.md

## openspec/changes/tinydb-constraints/tasks.md

- Source: openspec/changes/tinydb-constraints/tasks.md
- Lines: 1-50
- SHA256: f7bb0a9c62edb602ad2e77b31a7f2d9805dcb3b4352cde3de4c314b866ad5ba5

```md
# Tasks: tinydb-constraints

> **TDD 模式**：每个任务遵循"红 → 绿 → 重构"。

## 1. Catalog Schema 升级

- [ ] 1.1 编写 `tests/unit/test_catalog_column.py::test_column_dataclass_*`，红
- [ ] 1.2 在 `src/tinydb/catalog.py` 定义 `Column` dataclass（name / type / nullable / unique / primary_key）
- [ ] 1.3 升级 `TableInfo` 使用 `tuple[Column, ...]`；`from_bytes` / `to_bytes` 序列化包含新字段
- [ ] 1.4 编写 `test_catalog_roundtrip_with_constraints`，绿（约束往返一致）

## 2. Parser：列约束

- [ ] 2.1 编写 `tests/unit/test_constraints_parser.py::test_create_table_primary_key_unique_not_null`，红
- [ ] 2.2 在 `parser.py::parse_create_table` 接入 `NOT NULL` / `UNIQUE` / `PRIMARY KEY` 子句链
- [ ] 2.3 在 `tokenizer.py` 增加 `PRIMARY` / `KEY` / `NOT` / `NULL`（限 column_def 上下文）；`UNIQUE` 不需要新增关键字（已存在 keyword 表）

## 3. Parser：`NULL` 字面量（INSERT 上下文）

- [ ] 3.1 编写 `test_insert_accepts_null_literal_when_column_nullable`，红
- [ ] 3.2 在 INSERT 解析路径识别 `NULL` 字面量为 `Literal(None)`（限 INSERT VALUES 上下文）
- [ ] 3.3 编写 `test_insert_rejects_null_for_pk`，红（PK 列写 NULL 时 executor 抛错，由 Task 5 覆盖）

## 4. Executor：INSERT 校验顺序

- [ ] 4.1 编写 `tests/unit/test_constraints_executor.py::test_insert_rejects_null_on_not_null`，红
- [ ] 4.2 在 `execute_insert` 加 NOT NULL 校验（在类型校验后落盘前）
- [ ] 4.3 编写 `test_insert_rejects_duplicate_unique_key`，红
- [ ] 4.4 实现 UNIQUE 单列 + 复合键校验（全表扫描）
- [ ] 4.5 编写 `test_insert_rejects_duplicate_primary_key`，红（PRIMARY KEY 走同一路径）
- [ ] 4.6 实现 PRIMARY KEY 等价 NOT NULL + UNIQUE 合并检查（executor 内对 PK 列同时跑 null 和 unique 校验）

## 5. 异常类型

- [ ] 5.1 编写 `test_constraint_violation_includes_kind_column_value`，红
- [ ] 5.2 在 `errors.py` 新增 `ConstraintViolation(kind, column=None, columns=None, value)` 继承 `ExecutionError`
- [ ] 5.3 在 REPL/CLI 路径上把 `ConstraintViolation` 渲染为单行 `ERROR: ConstraintViolation(kind=..., column=...)`

## 6. 兼容性

- [ ] 6.1 编写 fixture：MVP 旧版 `.db`（无约束 schema）反序列化路径不能爆
- [ ] 6.2 编写 `test_catalog_old_file_migration_loads_with_nullable_default_true`，绿
- [ ] 6.3 验证：MVP 234 个测试 + engine-v1 后续测试全部继续通过

## 7. 性能与回归

- [ ] 7.1 计时 fixture：n=1000 行 INSERT 全部通过 O(n) UNIQUE 校验总耗时 < 100ms
- [ ] 7.2 模块行数回归：`parser.py ≤ 750`、`executor.py ≤ 620`、`catalog.py ≤ 130`
- [ ] 7.3 覆盖率 ≥ 90% across project；新代码 100%
- [ ] 7.4 `docs/MVP_LIMITATIONS.md` 增补：本 change 交付后 O(n) UNIQUE 校验仍生效；索引化留 `tinydb-engine-v2`

```
