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
            raise ConstraintViolation(kind="duplicate_key", columns=group)

    # 3. 已有 INSERT 路径
    table.insert_row(...)
```

UNIQUE 不带索引 → O(n) 扫描每条 INSERT；MVP 表规模下可接受。

## Decisions

### D1: parser 不强制 PRIMARY KEY = NOT NULL + UNIQUE

- **选项 A**：parser 把 PRIMARY KEY 单独存在，运行期 executor 补齐等价语义 ← 选 A
- **选项 B**：parser 强制 PRIMARY KEY 自动添加 NOT NULL + UNIQUE
- **理由**：parser 同时维护两种状态易出 bug；executor 单一职责更能排查

### D2: ConstraintViolation 是子异常继承自 ExecutionError

- **选项 A**：继承 `ExecutionError` ← 选 A
- **选项 B**：独立顶级异常
- **理由**：REPL 与 API 已有 `except ExecutionError` 路径；新增级别增加分支而收益为 0

### D3: 旧 catalog 反序列化默认 nullable=True

- **选项 A**：nullable=True（SQL92 默认）；INSERT 路径由 executor 强制 ← 选 A
- **选项 B**：默认 nullable=False（与 MVP 行为等价）
- **理由**：MVP 实际行为是"显式 NULL 抛 ParseError"，等价于 nullable=False；但 SQL92 标准默认 NULL；A 路线更接近标准同时给旧数据兼容路径（实际新数据走 INSERT 解析期就阻止显式 NULL 写入了）

### D4: UNIQUE 复合键用 set 而非 hash

- **选项 A**：tuple 构造 set，去重 ← 选 A
- **选项 B**：单独哈希实现
- **理由**：MVP 表规模下 set 完全够用；后续 engine-v2 上索引时再换

### D5: PRIMARY KEY 列强制 NOT NULL 行为在 executor 校验期合并

- 单一来源真相：`ConstraintViolation(kind="null")` 同时覆盖显式 `NULL` 与 PK 列上的 `NULL`

## Risks

- **R1**：catalog schema 升级后，旧 `.db` 文件读不出来 → 单元测试准备 fixtures（写一份 MVP 旧版 .db）；Catalog 反序列化兼容路径必须测
- **R2**：UNIQUE 全表扫描在测试覆盖率跑满后变慢 → 引入计时 fixture（n=1000 行验证 < 100ms）
- **R3**：parser 新增 `NULL` 关键字会破坏现有 `NULL` 字面量路径 → 测试 `INSERT INTO t(x) VALUES (NULL)` 必须通过
- **R4**：PRIMARY KEY 双重约束校验顺序错乱 → 显式断言 violation 信息 kind

## Test Plan

- 单元（`tests/unit/test_constraints_parser.py`）：每种约束串一个最小 CREATE TABLE parse
- 单元（`tests/unit/test_constraints_executor.py`）：NOT NULL 拒绝、UNIQUE 单列去重、UNIQUE 复合键去重、PRIMARY KEY NULL 拒绝、PRIMARY KEY 重复拒绝
- 集成（`tests/integration/test_catalog_constraints.py`）：旧版 .db 文件能升级加载；新格式 round-trip
- e2e golden：`tests/e2e/sql/constraints/` ~8 条 SQL（覆盖每个错误路径）
- 反向：MVP 234 个测试 + engine-v1 后续测试不回归（parser 关键字新增可能误伤 → 用现有 tokenizer 区分大小写测覆盖）
