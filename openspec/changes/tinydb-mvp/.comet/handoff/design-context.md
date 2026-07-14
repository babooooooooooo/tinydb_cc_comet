# Comet Design Handoff

- Change: tinydb-mvp
- Phase: design
- Mode: compact
- Context hash: 5481d164ea2f46920f5b2b99cc3041e925c4208c521e1cb45f784dab74c09725

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tinydb-mvp/proposal.md

- Source: openspec/changes/tinydb-mvp/proposal.md
- Lines: 1-61
- SHA256: 09da97c194c09daed7b36ba395545f24aba0db1aff67b57f8a401775f40bb553

```md
# Proposal: tinydb-mvp

> **范围声明**：本 change 是 `tinydb` 项目的**第一个里程碑（最小可演示版）**，是后续 `tinydb-acid`（事务）与 `tinydb-engine-v2`（SQL/索引/CLI 扩展）的基石。完整愿景参见仓库根目录的 `tinydb-proposal.md`。

## Why

要从零构建一个可教学的 Python 嵌入式关系型数据库，第一阶段必须交付一个**能跑通端到端最小路径**的可演示版本：纯 Python、单文件持久化、最简 SQL 子集、Python API 直调。如果第一阶段就追求 7 大能力齐备，代码会膨胀到无法教学拆解；如果第一阶段只做底层存储不做 SQL，则无法验证"存储 + 解析 + 执行"三层拼装的正确性。MVP 是两端之间的最小可用切片，能在三周内交付并被一名中高级 Python 开发者 30 分钟内读完核心模块。

## What Changes

- **新增** 单文件页式存储引擎（slotted pages），支持页头 / 槽位 / 空闲空间回收 / 文件魔术字
- **新增** 基础类型系统（INT/TEXT/FLOAT/BOOL），含类型校验、字面量解析、Python ↔ DB 类型转换
- **新增** 最小 SQL 解析器，覆盖 5 个语句：`CREATE TABLE` / `DROP TABLE` / `INSERT INTO` / `SELECT ... FROM` / `DELETE FROM`，WHERE 暂只支持 `col = literal`（无 AND/OR、无 UPDATE、无子查询）
- **新增** 执行器：DDL 创建/删除表；DML 插入 / 全表扫描 / 等值过滤 / 标记删除
- **新增** Python API：`Database(path).execute(sql) -> list[Row]`，库入口与 `tinydb` 顶层包名
- **新增** 端到端 SQL golden 测试集（`tests/e2e/sql/`），纯 Python + pytest，不引入 sqlite3 验证
- **新增** 文档：README + 模块导览（指出每个模块的预期行数上限）
- **暂不引入** WAL、UPDATE、AND/OR 复合条件、ORDER BY/LIMIT、聚合、B-tree、索引、CLI、扩展类型——这些都在后续两个 change 的范围内

## Capabilities

### New Capabilities

- `type-system-basic`：4 个基础类型（INT/TEXT/FLOAT/BOOL）的字面量解析、存储编码、解码校验、Python ↔ DB 双向转换；严格类型（不允许隐式转换）；FLOAT 拒绝 inf/NaN
- `storage-engine`：单文件持久化，slotted pages（每页 = header + slots 数组 + free space），文件头魔术字，固定页大小（4KB），表元数据页（catalog），全表扫描 + 等值定位 API
- `sql-minimal-parser`：tokenizer（识别关键字 / 标识符 / 字面量 / 运算符 / 括号 / 分号）+ recursive descent parser，输出 AST，覆盖 5 个 DDL/DML 语句；解析错误抛出带行号的 `ParseError`
- `python-api`：`tinydb` 顶层包入口；`Database` 类（接受 file 路径或 `:memory:`）；`execute(sql) -> list[Row]`；`Row` 支持按列名访问

### Modified Capabilities

无（项目从零开始，`openspec/specs/` 当前为空）

## Impact

- 新增 Python 包 `tinydb`，零运行时依赖
- 单一 `.db` 文件作为数据存储格式（首个 MVP 版）
- 新增 dev 依赖：`pytest`（核心）、`hypothesis`（属性测试，仅在 storage 模块涉及）
- 公共接口仅 `tinydb.Database` 一类；后续 change 会扩展但不破坏 MVP API 形状
- 不影响外部系统（单进程、单线程、嵌入式）
- 模块行数预算（硬约束）：
  - `type_system.py` ≤ 150 行
  - `pager.py` ≤ 250 行
  - `slotted_page.py` ≤ 150 行
  - `catalog.py` ≤ 100 行
  - `tokenizer.py` ≤ 200 行
  - `parser.py` ≤ 600 行
  - `executor.py` ≤ 400 行
  - `database.py` ≤ 100 行
  - 违反预算 = 违反 MVP 教学定位

## Out of Scope（本 change 明确不做）

- ACID 事务 / WAL / 崩溃恢复 → 留 `tinydb-acid`
- UPDATE 语句、WHERE 的 AND/OR/IN/LIKE → 留 `tinydb-engine-v2`
- ORDER BY、LIMIT、OFFSET、聚合、GROUP BY → 留 `tinydb-engine-v2`
- B-tree / 哈希索引 → 留 `tinydb-engine-v2`
- 列约束（PRIMARY KEY、NOT NULL、UNIQUE）→ 留 `tinydb-engine-v2`
- 扩展类型（VARCHAR、CHAR、DECIMAL、DATE、TIME、TIMESTAMP、SMALLINT、BIGINT）→ 留 `tinydb-engine-v2`
- CLI / REPL / psql 风格界面 → 留 `tinydb-engine-v2`
- 并发安全（多线程 / 多进程 / 文件锁）→ 不在任何 change 范围内（永久 out）
- ALTER TABLE、视图、触发器、外键、JOIN、用户/权限、网络协议 → 不在任何 change 范围内（永久 out）

```

