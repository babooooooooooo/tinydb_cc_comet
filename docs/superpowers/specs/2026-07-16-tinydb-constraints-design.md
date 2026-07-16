---
comet_change: tinydb-constraints
role: technical-design
canonical_spec: openspec
---

# Design: tinydb-constraints

> **关联文档**：[proposal.md](../../../../openspec/changes/tinydb-constraints/proposal.md) · [design.md](../../../../openspec/changes/tinydb-constraints/design.md) · [tasks.md](../../../../openspec/changes/tinydb-constraints/tasks.md)
> **Brainstorm checkpoint**：[brainstorm-summary.md](../../../../openspec/changes/tinydb-constraints/.comet/handoff/brainstorm-summary.md)
> **Date**：2026-07-17
> **承接 change 名**：`tinydb-constraints`

本文档落实 D1-D5 与九个第二轮细化点裁决，提供实现级技术方案供 build 阶段 implementer 直接对照。

---

## 1. Context

`tinydb-mvp` 的列类型只是元标签；现 schema 表达为 `dict[str, str]`（或 `[(name, type), ...]` 二元数组），约束信息完全丢失。本 change 把列升级为带约束的 dataclass，并在 INSERT executor 上插入运行时校验。

本 change **不动存储页格式**（row codec 已有 null bitmap，能机械编码/解码 `None`），不动 catalog 物理页面（仍是 page 1），不动 `SlottedPage.insert / delete / update` 路径。它只改：

- `parser.py` — 增加列定义 AST、tokenizer 关键字、INSERT 上下文 `NULL` 字面量
- `catalog.py` — `Column` dataclass 升级、`TableInfo.columns` 升级、JSON 双格式加载
- `executor.py` — INSERT 路径前插入校验流水线，executor 顶层映射 `ColumnDefinition` AST → `Column`
- `errors.py` — `ConstraintViolation` 子异常
- `repl.py` — `ConstraintViolation` 专门渲染

---

## 2. Goals / Non-Goals

### Goals

- CREATE TABLE 列定义支持 `NOT NULL` / `UNIQUE` / `PRIMARY KEY`，单独或组合
- INSERT 路径触发约束校验；违反抛 `ConstraintViolation`；失败行不写入
- 多行 INSERT 逐行校验；后续失败保留先前成功行（无事务）
- PRIMARY KEY ≈ UNIQUE + NOT NULL；NULL 走 `kind="null"`，UNIQUE 不与 NULL 冲突
- UNIQUE 复合键：多列同声明组内构成复合唯一键（**不是**多列各自单唯一键）
- 错误信息含 `kind` / `column[s]` / `value`
- 不破坏 MVP 已写表的 schema（旧表按 `nullable=True, unique=False, primary_key=False` 反序列化）

### Non-Goals（本期明确不做）

- 不引入索引（UNIQUE 校验走全表 O(n) 扫描）
- 不引入事务（约束校验失败半成品由 `tinydb-acid` 处理）
- 不引入 ALTER TABLE / DROP CONSTRAINT
- 不引入 CHECK / FOREIGN KEY / DEFAULT
- UPDATE 路径下约束校验留待后续 change（与 `tinydb-engine-v1` 路线合并时引入）
- `differential row encoding` / `partial indexes` / `predicate indexes` 全都不做

---

## 3. Architecture Overview

### 模块边界（按裁决 1：方案 A — 分层列模型 + executor 显式映射）

```
┌───────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│   tokenizer       │───▶│   parser        │───▶│   executor       │
│  +5 关键字        │    │  ColumnDefinition│    │  ColumnDef→Col    │
│  +NULL 字面量     │    │  AST 节点       │    │  显式映射        │
└───────────────────┘    └─────────────────┘    └──────────────────┘
                                │                       │
                                ▼                       ▼
                        ┌─────────────────┐    ┌──────────────────┐
                        │   parser AST    │    │   catalog        │
                        │  (frozen)       │    │  Column (frozen)  │
                        └─────────────────┘    │  TableInfo       │
                                               └──────────────────┘
```

- `parser` 持有 frozen `ColumnDefinition(name, type, nullable, unique, primary_key)`，**绝不**引入 `catalog`
- `catalog` 持有 frozen `Column(name, type, nullable, unique, primary_key)`；物理 JSON 表示为 `{name, type, nullable, unique, primary_key}` object
- `executor` 在 CREATE TABLE 阶段把 `tuple[ColumnDefinition, ...]` 显式映射为 `tuple[Column, ...]`；其它阶段只用 `Column`
- 双向解耦：parser 单测无需 `tinydb.catalog`；catalog 单测无需 `tinydb.parser`；executor 在中间做桥

### JSON 双格式加载（D6 裁决：方案 A）

`catalog.py` 必须同时识别两种 JSON 格式：

- 旧格式：`"schema": [["id", "INT"], ["name", "TEXT"]]`（二维数组）
- 新格式：`"schema": [{"name": "id", "type": "INT", "nullable": false, "unique": false, "primary_key": true}, ...]`

加载优先级（详见 §6）。

### INSERT 校验流水线（按裁决 3：方案 A — 逐行校验）

每条 INSERT 语句在 parser 已检查列数与字面量解析后，进入 executor 时按下表顺序逐行校验：

| 阶段 | 检查 | 失败抛出 | 错误信息 |
|------|------|----------|----------|
| 1 | 表存在 | `ExecutionError(table_not_found)` | 已有路径，不动 |
| 2 | 列清单非空、无重复、全部存在 | `ParseError`（parser 期已检查）；executor 防御性复查抛 `ExecutionError` | `unknown column "x"` / `duplicate column "x"` |
| 3 | 每行值数与显式列数一致 | `ExecutionError(column_count_mismatch)` | `expected N values, got M` |
| 4 | 归一化为 schema 顺序；省略列 → `None` | （此步纯映射，不抛错） | — |
| 5 | NOT NULL 与 PK 列非空 | `ConstraintViolation(kind="null")` | `kind=null column=x value=None` |
| 6 | 非 NULL 值类型校验（既有路径） | `TypeError`（已有路径） | 既有错误，不动 |
| 7 | UNIQUE 与 PK 重复键 | `ConstraintViolation(kind="unique" \| "duplicate_pk")` | `kind=unique columns=(a,b,c) value=(...)` |
| 8 | 编码并落盘 | 既有 row_codec / catalog 路径 | — |

- 第 1-3 步在 executor 顶上对整条语句检查一次
- 第 4-8 步逐行；任一行失败抛错并**停止**当行写入；先前成功行已落盘，无法回滚（无事务，per 裁决 3 方案 A）

---

## 4. Tokenizer

### 新增关键字

| Token | 关键字 | 上下文 |
|-------|--------|--------|
| `TOK_NOT` | `NOT` | column_def / 比较右侧；与 `NULL` 配对 |
| `TOK_NULL_KW` | `NULL` | column_def：`NOT NULL` 与 `PRIMARY KEY` 同上下文；裸 `NULL`（裁决 2：拒绝） |
| `TOK_PRIMARY` | `PRIMARY` | column_def 后续是 `KEY` |
| `TOK_KEY` | `KEY` | `PRIMARY KEY` 终结标识 |
| `TOK_UNIQUE` | `UNIQUE` | column_def / 单列 UNIQUE |

### NULL 字面量

| 上下文 | 旧行为 | 新行为 |
|--------|--------|--------|
| `INSERT INTO t(x) VALUES (NULL)` | parser 不识别；抛 `ParseError` | 接受为 `Literal(None)` |
| `INSERT INTO t(x) VALUES (NOT NULL)` | parser 报错 | parser 报错（NOT 是关键字，不能直接做字面量前导） |
| `CREATE TABLE t(x INT NULL)` | parser 不识别 | 拒绝（裁决 2：方案 A），但 `NOT NULL` 必须接受 |
| `CREATE TABLE t(x INT)` | nullable=True 默认 | nullable=True 默认（D3） |
| `INSERT NULL INTO t(x) VALUES (1)` | parser 不识别 | parser 报错：NULL 不是 INSERT 子句首位置关键字 |

实现细节：`tokenizer.lex()` 在遇到大小写不敏感的 `NULL` 时不直接返回；改为 peek 下一 token：

- next 是 `(`, `,`, `)`, `;`, 比较运算符右侧 → 返回 `TOK_NULL_LITERAL`
- next 是 token end 或 `KEY` / `FROM` 等 → 抛 `ParseError("bare NULL not allowed")`（限 column_def 上下文，详见 §5.2）

---

## 5. Parser

### 5.1 ColumnDefinition AST

```python
@dataclass(frozen=True)
class ColumnDefinition:
    name: str
    type: str                  # "INT" | "TEXT" | "FLOAT" | "BOOL"
    nullable: bool = True      # SQL92 default; D3 与裁决 1 一致
    unique: bool = False
    primary_key: bool = False
```

`CreateTable` 升级（向后兼容默认值）：

```python
@dataclass(frozen=True)
class CreateTable:
    name: str
    columns: tuple[ColumnDefinition, ...] = ()   # 旧代码若有 tuple[tuple[str,str]] 兼容层由适配函数兜底
    if_not_exists: bool = False                  # 兼容 MVP 的 `CREATE TABLE IF NOT EXISTS`
```

旧 `tuple[str, str]` AST 形态彻底移除；任何遗留断言更新。所有现有测试 `tests/unit/test_parser.py` 必须逐个改写为新 shape。这是显式的大改动——调用方（executor、REPL）通过 `ColumnDefinition` 字段访问，迁移成本是一次性的。

### 5.2 Grammar（EBNF 简写）

```
column_def    = IDENT type_spec constraint_clause*

type_spec     = 'INT' | 'TEXT' | 'FLOAT' | 'BOOL'

constraint_clause
              = 'NOT' 'NULL'         -> nullable = False
              | 'PRIMARY' 'KEY'      -> primary_key = True
              | 'UNIQUE'             -> unique = True

# 拒绝裸 NULL（裁决 2）
# parser 看到 NOT 后必须跟 NULL；parser 看到 PRIMARY 必须跟 KEY；
# parser 看到 UNIQUE 单 token 即可；不识别顺序约束以后任何与已知关键字冲突的 token。
```

### 5.3 INSERT 上下文