## openspec/changes/tinydb-mvp/design.md

- Source: openspec/changes/tinydb-mvp/design.md
- Lines: 1-148
- SHA256: 56cc545e5a34ddad671833dd6d979c60a522747954d7048d0896577ba3c15452

[TRUNCATED]

```md
# Design: tinydb-mvp

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

`tinydb` 是一个从零构建的 Python 嵌入式关系型数据库（项目愿景见仓库根目录 `tinydb-proposal.md`）。MVP 阶段是这个项目的"第一个里程碑切片"，目标是交付一个能端到端跑通最小 SQL 子集的可教学存储引擎。本设计解决两个核心问题：

1. 用什么样的文件 / 页 / 行编码结构，让存储层既"足够真实"又能用纯 Python 在 ~150-250 行内表达清楚？
2. SQL 解析器、executor、storage 三层如何解耦，才能让每层都能被独立教学？

约束：
- 纯 Python 实现，零运行时依赖
- 单文件持久化，单进程单线程
- 不与 SQLite 拼性能或兼容性
- 每个模块行数预算上限（已在 proposal 中声明）必须被尊重，违规即返工

## Goals / Non-Goals

**Goals：**
- 端到端打通 `CREATE TABLE → INSERT → SELECT WHERE col = x → DELETE`
- 模块边界清晰，每个模块可被一名中高级 Python 开发者 30 分钟内读完
- 4 层测试金字塔（unit / integration / e2e / property）覆盖核心模块 ≥85%
- 文件格式与 module 名稳定到足以承载后续 `tinydb-acid`（WAL 接入）和 `tinydb-engine-v2`（SQL/索引扩展）

**Non-Goals（本期明确不做）：**
- ACID / WAL / 崩溃恢复（→ `tinydb-acid`）
- UPDATE、WHERE AND/OR/IN/LIKE、ORDER BY、LIMIT、聚合、JOIN（→ `tinydb-engine-v2`）
- 索引、列约束、扩展类型、CLI（→ `tinydb-engine-v2`）
- 并发安全（永久 out）
- 性能基准（学习项目不参与 SQLite 同维度比较）

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Python API 层                              │
│   tinydb.Database(file).execute(sql_str) → list[Row]            │
│   tinydb.Database(path) / tinydb.Database(':memory:')           │
└─────────────────────────────┬───────────────────────────────────┘
                              │ SQL 字符串
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     SQL 解析管线                                  │
│   tokenizer.scan(sql) → list[Token]                             │
│   parser.parse(tokens) → ASTNode                                │
│        ├─ CreateTable / DropTable (DDL)                          │
│        ├─ Insert / Select / Delete (DML)                         │
│        └─ ParseError(line, col, message)                         │
└─────────────────────────────┬───────────────────────────────────┘
                              │ AST
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                          Executor                                │
│   Executor(pager, catalog).run(stmt) → list[Row]                │
│        ├─ DDL: catalog.{create,drop}_table                       │
│        └─ DML: scan / filter / project / mutate                 │
└─────────────────────────────┬───────────────────────────────────┘
                              │ row reads/writes
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Storage 引擎                                │
│                                                                  │
│   ┌─────────── File (.db) ──────────────────────────────────┐   │
│   │ Page 0  │ Page 1    │ Page 2    │ Page 3    │ Page 4+  │   │
│   │ header  │ catalog   │ table A   │ table A   │ 空闲     │   │
│   │(magic,  │(tables[], │(slotted   │(slotted   │          │   │
│   │ version)│ root_pg)  │ rows)     │ rows)     │          │   │
│   └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**层职责边界**：
- **API 层**：对外接口，不感知 SQL 内部表示
- **解析器**：纯函数，不持有状态、不调 I/O
- **Executor**：唯一同时持有 `Pager` + `Catalog` 的层；所有 I/O 在这里发生
- **Pager**：单文件 mmap，每次操作拉一页进内存，对外暴露 `read_page(id)` / `write_page(id)` / `alloc_page()`
- **Catalog**：表名 → (root_page_id, schema) 映射，序列化为 Page 1

## Decisions

```