`parser.parse_insert` 接受 `VALUES (NULL, ...)` 列表，NULL 项转为 `Literal(None)`：

```python
@dataclass(frozen=True)
class Literal:
    value: int | float | str | bool | None   # ← None 类型加入
```

类型提示与小注释更新为 `int | float | str | bool | None`。`Literal(None)` 的运行期语义由 executor 决定（NULL 走约束或值映射）。

### 5.4 INSERT 列归一化（裁决 5：方案 A）

```
insert_statement = 'INSERT' 'INTO' IDENT '(' column_list ')' 'VALUES' value_rows
column_list      = IDENT (',' IDENT)*
value_rows       = '(' value (',' value)* ')' (',' '(' value (',' value)* ')')*
```

约束：
- column_list 非空
- column_list 无重复
- column_list 中每个 IDENT 必须在 schema 内存在（executor 防御性复查）
- 省略列由 executor 归一化时填 None（不要在这里默认 0/空串）

### 5.5 Parser 错误矩阵

| 输入 | 抛错 | 消息 |
|------|------|------|
| `CREATE TABLE t(x INT NULL)` | ParseError | `bare NULL not allowed; use NOT NULL or omit` |
| `CREATE TABLE t(x INT NOT)` | ParseError | `expected NULL after NOT` |
| `CREATE TABLE t(x INT PRIMARY)` | ParseError | `expected KEY after PRIMARY` |
| `CREATE TABLE t(x INT UNIQUE NOT NULL UNIQUE)` | ParseError | `duplicate UNIQUE constraint` |
| `CREATE TABLE t(x INT PRIMARY KEY PRIMARY KEY)` | ParseError | `duplicate PRIMARY KEY` |
| `CREATE TABLE t(NOT)` | ParseError | `unexpected NOT at column name` |
| `INSERT INTO t() VALUES (1)` | ParseError | `INSERT column list must be non-empty` |
| `INSERT INTO t(x, x) VALUES (1, 2)` | ParseError | `duplicate column x in INSERT list` |

---

## 6. Catalog 升级（D6 裁决：方案 A）

### 6.1 数据结构

```python
@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "Column": ...

@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: tuple[Column, ...]
    root_page_id: int
    next_page_id: int

    @property
    def schema(self) -> tuple[tuple[str, str], ...]:
        """只读投影片，供 row_codec 与现有 API 使用；返回 [(name,type)] 二元数组。
        仅用于不需要约束信息的旧代码路径；新代码直接读 columns。"""
        return tuple((c.name, c.type) for c in self.columns)
```

`schema` 投影保留老 API，让 row_codec / `Database.Row` / REPL 不需要一次性大改。

### 6.2 JSON 双格式加载

```python
def _load_column(item) -> Column:
    if isinstance(item, list) and len(item) == 2:
        # 旧 [name, type] 二元数组
        return Column(name=item[0], type=item[1],
                      nullable=True,      # D3 + 裁决 1
                      unique=False,
                      primary_key=False)
    if isinstance(item, dict):
        return Column.from_dict(item)
    raise InvalidDatabaseFile(f"unrecognized column entry: {item!r}")
```

写盘一律走新格式（`to_dict()`）。

兼容性测试 fixture：
- `tests/fixtures/legacy_mvp_schema.json` — 写一份旧 `[name,type]` schema 的 catalog fixture
- `tests/fixtures/new_constraints_schema.json` — 新格式对照

### 6.3 持久化迁移

无需运行时迁移：所有 `from_bytes` 调用 `_load_column` 即可识别两种形态。第一次 reopen 旧版 `.db` 时表被加载为 nullable=True；后续 `INSERT` 走新路径；如果用户显式写 NOT NULL 列未提供值，会按 nullable=True 行为通过（D3 设计）。这是兼容性与 MVP "NOT NULL 隐式" 行为的明确语义切换。

---

## 7. Executor INSERT 校验流水线

### 7.1 总入口

```python
def execute_insert(self, stmt: Insert) -> None:
    table = self.catalog.get_table(stmt.table)         # 步骤 1
    cols = self._resolve_columns(stmt.columns, table)   # 步骤 2
    self._validate_value_rows_count(stmt)               # 步骤 3
    self._validate_unique_columns(stmt.columns, table)  # parser 已检查，executor 防御性复查

    for row_values in stmt.values:
        normalized = self._normalize_row(row_values, stmt.columns, table)
        self._validate_not_null(normalized, table)
        self._validate_types(normalized, table)         # 既有路径；NULL 走约束前置
        self._validate_unique_keys(normalized, table)
        encoded = row_codec.encode_row(normalized, [c.name for c in table.columns],
                                       [c.type for c in table.columns])
        self._insert_row_to_page(table, encoded)
```

### 7.2 步骤细节

#### _normalize_row

```python
def _normalize_row(self, values: tuple, explicit_cols: tuple[str, ...],
                   table: TableInfo) -> tuple:
    full = [None] * len(table.columns)
    name_to_idx = {c.name: i for i, c in enumerate(table.columns)}
    for col_name, val in zip(explicit_cols, values):
        full[name_to_idx[col_name]] = val
    return tuple(full)
```

类型契约：所有 SQLite-style Python 值；`None` 表示 SQL NULL。

#### _validate_not_null（步骤 5）

```python
def _validate_not_null(self, row: tuple, table: TableInfo) -> None:
    for i, col in enumerate(table.columns):
        if row[i] is None and (not col.nullable or col.primary_key):
            kind = "null"
            raise ConstraintViolation(
                kind=kind,
                column=col.name,
                value=None,
            )
```

- `col.nullable=False`（NOT NULL）拒绝 None
- `col.primary_key=True` 即使 nullable=True 也拒绝 None（D5）

#### _validate_types（步骤 6）

既有 `py_to_db` 类型校验；本 change 不动它。要点：None 必须已在 _validate_not_null 阶段被拒绝，否则 `py_to_db(None, type)` 会抛类型错误（D3 默认走 NOT NULL 路径，None 不会被传入类型校验）。

#### _validate_unique_keys（步骤 7，D4 方案 A）

```python
class _UniqueGroup:
    columns: tuple[str, ...]            # 复合唯一键的列名列表（size=1 也合法）
    include_pk_overlap: bool            # 若该组与 PK 列集合相同，errors 用 duplicate_pk

def _validate_unique_keys(self, row: tuple, table: TableInfo) -> None:
    for group in self._unique_groups(table):
        key_value = tuple(row[self._col_index(c)] for c in group.columns)

        # 裁决 9（NULL UNIQUE 方案 A）：任一成员 None，跳过此组检查
        if any(v is None for v in key_value):
            continue

        # 检查已有表行
        existing_rows = self._scan_for_key_check(table, group.columns)
        seen = {tuple(r[c] for c in group.columns) for r in existing_rows}

        # 加入本批次已通过 key（防止同语句内同 key 重复）
        if key_value in self._pending_session_keys(group):
            raise ConstraintViolation(
                kind=group.kind,        # "duplicate_pk" if include_pk_overlap else "unique"
                columns=group.columns,
                value=key_value,
            )

        if key_value in seen:
            raise ConstraintViolation(
                kind=group.kind,
                columns=group.columns,
                value=key_value,
            )

        self._mark_session_key(group, key_value)
```

### 7.3 UNIQUE 组构造（D4 方案 A）

```python
def _unique_groups(self, table: TableInfo) -> list[_UniqueGroup]:
    groups: list[_UniqueGroup] = []
    pk_cols = tuple(c.name for c in table.columns if c.primary_key)
    if pk_cols:
        groups.append(_UniqueGroup(columns=pk_cols, include_pk_overlap=True))

    # 单列 UNIQUE：每个 unique=True 的列单独成组
    for col in table.columns:
        if col.unique and col.name not in pk_cols:
            groups.append(_UniqueGroup(columns=(col.name,), include_pk_overlap=False))

    # 复合 UNIQUE：MVP 不支持显式 `UNIQUE (a, b)` 表级约束；本 change 仅单列 UNIQUE
    # 复合 UNIQUE 的引入留到后续 change（见 § Spec Gap）

    return groups
```

复合 PK 是合法的（如 `CREATE TABLE t(a INT, b INT, PRIMARY KEY (a, b))` —— 但**本 change 不实现表级约束**，仅列内 `PRIMARY KEY`）。若多列都标 `PRIMARY KEY`，合并为 1 个 PK 组；这是 PK 行内写法（不是表级）的合理子集。

### 7.4 同批次重复（裁决 3）

每条 INSERT 语句独占一个 `_pending_session_keys` 集合；语句结束后清空。这防止：

```sql
INSERT INTO t(email) VALUES ('a@x'), ('a@x');  -- 第二条应报 unique
```

### 7.5 tombstone 与并发

MVP 标记删除写 tombstone（slot offset=0xFFFF）；`scan_table` 必须跳过 tombstone。MVP 已有此逻辑，本 change 复用。同批次 `_pending_session_keys` 仅记成功行。

---

## 8. ConstraintViolation 类型与 REPL 渲染

### 8.1 异常类型（D2 裁决）

```python
class ConstraintViolation(ExecutionError):
    kind: str               # "null" | "unique" | "duplicate_pk"
    column: str | None = None
    columns: tuple[str, ...] | None = None
    value: Any = None

    def __str__(self) -> str:
        parts = [f"kind={self.kind!r}"]
        if self.column is not None:
            parts.append(f"column={self.column!r}")
        if self.columns is not None:
            parts.append(f"columns={list(self.columns)!r}")
        if self.value is not None:
            parts.append(f"value={self.value!r}")
        return f"ConstraintViolation({', '.join(parts)})"
```

继承 `ExecutionError`：REPL 已有 `except Exception` 路径捕获；加上 `kind` 字段后，子异常在 API 层仍按 `ExecutionError` 处理；REPL 输出仍以单行 `ERROR:` 格式呈现。

### 8.2 REPL 渲染（裁决 7：方案 A — 专门渲染）

```python
# src/tinydb/repl.py
def _format_exception(exc: Exception) -> str:
    if isinstance(exc, ConstraintViolation):
        # 专门渲染：D1 错误信息含 kind/columns/value
        return f"ERROR: {type(exc).__name__}({exc._repr_args()})"
    if isinstance(exc, ExecutionError):
        return f"ERROR: {type(exc).__name__}: {exc}"
    return f"ERROR: {type(exc).__name__}: {exc}"
```