Full source: openspec/changes/tinydb-mvp/design.md

## openspec/changes/tinydb-mvp/tasks.md

- Source: openspec/changes/tinydb-mvp/tasks.md
- Lines: 1-113
- SHA256: 40ceb0c40da6f2d0d2c0ffec39b3976089e9abd8734853d809addecc35f27f92

[TRUNCATED]

```md
# Tasks: tinydb-mvp

> **实施起点**：所有任务基于 `proposal.md` + `design.md` + `specs/*.md` 已确认产物。
> **TDD 模式**：全任务采用 Test-Driven Development（`tdd_mode: tdd`），每个任务遵循 "红→绿→重构" 循环。
> **预算红线**：模块行数上限见 `proposal.md` "Impact" 段落；任何任务实施后超过 = 违反 MVP 教学定位，需拆分子任务。

## 1. 项目骨架与配置

- [ ] 1.1 创建 `src/tinydb/` 与 `tests/` 目录，编写 `pyproject.toml`（声明 `tinydb` 包名、零运行时依赖、dev 依赖 `pytest>=7`、`hypothesis>=6`）
- [ ] 1.2 编写 `src/tinydb/__init__.py`，导出 `Database`、`Row`、`__version__ = "0.1.0"` 与异常类 `ParseError`、`ExecutionError`、`InvalidDatabaseFile`、`UnsupportedSchemaVersion`
- [ ] 1.3 编写 `README.md`（说明 MVP 范围、非 ACID 警示、快速开始示例）
- [ ] 1.4 编写 `pytest.ini`（或 `pyproject.toml` 中 `[tool.pytest.ini_options]`），启用 strict markers
- [ ] 1.5 创建空模块占位文件：`type_system.py`、`pager.py`、`slotted_page.py`、`catalog.py`、`tokenizer.py`、`parser.py`、`executor.py`、`database.py`、`errors.py`，每个写好 docstring 声明模块职责

## 2. 类型系统（spec: type-system-basic）

- [ ] 2.1 编写 `tests/unit/test_type_system.py`，红：覆盖 `specs/type-system-basic/spec.md` 中所有 Scenario 用例
- [ ] 2.2 实现 `type_system.py::encode_int / decode_int`（8-byte big-endian），绿：跑通 INT roundtrip + OverflowError
- [ ] 2.3 实现 `type_system.py::encode_text / decode_text`（length-prefixed UTF-8），绿：跑通 TEXT roundtrip + UnicodeEncodeError
- [ ] 2.4 实现 `type_system.py::encode_bool / decode_bool`（1 字节 0/1），绿：跑通 BOOL roundtrip
- [ ] 2.5 实现 `type_system.py::encode_float / decode_float`（`struct.pack('>d', v)`），绿：跑通 FLOAT roundtrip
- [ ] 2.6 在 `tokenizer.py` 中实现 4 个字面量识别（`parse_int_literal`、`parse_float_literal`、`parse_text_literal`、`parse_bool_literal`），绿：跑通 4 类型字面量解析 + NaN/Inf 拒绝
- [ ] 2.7 实现 `type_system.py::py_to_db(value, column_type)` 与 `db_to_py(bytes, column_type)`，绿：跑通所有 Python ↔ DB 转换用例（含 float NaN 拒绝、float→INT 拒绝）
- [ ] 2.8 实现 `type_system.py::validate_compare(col_value, lit_value)` 用于 executor 严格类型守卫，绿：跑通 strict type coercion rejection 用例

## 3. 存储引擎 · Pager 层（spec: storage-engine, file/page 部分）

- [ ] 3.1 编写 `tests/integration/test_pager.py`，红：覆盖文件创建、magic 校验、版本校验、`:memory:` 模式、page alloc/read/write 行为
- [ ] 3.2 实现 `pager.py::Pager` 类，接受 path 或 `:memory:`，初始化时按需 open 或 create
- [ ] 3.3 实现 page 0 文件头：`MAGIC = b'TINYDB\x00\x01'` + `SCHEMA_VERSION = 0x01`；open 已有文件时强制 magic 校验，绿：跑通 magic / version 异常用例
- [ ] 3.4 实现 `_path_for(page_id)` 与 `read_page(page_id)` / `write_page(page_id, bytes)` / `alloc_page()`，文件 backed 模式用 `mmap`，`:memory:` 用 bytearray 模拟，绿：跑通 page addressing 用例
- [ ] 3.5 实现 `Pager.close()` 释放 mmap 与文件句柄；`Database.__exit__` 中调用

## 4. 存储引擎 · Slotted Page + 行编码（spec: storage-engine, page layout 部分）

- [ ] 4.1 编写 `tests/unit/test_slotted_page.py`，红：覆盖 insert / update / tombstone / slot reuse / null bitmap / page full
- [ ] 4.2 实现 `slotted_page.py::SlottedPage` 数据类，持有 `page_id`、`num_slots`、`free_offset`、`slots: list[Slot]`、`data: bytearray`
- [ ] 4.3 实现 `SlottedPage.from_bytes(page_id, bytes)` 与 `to_bytes()` 序列化格式，绿：跑通 roundtrip
- [ ] 4.4 实现 `SlottedPage.insert(row_bytes)`：tombstone 优先复用，否则 append 到末尾；返回 slot id；满则 raise `PageFull`，绿：跑通 insert 用例
- [ ] 4.5 实现 `SlottedPage.delete(slot_id)`：标记 tombstone（offset=0xFFFF），绿：跑通 tombstone 用例
- [ ] 4.6 实现 `SlottedPage.update(slot_id, row_bytes)`：同长或更短则在原位覆盖，否则 raise，绿：跑通 in-place update 用例
- [ ] 4.7 实现 `SlottedPage.get(slot_id)`：返回解码后的字节或 None（tombstone）
- [ ] 4.8 在 `type_system.py` 或新文件 `row_codec.py` 中实现 `encode_row(values, schema)` 与 `decode_row(bytes, schema)`，含 null bitmap，绿：跑通 row encoding 用例

## 5. 存储引擎 · Catalog（spec: storage-engine, catalog 部分）

- [ ] 5.1 编写 `tests/integration/test_catalog.py`，红：覆盖 register / lookup / persist across reopen / drop
- [ ] 5.2 实现 `catalog.py::Catalog` 数据类：`tables: dict[name, TableInfo]`，`TableInfo = (schema, root_page_id, next_page_id)`
- [ ] 5.3 实现 `Catalog.from_bytes(page1_bytes)` / `to_bytes()`：序列化为 JSON（候选 Q1，MVP 优先 JSON）
- [ ] 5.4 在 Pager 中预留 page 1 给 catalog，新增表时 alloc 一个 page 作为 root_page，落盘在 page 1
- [ ] 5.5 实现 `Catalog.create_table(name, schema)` / `drop_table(name)` / `get_table(name)`，绿：跑通 catalog 用例

## 6. SQL · Tokenizer（spec: sql-minimal-parser tokenizer 部分）

- [ ] 6.1 编写 `tests/unit/test_tokenizer.py`，红：覆盖 identifier / keyword / int / float / text literal（含 doubled single-quote）/ boolean / punctuation / position tracking / TokenError
- [ ] 6.2 实现 `tokenizer.py::tokenize(sql)` 主循环：跳过空白、跟踪 line/col、按字符分类（alpha→identifier or keyword、digit→number、'→text、字母 T/F→bool）
- [ ] 6.3 实现关键字字典（CREATE / TABLE / DROP / INSERT / INTO / VALUES / SELECT / FROM / WHERE / TRUE / FALSE / INT / TEXT / FLOAT / BOOL），绿：跑通 keyword 大小写不敏感用例
- [ ] 6.4 实现 integer / float / text literal 三种字面量解析，text 含 doubled-quote 转义，绿：跑通字面量用例
- [ ] 6.5 实现 boolean literal（识别 TRUE / FALSE token），连接到 type_system 的字面量拒绝逻辑，绿：跑通 bool literal
- [ ] 6.6 实现 punctuation（`( ) , ; = *`），绿：跑通 punctuation 用例
- [ ] 6.7 错误路径：`TokenError(line, col, message)`，绿：跑通 `@` 报 TokenError 用例

## 7. SQL · Parser（spec: sql-minimal-parser parser 部分）

- [ ] 7.1 编写 `tests/unit/test_parser.py`，红：覆盖 5 个语句的 AST 形状、column 重复、类型不支持、count mismatch、未支持操作符、ParseError 携带位置、StatementList 多语句
- [ ] 7.2 实现 `parser.py::parse(tokens)` 主入口：循环解析语句，分号分隔，返回 `StatementList`
- [ ] 7.3 定义 AST 数据类（`StatementList`、`CreateTable`、`DropTable`、`Insert`、`Select`、`Delete`），所有节点带 `line`、`col`
- [ ] 7.4 实现 `parse_create_table`：识别 `CREATE TABLE name (col TYPE, ...)`；重复列名检测；不支持类型 raise ParseError，绿：跑通 CreateTable 用例
- [ ] 7.5 实现 `parse_drop_table`：识别 `DROP TABLE name`；缺失表名 raise ParseError，绿：跑通 DropTable 用例
- [ ] 7.6 实现 `parse_insert`：识别 `INSERT INTO name (cols) VALUES (row), (row)`；列数不匹配 raise ParseError，绿：跑通 Insert 用例
- [ ] 7.7 实现 `parse_select`：识别 `SELECT * | cols FROM name [WHERE col = lit]`；不支持操作符 raise ParseError；缺失 FROM raise ParseError，绿：跑通 Select 用例
- [ ] 7.8 实现 `parse_delete`：识别 `DELETE FROM name [WHERE col = lit]`；WHERE 可选，绿：跑通 Delete 用例
- [ ] 7.9 解析器纯函数性质（同输入两次结果一致），绿

## 8. Executor（spec 跨 storage-engine row CRUD + sql-minimal-parser parse-then-execute）

- [ ] 8.1 编写 `tests/integration/test_executor.py`，红：覆盖 DDL/DML 在真 storage 上的完整流程、PageFull 时新页分配、tombstone 过滤、严格类型守卫在 execute 层抛 TypeError
- [ ] 8.2 实现 `executor.py::Executor(pager, catalog)` 类，入口 `run(stmt) -> list[Row]`
- [ ] 8.3 实现 `Executor._exec_create_table` / `_exec_drop_table`，落 catalog + alloc/dealloc root page
- [ ] 8.4 实现 `Executor._exec_insert`：定位表 root page，扫描到有空槽的页（满则 alloc 新页），调用 slotted_page.insert + row_codec.encode_row

```

Full source: openspec/changes/tinydb-mvp/tasks.md

## openspec/changes/tinydb-mvp/specs/python-api/spec.md

- Source: openspec/changes/tinydb-mvp/specs/python-api/spec.md
- Lines: 1-107
- SHA256: b2d00c998fbc6c504d89bf01f37757ece4def7a94636ac5d06d43fa479034abb

[TRUNCATED]

```md
# Spec: python-api

> 范围：MVP 阶段的 `tinydb` 顶层包 + `Database` 类 + `Row` 数据类。后续 `tinydb-engine-v2` 可能扩展更多方法（事务上下文、`executemany` 等），但不应破坏 MVP 阶段的 API 形状。

## ADDED Requirements

### Requirement: Top-level package `tinydb` importable

The system SHALL expose a top-level Python package named `tinydb`, importable with `import tinydb`. The package MUST expose `Database` and `Row` as public names.

#### Scenario: Import Database and Row
- **WHEN** executing `import tinydb; tinydb.Database; tinydb.Row`
- **THEN** both names MUST be available without `__import__` workaround

#### Scenario: Package has `__version__`
- **WHEN** accessing `tinydb.__version__`
- **THEN** the value MUST be a string matching the format `"X.Y.Z"`; for MVP the value MUST be `"0.1.0"`

### Requirement: Database class supports file-backed and in-memory modes

`Database` SHALL accept a path argument that is either a filesystem path (file-backed) or the literal string `":memory:"` (in-memory).

#### Scenario: Open file-backed database
- **WHEN** constructing `Database('/tmp/foo.db')`
- **THEN** the system MUST create the file (if missing) or open it (if existing)
- **AND** persist data across `Database` instances across the same path

#### Scenario: Open in-memory database
- **WHEN** constructing `Database(':memory:')`
- **THEN** the system MUST NOT create any filesystem entry
- **AND** data MUST be lost when the `Database` object is garbage-collected

#### Scenario: Context manager closes the database
- **WHEN** using `Database(path)` as a context manager (`with` statement)
- **THEN** on `__exit__` the system MUST flush any pending writes and release file handles

### Requirement: execute method runs SQL statements

`Database.execute(sql)` SHALL parse the supplied SQL string, execute the resulting AST, and return a result value (defined per statement type).

#### Scenario: SELECT returns list of Row
- **WHEN** executing `SELECT * FROM users`
- **THEN** the return value MUST be a `list[Row]`

#### Scenario: DDL returns empty list
- **WHEN** executing `CREATE TABLE t(id INT)`
- **THEN** the return value MUST be `[]`

#### Scenario: DML returns empty list (MVP simplification)
- **WHEN** executing `INSERT INTO t VALUES (1)`
- **THEN** the return value MUST be `[]` (changed behavior in `tinydb-engine-v2` to return affected row count)

#### Scenario: Multiple statements separated by ;
- **WHEN** executing `CREATE TABLE t(id INT); INSERT INTO t VALUES (1); SELECT * FROM t`
- **THEN** the system MUST run all three statements in order
- **AND** return the result of the final SELECT

#### Scenario: ParseError propagates from execute
- **WHEN** executing malformed SQL `SELECT FROM`
- **THEN** the system SHALL raise `tinydb.ParseError` (a subclass of the parser's `ParseError` if applicable, or re-exported)

#### Scenario: ExecutionError on missing table
- **WHEN** executing `SELECT * FROM nonexistent`
- **THEN** the system SHALL raise `tinydb.ExecutionError` with message containing `"table nonexistent does not exist"`

### Requirement: Row class provides column access

`Row` SHALL provide attribute access and dict-style access by column name. Iteration SHALL yield column values in schema order.

#### Scenario: Access by attribute
- **WHEN** iterating over a SELECT result with row having columns `id` and `name`
- **THEN** `row.id` MUST return the `id` column value
- **AND** `row.name` MUST return the `name` column value

#### Scenario: Iteration yields values in schema order
- **WHEN** iterating `for value in row:`
- **THEN** values MUST yield in the order defined by the table's column list

#### Scenario: Repr is human-readable
- **WHEN** calling `repr(row)` for a row `(1, 'alice', TRUE)`

```

Full source: openspec/changes/tinydb-mvp/specs/python-api/spec.md

## openspec/changes/tinydb-mvp/specs/sql-minimal-parser/spec.md

- Source: openspec/changes/tinydb-mvp/specs/sql-minimal-parser/spec.md
- Lines: 1-136
- SHA256: d7cea9e0a06b1c7f0b1b67324ab0de5a3f331bbded00b7f08d0bf5db5a2febff

[TRUNCATED]

```md
# Spec: sql-minimal-parser

> 范围：MVP 阶段的 5 个语句（CREATE TABLE / DROP TABLE / INSERT / SELECT / DELETE），WHERE 暂只支持 `col = literal`。更复杂的解析（UPDATE、AND/OR、ORDER BY、LIMIT、子查询）属于 `tinydb-engine-v2`。

## ADDED Requirements

### Requirement: Tokenizer recognizes lexical categories

The tokenizer SHALL classify input characters into six token categories: identifier / keyword, integer, float, text literal, boolean literal, and punctuation. Invalid characters SHALL raise a tokenizer error with line and column.

#### Scenario: Tokenize identifier
- **WHEN** tokenizing `users`
- **THEN** the tokenizer MUST emit one IDENT token with value `"users"` and source position `(line=1, col=1)`

#### Scenario: Tokenize keyword case-insensitively
- **WHEN** tokenizing `CREATE` or `create` or `Create`
- **THEN** all three SHALL emit a KEYWORD token with the same canonical form `"CREATE"`

#### Scenario: Tokenize text literal with embedded space
- **WHEN** tokenizing `'hello world'`
- **THEN** the tokenizer MUST emit one TEXT token with value `"hello world"`

#### Scenario: Tokenize text literal with escaped quote
- **WHEN** tokenizing `'it''s ok'` (SQL-style doubled single quote)
- **THEN** the tokenizer MUST emit one TEXT token with value `"it's ok"`

#### Scenario: Tokenize punctuation
- **WHEN** tokenizing `( ) , ; = *`
- **THEN** the tokenizer MUST emit one PUNCT token for each in source order

#### Scenario: Tokenizer error reports position
- **WHEN** tokenizing `@` (invalid character)
- **THEN** the tokenizer SHALL raise `TokenError` with `line` and `col` attributes set to the position of `@`

### Requirement: Parser produces AST nodes

The parser SHALL consume a token stream and produce a typed AST node. Each supported statement type SHALL have a distinct AST node class. Errors SHALL raise `ParseError` with line, column, and message.

#### Scenario: CREATE TABLE produces CreateTable AST
- **WHEN** parsing `CREATE TABLE users (id INT, name TEXT)`
- **THEN** the parser MUST emit a `CreateTable(name="users", columns=[("id", "INT"), ("name", "TEXT")])` AST node
- **AND** line/column attributes MUST point to the `CREATE` keyword

#### Scenario: CREATE TABLE rejects duplicate column names
- **WHEN** parsing `CREATE TABLE t(id INT, id TEXT)`
- **THEN** the parser SHALL raise `ParseError` with message containing `"duplicate column"` and column position

#### Scenario: CREATE TABLE rejects unsupported type
- **WHEN** parsing `CREATE TABLE t(id VARCHAR(10))`
- **THEN** the parser SHALL raise `ParseError` mentioning `"VARCHAR not supported in MVP"`
- **AND** the position attribute MUST point to `VARCHAR`

### Requirement: DROP TABLE parsing

The parser SHALL recognize the `DROP TABLE` statement and emit a `DropTable` AST node.

#### Scenario: Parse DROP TABLE
- **WHEN** parsing `DROP TABLE users`
- **THEN** the parser MUST emit a `DropTable(name="users")` AST node

#### Scenario: DROP TABLE missing table name
- **WHEN** parsing `DROP TABLE`
- **THEN** the parser SHALL raise `ParseError` with message containing `"expected table name"`

### Requirement: INSERT parsing with explicit column list

The parser SHALL recognize the `INSERT INTO table(col, ...) VALUES (val, ...)` form and emit an `Insert` AST node.

#### Scenario: Parse single-row INSERT
- **WHEN** parsing `INSERT INTO users(id, name) VALUES (1, 'alice')`
- **THEN** the parser MUST emit `Insert(table="users", columns=["id","name"], values=[[1, "alice"]])`

#### Scenario: Parse multi-row INSERT
- **WHEN** parsing `INSERT INTO users(id, name) VALUES (1, 'alice'), (2, 'bob')`
- **THEN** the parser MUST emit `Insert(table="users", columns=["id","name"], values=[[1,"alice"],[2,"bob"]])`

#### Scenario: INSERT column count mismatch rejected
- **WHEN** parsing `INSERT INTO users(id, name) VALUES (1)`
- **THEN** the parser SHALL raise `ParseError` mentioning `"value count mismatch"`


```

Full source: openspec/changes/tinydb-mvp/specs/sql-minimal-parser/spec.md

## openspec/changes/tinydb-mvp/specs/storage-engine/spec.md

- Source: openspec/changes/tinydb-mvp/specs/storage-engine/spec.md
- Lines: 1-158
- SHA256: a2351add74a094f746b5bc1973193a2ea9abe3ab9173ff0f35ce94d03da78207

[TRUNCATED]

```md
# Spec: storage-engine

> 范围：MVP 阶段的单文件 slotted-page 存储引擎。WAL / 崩溃恢复属于 `tinydb-acid`；B-tree 索引属于 `tinydb-engine-v2`；并发安全永久 out。

## ADDED Requirements

### Requirement: Single-file .db format with magic header

The system SHALL persist tables into a single `.db` file identified by a fixed magic header on page 0. Opening an existing file MUST verify the magic header before any data access.

#### Scenario: Create new .db file writes magic header
- **WHEN** opening a non-existent file path with `Database(path)`
- **THEN** the system MUST create the file and write the magic bytes `b'TINYDB\\x00\\x01'` into page 0
- **AND** must also write the schema_version byte (`0x01` for MVP) into page 0 header

#### Scenario: Open existing .db verifies magic
- **WHEN** opening a file whose page 0 does not start with the magic bytes
- **THEN** the system SHALL raise `InvalidDatabaseFile` with a message indicating the file is not a tinydb file

#### Scenario: Reject wrong schema version
- **WHEN** opening a file with valid magic but unknown schema_version
- **THEN** the system SHALL raise `UnsupportedSchemaVersion` with the version number in the message

#### Scenario: Support `:memory:` mode
- **WHEN** opening `Database(':memory:')`
- **THEN** the system MUST NOT touch the filesystem
- **AND** must use an in-memory byte buffer as backing storage

### Requirement: Fixed 4KB page addressing

The system SHALL use a fixed page size of 4096 bytes. Page addressing SHALL be by integer id, with page 0 always being the file header page.

#### Scenario: Allocate a new page returns monotonic id
- **WHEN** calling `alloc_page()`
- **THEN** the returned page id SHALL be greater than any previously allocated id

#### Scenario: Read page by id returns exact 4096 bytes
- **WHEN** calling `read_page(page_id)`
- **THEN** the returned bytes MUST be exactly 4096 bytes long

#### Scenario: Write page updates on-disk content
- **WHEN** calling `write_page(page_id, data)` followed by `read_page(page_id)` after a flush
- **THEN** the read MUST return the written data

### Requirement: Slotted page layout

The system SHALL organize each table data page as a slotted page: a fixed-size page header, a slot directory grown from the start, a free space region in the middle, and the data area grown from the end.

#### Scenario: Insert row into empty page succeeds
- **WHEN** inserting the first row into an empty data page
- **THEN** the page MUST record one slot entry with the row's offset and length
- **AND** the free-space offset MUST move forward by the slot directory size

#### Scenario: Insert into full page raises PageFull
- **WHEN** attempting to insert a row whose encoded size exceeds the available free space
- **THEN** the slotted page MUST raise `PageFull`

#### Scenario: Update row in-place when slot space suffices
- **WHEN** updating an existing row with a new value of the same or smaller encoded length
- **THEN** the slot's length SHALL be updated in place without moving the row

#### Scenario: Mark row deleted via tombstone
- **WHEN** deleting a row
- **THEN** the slot SHALL be marked as tombstoned (offset == 0xFFFF)
- **AND** the underlying data bytes MAY remain in place

#### Scenario: Reuse tombstoned slot on next insert
- **WHEN** inserting a row into a page that has a tombstoned slot
- **THEN** the slotted page SHALL reuse the tombstoned slot if the new row fits the freed length

### Requirement: Row encoding with null bitmap

The system SHALL encode each row as a null bitmap followed by length-prefixed column values. The null bitmap SHALL have one bit per column, MSB-first.

#### Scenario: Encode row with all non-null columns
- **WHEN** encoding `(42, 'alice', TRUE)` for schema `(INT, TEXT, BOOL)`
- **THEN** the bytes MUST start with `b'\\x00'` (no NULLs)
- **AND** followed by INT encoding + length-prefixed text + BOOL encoding

#### Scenario: Encode row with null in second column

```

Full source: openspec/changes/tinydb-mvp/specs/storage-engine/spec.md

## openspec/changes/tinydb-mvp/specs/type-system-basic/spec.md

- Source: openspec/changes/tinydb-mvp/specs/type-system-basic/spec.md
- Lines: 1-135
- SHA256: 2dfe5f6f3024c15e65be3214a141482bcb5cf3cfe8f3cf00aaba7ef357293d20

[TRUNCATED]

```md
# Spec: type-system-basic

> 范围：MVP 阶段的 4 个基础类型 INT / TEXT / FLOAT / BOOL。扩展类型（VARCHAR / CHAR / DECIMAL / DATE / TIME / TIMESTAMP / SMALLINT / BIGINT）属于 `tinydb-engine-v2`，本 spec 不覆盖。

## ADDED Requirements

### Requirement: Type literals parseable from SQL text

The system SHALL parse the four base type literals from SQL text strings without ambiguity: signed integers, decimal floats, single-quoted text, and boolean keywords.

#### Scenario: Parse positive integer literal
- **WHEN** parsing the SQL text `42`
- **THEN** the tokenizer SHALL produce one INTEGER token with value `42`

#### Scenario: Parse negative integer literal
- **WHEN** parsing the SQL text `-7`
- **THEN** the tokenizer SHALL produce one INTEGER token with value `-7`

#### Scenario: Parse decimal float literal
- **WHEN** parsing the SQL text `3.14`
- **THEN** the tokenizer SHALL produce one FLOAT token with value `3.14`

#### Scenario: Parse text literal
- **WHEN** parsing the SQL text `'hello world'`
- **THEN** the tokenizer SHALL produce one TEXT token with value `hello world`

#### Scenario: Parse boolean literal TRUE
- **WHEN** parsing the SQL text `TRUE` (case-insensitive)
- **THEN** the tokenizer SHALL produce one BOOL token with value `true`

#### Scenario: Parse boolean literal FALSE
- **WHEN** parsing the SQL text `false`
- **THEN** the tokenizer SHALL produce one BOOL token with value `false`

#### Scenario: Reject NaN in float literal
- **WHEN** parsing the SQL text `NaN`
- **THEN** the tokenizer SHALL raise `ValueError` with message containing `"NaN not allowed"`

#### Scenario: Reject Infinity in float literal
- **WHEN** parsing the SQL text `Infinity` or `inf`
- **THEN** the tokenizer SHALL raise `ValueError`

### Requirement: Type encoding to binary buffer

The system SHALL encode typed values into a stable binary format suitable for slotted-page storage. Each type SHALL have a deterministic byte-level encoding.

#### Scenario: INT encodes as 8-byte signed big-endian
- **WHEN** encoding the integer `42`
- **THEN** the bytes MUST equal `b'\x00\x00\x00\x00\x00\x00\x00\x2a'`

#### Scenario: INT encoding rejects out-of-range value
- **WHEN** encoding the integer `2**63`
- **THEN** the encoder SHALL raise `OverflowError`

#### Scenario: TEXT encodes length-prefixed UTF-8
- **WHEN** encoding the text `alice`
- **THEN** the bytes MUST equal `b'\x00\x05alice'`

#### Scenario: TEXT encoding rejects non-UTF-8
- **WHEN** attempting to encode a Python string with invalid surrogate
- **THEN** the encoder SHALL raise `UnicodeEncodeError`

#### Scenario: BOOL encodes as single byte
- **WHEN** encoding the boolean `True`
- **THEN** the bytes MUST equal `b'\x01'`
- **WHEN** encoding the boolean `False`
- **THEN** the bytes MUST equal `b'\x00'`

#### Scenario: FLOAT encodes as 8-byte big-endian IEEE 754
- **WHEN** encoding the float `3.14`
- **THEN** the bytes MUST equal `struct.pack('>d', 3.14)`

### Requirement: Type decoding from binary buffer

The system SHALL decode binary buffers back to typed Python values, round-tripping with encoding for all valid inputs.

#### Scenario: Decode INT roundtrips
- **WHEN** decoding `b'\x00\x00\x00\x00\x00\x00\x00\x2a'` as INT
- **THEN** the value MUST equal `42`


```

Full source: openspec/changes/tinydb-mvp/specs/type-system-basic/spec.md