REPL `_run_sql` 调用替换为 `_format_exception`。该函数挂在 `repl.py` 私有命名空间下，不改 `Database.execute` 返回行为。

`tests/integration/test_repl_process.py` 增加：
- `test_repl_constraint_violation_renders_kind` — `INSERT NOT NULL 列 NULL` 输出 `ERROR: ConstraintViolation(kind='null', column='x')`
- `test_repl_unique_violation_renders_columns` — `INSERT 重复 email` 输出含 `columns=['email']` 字串
- `test_repl_loop_continues_after_constraint_violation` — 错误后下一条 SQL 仍执行（与 MVP 既有错误路径一致）

### 8.3 Python API 层

`Database.execute()` 抛 `ConstraintViolation` 给调用方；调用方按 `except ConstraintViolation as e` 接住，按 `e.kind` / `e.columns` 处理。文档化示例：

```python
try:
    db.execute("INSERT INTO users(email) VALUES ('dup@x')")
except ConstraintViolation as e:
    if e.kind == "unique":
        # 处理 unique 冲突
        ...
```

---

## 9. NULL 语义（UNIQUE vs PRIMARY KEY）— 裁决 9

### 9.1 PRIMARY KEY 与 NULL

PRIMARY KEY 列上的 `NULL` 在 executor `_validate_not_null` 阶段触发：
- 即使 `col.nullable=True`（MVP 旧表兼容路径），PK 列仍强制 NOT NULL（D5 合并）
- 触发 `ConstraintViolation(kind="null", column=...)`
- 永远不会进入 `_validate_unique_keys` 阶段

### 9.2 UNIQUE 列与 NULL

UNIQUE 列 tuple 任一成员为 `NULL` 时，**跳过整个组的检查**：
- 多个含 NULL 的 UNIQUE tuple 在同一表内并存合法
- 同批次（同一 INSERT 语句）含 NULL 的 UNIQUE tuple 也并存合法
- 仅在 tuple **完全无 NULL** 时才查重复

理由（裁决 9）：
- 与 PostgreSQL / MySQL 默认行为一致；教学型 DB 应贴最广为人知的语义
- PRIMARY KEY 与 NULL 处理保持正交：PK 列显式 NULL 走 `kind="null"`，永远不会通过 PK 阶段
- 复合 UNIQUE 任一成员 NULL 就跳整个组；这是 SQL 标准解释，不是"部分匹配"

### 9.3 反例

| 场景 | 期望行为 |
|------|----------|
| `t(col INT UNIQUE)` 三行 `(1), (NULL), (NULL)` | 全部接受；UNIQUE 不报 |
| `t(col INT UNIQUE)` 三行 `(1), (1), (2)` | 第二行拒绝 `kind=unique` |
| `t(col INT PRIMARY KEY)` 一行 `(NULL)` | 拒绝 `kind=null` |
| `t(col INT PRIMARY KEY)` 一行 `(1), (1)` | 第二行拒绝 `kind=duplicate_pk` |
| `t(a INT, b INT, UNIQUE 复合预计后续支持)` | 暂不实现，留 Spec Gap |

### 9.4 边界

- 多行 INSERT 中：第一行 `NULL` 通过 UNIQUE；第二行 `(2)` 通过 UNIQUE；第三行 `(2)` 拒绝。第一行 `(NULL), (NULL)` 都通过 UNIQUE。

---

## 10. 多行 INSERT 行为（裁决 3：方案 A — 逐行落盘）

### 10.1 失败半成品保留

按"无事务"边界，多行 INSERT 中第三行触发 ConstraintViolation 时：

```
[SUCCESS] row 1: 已落盘 page N
[SUCCESS] row 2: 已落盘 page N
[FAILURE] row 3: ConstraintViolation(kind="unique")
```

- row 1 与 row 2 保留在表里（即使 row 3 失败）
- 单行 INSERT 失败时等价于"未写入"
- 多行 INSERT 中途失败**不抛** `MultiRowPartialInsert`；仅抛 `ConstraintViolation`，消息中含 `value=...` 标识是哪一行

### 10.2 实现要点

执行器维持 `_pending_session_keys` 缓存；在 `_validate_unique_keys` 抛错前已落盘的行不受影响（已被 page 写入路径独立处理）。INSERT 整句返回 `[]`（与 MVP 一致）。

### 10.3 性能

`_pending_session_keys` 在 INSERT 句外置零（`finally`）。句内累积键为 O(batch_size × unique_groups)；MVP batch_size 通常 1，无需优化。

---

## 11. 测试矩阵

### 11.1 单元（`tests/unit/test_constraints_parser.py`）

| Test | 覆盖点 |
|------|--------|
| `test_create_table_not_null` | NOT NULL |
| `test_create_table_unique` | UNIQUE |
| `test_create_table_primary_key` | PRIMARY KEY |
| `test_create_table_all_three_combined` | 三种子句全在一列 |
| `test_create_table_no_constraint_implies_nullable_true` | 缺省 nullable=True |
| `test_create_table_rejects_bare_null` | 裁决 2 方案 A：`x INT NULL` 拒绝 |
| `test_create_table_constraint_order_independent` | UNIQUE NOT NULL PRIMARY KEY 顺序无碍 |
| `test_create_table_duplicate_unique_constraint` | 双 UNIQUE 报错 |
| `test_create_table_duplicate_primary_key` | 双 PRIMARY KEY 报错 |
| `test_create_table_too_many_constraints_rejected` | 语法违例 |
| `test_insert_null_literal_accepted` | `VALUES (NULL)` |
| `test_insert_null_in_unique_column` | executor 验证：UNIQUE 接受 NULL |
| `test_insert_null_in_pk_column_rejected` | executor 验证：PK 拒绝 NULL |
| `test_insert_composite_pk_deduped_into_one_group` | 多列 PRIMARY KEY → 一组 |
| `test_insert_composite_unique_columns` | 多列同 UNIQUE → 一组（MVP 暂无场景，留 TODO） |

### 11.2 单元（`tests/unit/test_constraints_executor.py`）

| Test | 覆盖点 |
|------|--------|
| `test_executor_insert_rejects_null_on_not_null` | kind=null |
| `test_executor_insert_rejects_null_on_pk` | kind=null 即使 nullable=True |
| `test_executor_insert_rejects_duplicate_unique` | kind=unique |
| `test_executor_insert_rejects_duplicate_pk` | kind=duplicate_pk |
| `test_executor_insert_unique_with_nulls_all_pass` | 三个 NULL 通过 |
| `test_executor_insert_unique_one_null_two_concrete_dup` | `(NULL, 1), (NULL, 1)` 第二行 kind=unique |
| `test_executor_insert_composite_pk_dup` | `(1, 2)` 重复报 duplicate_pk |
| `test_executor_insert_omitted_column_becomes_none` | 裁决 5 方案 A |
| `test_executor_insert_unknown_column_rejected` | 裁决 5 方案 A |
| `test_executor_insert_duplicate_column_rejected` | 裁决 5 方案 A |
| `test_executor_insert_multi_row_partial_failure_keeps_successful_rows` | 裁决 3 方案 A |
| `test_executor_unique_groups_pk_overlap` | 裁决 4 方案 A：PK 优先 |

### 11.3 集成（`tests/integration/test_catalog_constraints.py`）

| Test | 覆盖点 |
|------|--------|
| `test_catalog_loads_new_format_roundtrip` | 新格式 in/out 一致 |
| `test_catalog_loads_legacy_mvp_format` | 旧 `[name,type]` 兼容 |
| `test_catalog_legacy_format_nullable_true_default` | D3 |
| `test_catalog_corrupted_format_raises_invalid_db_file` | 错误格式拒绝 |
| `test_catalog_rejects_mixed_old_and_new_columns` | 同 schema 中不可混 |
| `test_executor_legacy_table_insert_with_no_value_still_rejected` | D3 行为切换显式测 |
| `test_executor_constraints_persist_across_reopen` | 落盘 + reload |

### 11.4 集成（`tests/integration/test_constraints_repl.py`）

| Test | 覆盖点 |
|------|--------|
| `test_repl_constraint_violation_renders_kind_null` | 单行 REPL 输出 |
| `test_repl_constraint_violation_renders_kind_unique` | 单行 REPL 输出 |
| `test_repl_constraint_violation_renders_kind_duplicate_pk` | 单行 REPL 输出 |
| `test_repl_loop_continues_after_constraint_violation` | 错误后 `OK` 仍出 |

### 11.5 e2e golden（`tests/e2e/sql/constraints/`）

8 条 SQL：覆盖每种错误路径 + happy path + 多行 partial。

### 11.6 回归

- MVP 234 个测试 + engine-v1 后续测试保持全绿
- `tests/integration/test_persistence.py`：旧版 .db 文件能升级加载

---

## 12. Spec Gap（裁决 8：方案 A — 仅 Design Doc 记录，不创建 delta spec）

下列 11 个 scenario 在本次 change 的 `openspec/changes/tinydb-constraints/specs/` 中**缺失**，由 build/verify 阶段标注，归档阶段决定是否合并：

| Scenario | 描述 |
|----------|------|
| `null-in-not-null-column-rejected` | NOT NULL 列写 NULL 拒 |
| `null-in-pk-column-rejected` | PK 列写 NULL 拒 |
| `duplicate-unique-column-rejected` | UNIQUE 列重复拒 |
| `duplicate-pk-rejected` | PK 重复拒 |
| `multiple-nulls-in-unique-column-allowed` | NULL-UNIQUE 允许多 NULL |
| `composite-pk-rejected-on-duplicate` | 多列 PK 重复拒 |
| `constraint-persists-across-reopen` | 约束落盘 + reload |
| `insert-omitted-column-becomes-null` | 省略列 None 化 |
| `insert-unknown-column-rejected` | 未知列拒 |
| `insert-duplicate-column-rejected` | 重复列拒 |
| `multi-row-partial-failure` | 多行 partial 失败保留 |

这 11 个 scenario 的覆盖测试**已经写在本文 §11 测试矩阵中**。在 build 阶段会作为 Task 5.x 的实现验证条件；verify 阶段报告其对应到 spec 的 traceability。归档时一次性决定回写到主 spec 还是仅留 trace。

---

## 13. 风险与缓解

| ID | 风险 | 严重度 | 缓解 |
|----|------|--------|------|
| R1 | catalog JSON 升级后旧 .db 拒绝加载 | HIGH | 兼容加载路径必须测；提供 fixture |
| R2 | UNIQUE 全表扫描在 n=10000 时单条 INSERT 100ms 以上 | MEDIUM | 计时 fixture 验证 n=1000 < 100ms；n 超出由 engine-v2 接管 |
| R3 | parser 新增 NULL 关键字破坏 MVP 路径 | HIGH | tokenizer 区分大小写 + 多测覆盖 |
| R4 | PRIMARY KEY 双重约束校验顺序错乱 | MEDIUM | 显式断言 violation.kind |
| R5 | 多行 INSERT partial 失败被解读为"半成品"是 bug | LOW | 单元 + 集成覆盖；README 段落显式说明 |
| R6 | ColumnDefinition 引入让所有 CreateTable 测试改写 | MEDIUM | 一次性完成；用 property-based 重写覆盖 boundary |
| R7 | ConstraintViolation 子异常可能在外部代码 `except ExecutionError` 漏接 | LOW | REPL/API 已有路径；写完测试在 docs 标记 |
| R8 | _pending_session_keys 在异常路径漏清空 | LOW | `try/finally` 强制清空 |

---

## 14. 模块行数预算

| 模块 | 当前 MVP | 本 change 上调 | 累计上限 |
|------|----------|----------------|----------|
| `parser.py` | ~600 | +~150 | ≤ 750 |
| `executor.py` | ~400 | +~220 | ≤ 620 |
| `catalog.py` | ~100 | +~30 | ≤ 130 |
| `tokenizer.py` | ~200 | +~10 | ≤ 210 |
| `errors.py` | ~30 | +~25 | ≤ 55 |
| `repl.py` | ~291 | +~15 | ≤ 310 |

理由：parser 升级 AST 节点（含 NULL 字面量）；executor 升级校验流水线 + 同批次键缓存；catalog 升级 `Column` 与 JSON 双格式加载。

---

## 15. 决策清单（完整版）

### Open 阶段决定（proposal.md）

- **D1**：parser 不强制 PRIMARY KEY = NOT NULL + UNIQUE；executor 合并
- **D2**：`ConstraintViolation` 继承 `ExecutionError`
- **D3**：旧 catalog 反序列化默认 nullable=True
- **D4**：UNIQUE 复合键用 set 而非 hash
- **D5**：PRIMARY KEY 列强制 NOT NULL 行为在 executor 校验期合并

### 第二轮九点裁决（用户已确认，全部 A）

- **R1**：分层模型 — parser frozen `ColumnDefinition` + catalog frozen `Column` + executor 显式映射
- **R2**：裸 NULL 列子句 — 仅 NOT NULL；拒绝 `x INT NULL`
- **R3**：多行 INSERT — 逐行校验/落盘；后续失败保留先前成功行；不引入事务
- **R4**：kind 固定 `null | unique | duplicate_pk`；PK 与完全相同 UNIQUE 组只保留 PK 组
- **R5**：INSERT 列归一化 — 显式列清单必填；未知/重复列拒绝；省略列 None
- **R6**：catalog JSON — 新 object 格式 + 旧 `[name,type]` 兼容；`TableInfo.columns` tuple + 只读 schema 投影
- **R7**：REPL `ERROR: ConstraintViolation(kind=..., column=..., columns=...)` 专门渲染
- **R8**：Spec Gap 仅 Design Doc 记录，不创建 delta spec
- **R9**：UNIQUE 列 tuple 含 NULL 跳过冲突；PK 列 NULL 仍先报 null

---

## 16. Implementation Sequence（给 build 阶段 implementer）

按 tasks.md §1-7 子任务顺序：

1. **catalog.py 升级**：`Column` dataclass + `TableInfo.columns` + 双格式 `from_bytes` + fixture 测试
2. **tokenizer.py**：`NOT` / `NULL` / `PRIMARY` / `KEY` / `UNIQUE` 关键字 + NULL 字面量上下文判断
3. **parser.py**：`ColumnDefinition` AST + 列约束子句解析 + Insert 列清单检查 + Insert `Literal(None)` 路径
4. **errors.py**：`ConstraintViolation` 子异常 + str/repr 契约测试
5. **executor.py**：步骤 1-4 后写执行期流水线 + `_validate_not_null` + `_validate_unique_keys` + `_pending_session_keys`
6. **repl.py**：`_format_exception` 单行渲染
7. **回归**：MVP 234 + engine-v1 既有测试 + 新 47 个左右的测试

---

## 17. 关键算法伪代码（再补充 §3 流水线完整形态）

```python
def execute(self, stmt) -> list[Row]:
    if isinstance(stmt, Insert):
        return self._exec_insert(stmt)
    elif isinstance(stmt, Select):
        return self._exec_select(stmt)
    elif isinstance(stmt, Delete):
        return self._exec_delete(stmt)
    elif isinstance(stmt, CreateTable):
        self._exec_create_table(stmt)
        return []
    ...

def _exec_create_table(self, stmt: CreateTable) -> None:
    # parser 已校验：列名不重复、关键字不冲突、约束子句不重复
    # executor 仅做映射与持久化
    cols: list[Column] = []
    seen = set()
    for cd in stmt.columns:
        if cd.name in seen:
            raise ExecutionError(f"duplicate column {cd.name}")
        seen.add(cd.name)
        cols.append(Column(
            name=cd.name, type=cd.type,
            nullable=cd.nullable, unique=cd.unique,
            primary_key=cd.primary_key,
        ))
    cols_tuple = tuple(cols)

    # 同时收集 PK 列；同列名不允许同时 PK + UNIQUE（裁决 4）
    pk_cols = tuple(c.name for c in cols_tuple if c.primary_key)
    unique_cols = tuple(c.name for c in cols_tuple if c.unique and not c.primary_key)
    if len(pk_cols) >= 2 or (pk_cols and unique_cols and set(pk_cols) == set(unique_cols)):
        # 这种情况下 PK 已覆盖；保留 PK，不新增 UNIQUE 组
        pass

    new_table = TableInfo(
        name=stmt.name,
        columns=cols_tuple,
        root_page_id=self.pager.alloc_page(),
        next_page_id=0,
    )
    self.catalog.add_table(new_table)
    self._persist_catalog()
```

```python
def _exec_insert(self, stmt: Insert) -> list[Row]:
    ti = self.catalog.get_table(stmt.table)            # 1
    self._validate_insert_columns(stmt.columns, ti)    # 2
    self._validate_value_rows_count(stmt)              # 3
    self._init_session_keys()
    try:
        for row_values in stmt.values:                 # 每行
            normalized = self._normalize_row(row_values, stmt.columns, ti)
            self._validate_not_null(normalized, ti)    # 5
            self._validate_types(normalized, ti)       # 6（仅非 NULL）
            self._validate_unique_keys(normalized, ti) # 7
            encoded = row_codec.encode_row(
                [v for v in normalized],
                [c.name for c in ti.columns],
                [c.type for c in ti.columns],
            )
            self._do_insert(ti, encoded)              # 8
        return []
    finally:
        self._clear_session_keys()
```

---

## 18. 附录 A — JSON 双格式示例

旧格式 fixture `tests/fixtures/legacy_mvp_schema.json`：

```json
{
  "tables": {
    "users": {
      "schema": [["id", "INT"], ["name", "TEXT"]],
      "root_page_id": 2,
      "next_page_id": 0
    }
  }
}
```

新格式 fixture `tests/fixtures/new_constraints_schema.json`：

```json
{
  "tables": {
    "users": {
      "schema": [
        {"name": "id", "type": "INT", "nullable": false, "unique": false, "primary_key": true},
        {"name": "email", "type": "TEXT", "nullable": false, "unique": true, "primary_key": false},
        {"name": "name", "type": "TEXT", "nullable": false, "unique": false, "primary_key": false}
      ],
      "root_page_id": 2,
      "next_page_id": 0
    }
  }
}
```

混合格式 fixture `tests/fixtures/mixed_invalid_schema.json`：

```json
{"tables": {"bad": {"schema": [["id", "INT"], {"name": "x", "type": "TEXT"}], ...}}}
```

`_load_column` 对同表内混合 schema 必须抛 `InvalidDatabaseFile`。

---

## 19. 附录 B — REPL 期望输出

输入：
```
sqlite> CREATE TABLE u(id INT PRIMARY KEY, email TEXT UNIQUE);
sqlite> INSERT INTO u(id, email) VALUES (1, 'a@x');
sqlite> INSERT INTO u(id, email) VALUES (1, 'b@x');
sqlite> INSERT INTO u(id, email) VALUES (NULL, 'c@x');
sqlite> INSERT INTO u(id, email) VALUES (2, 'a@x');
```

期望输出：
```
OK
OK
ERROR: ConstraintViolation(kind='duplicate_pk', columns=['id'], value=(1,))
ERROR: ConstraintViolation(kind='null', column='id', value=None)
ERROR: ConstraintViolation(kind='unique', columns=['email'], value=('a@x',))
sqlite>
```

每行错误是单行；loop 继续。整体行为与 MVP `ERROR: ParseError:` 风格一致。

---

## 20. 附录 C — 与其它 change 的接口契约

### 给 `tinydb-engine-v1`

- UPDATE 路径暂不引入约束校验（按 proposals 范围）；后续 merge 时由 engine-v1 接入 `_validate_not_null` + `_validate_unique_keys`（步骤 5、7）。
- `ColumnDefinition` 字段不变；engine-v1 不需要触碰 parser 升级部分。

### 给 `tinydb-engine-v2`

- `_validate_unique_keys` 的 O(n) 扫描被替换为 `IndexManager.lookup(table, col, value)`；接口不变。
- 复合 UNIQUE 在索引版本中表达为多列 B-tree key，executor 路径不变。

### 给 `tinydb-acid`

- INSERT 在事务中按行校验；commit 时落盘。rollback 时不应用任何 partial。
- WAL 写入对 row codec 不变；本 change 不改页格式。
