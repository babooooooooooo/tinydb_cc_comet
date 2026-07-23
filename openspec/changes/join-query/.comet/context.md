# Comet Design Handoff

- Change: join-query
- Phase: design
- Mode: compact
- Context hash: f46067b9987374e0d93babf39cb7fba7f26d4d7547346d73bf23e06e8c2fa812

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/join-query/proposal.md

- Source: openspec/changes/join-query/proposal.md
- Lines: 1-33
- SHA256: c7ccf9cc01048186507019416ec71a9d2f756fa0ec1065409e1a79b66ab990d3

```md
## Why

TinyDB v0.1 仅支持单表查询，无法表达真实关系数据中常见的跨表关联。v0.2 需要在保留现有 tokenizer、parser、executor、类型系统、索引和事务实现的前提下，以增量方式加入可组合、可解释且可测试的多表 JOIN 能力，并为 CLI 的 `.explain` 提供稳定的逻辑计划接口。

## What Changes

- 扩展 SQL tokenizer 与 parser，支持两表及多表 `INNER JOIN`、`LEFT JOIN`、`RIGHT JOIN`、`FULL JOIN`、`CROSS JOIN`，以及 `ON`、`USING`、`NATURAL` 连接形式和表别名。
- 增加限定列引用（如 `users.id`、`u.id`）及确定性的列解析规则；未知表、未知列、重复别名和歧义列必须返回带位置的明确错误。
- 支持 `ON` 中的比较表达式以及 `AND` / `OR` / `NOT` 组合条件。
- 在现有单表执行路径之外增加 JOIN 逻辑计划和执行路径，同时保持现有单表查询行为兼容。
- JOIN 结果可继续进入 `WHERE`、投影、`ORDER BY`、`GROUP BY`、`HAVING` 和聚合处理。
- 外连接对未匹配侧按 SQL 语义补齐 `NULL`；`USING` 合并连接键的输出规则，`NATURAL` 使用双方同名列作为连接键。
- 暴露稳定、只读的逻辑计划表示，供后续 `cli-enhancement` change 的 `.explain` 使用；本 change 不实现 CLI 命令。
- 增加 parser、列解析、执行器、属性测试和端到端 JOIN 覆盖，并保持 v0.1 回归测试通过。

## Capabilities

### New Capabilities

- `sql-join-query`: 定义多表 `INNER` / `LEFT` / `RIGHT` / `FULL` / `CROSS JOIN` 的语法、`ON`/`USING`/`NATURAL` 名称解析、逻辑计划、执行语义、错误行为及与过滤、排序、分组和聚合的组合规则。

### Modified Capabilities

- `sql-minimal-parser`: 将现有单表 `SELECT ... FROM table` 语法扩展为带表引用、别名和多种 JOIN 子句的查询语法，同时保持既有单表 AST 行为兼容。
- `python-api`: 明确 `Database.execute()` 对 JOIN 结果的 `Row` 列名、限定名、USING/NATURAL 合并列和重复列名行为，以及 JOIN 查询错误的传播契约。

## Impact

- 主要影响 `src/tinydb/tokenizer.py`、`src/tinydb/parser.py`、`src/tinydb/executor.py`、`src/tinydb/database.py` 与新增的 JOIN/逻辑计划模块。
- 复用现有 Catalog、Pager、B+Tree、类型 codec、WAL 和事务路径，不重写存储引擎，不修改 v0.1 文件格式。
- 新增 JOIN parser 单元测试、执行器集成测试、组合语义测试、属性测试和 SQL E2E golden cases，覆盖所有选定 JOIN 类型及连接键形式。
- 与 `cli-enhancement` 存在显式接口依赖：后者消费本 change 的逻辑计划表示；本 change 不依赖 CLI 实现。
- 非目标包括 CTE、子查询、视图、`UNION`、分布式连接和成本优化器。

```

## openspec/changes/join-query/design.md

- Source: openspec/changes/join-query/design.md
- Lines: 1-75
- SHA256: 3b960dd74c3179dd34b955b8d049c028c554d765a38eef5d5871fcf2279c3741

```md
## Context

TinyDB v0.1 的 `Select` AST、执行器和聚合管线以单表裸列名为中心。现有 `tokenizer.py`、`parser.py`、`executor.py` 和 `database.py` 已经共同承担 SQL 解析、列校验、索引快速路径、聚合和 `Row` 包装；WAL、Pager、Catalog 和 B+Tree 是稳定的 v0.1 基线。本 change 必须从该基线增量演进，不改变现有单表查询和磁盘格式。

v0.2 的 JOIN 还需要为独立的 `cli-enhancement` change 提供可读、只构造不执行的逻辑计划。因此，JOIN 不能只作为 executor 内部的临时笛卡尔积实现，而应在 AST 与执行之间增加纯数据的逻辑计划层。

## Goals / Non-Goals

**Goals:**

- 支持两表及多表 `INNER JOIN`、`LEFT JOIN`、`RIGHT JOIN`、`FULL JOIN` 和 `CROSS JOIN`。
- 支持 `ON` 复杂谓词、`USING (column, ...)` 和 `NATURAL [INNER|LEFT|RIGHT|FULL] JOIN`，并定义连接键和输出合并规则。
- 支持表名、表别名和限定列引用，集中完成未知表、未知列、歧义列和重复别名诊断。
- 让 JOIN 结果继续经过现有的 WHERE、投影、ORDER BY、LIMIT/OFFSET、GROUP BY、HAVING 和聚合语义。
- 引入可冻结、可打印的 `LogicalPlan` 节点树；计划构造不触发 I/O 写入或事务提交，供 `.explain` 复用。
- 保留 v0.1 单表执行的索引快速路径和行为兼容性；JOIN 路径通过适配层使用现有读页与事务路由。
- 使用 TDD、单元/集成/property/E2E 测试验证 parser、名称解析、计划构造、各 JOIN 语义和回归行为。

**Non-Goals:**

- 不实现 CTE、子查询、视图、`UNION`、分布式连接或成本优化器。
- 不引入统计信息、hash/merge join 或完整物理优化器；首版连接算法为正确性优先的 nested-loop 变体。
- 不重写 Pager、Catalog、WAL、B+Tree 或 v0.1 磁盘格式。
- 不在本 change 实现 CLI `.explain` 命令；只提供 CLI 可消费的逻辑计划 API。

## Decisions

### 1. 保留 AST，新增 LogicalPlan 中间层

parser 继续产生语法 AST，并把 `Select` 扩展为 `TableRef`、`JoinClause`、连接类型/连接键信息和 `ColumnRef`。新增不可变计划节点（至少包含 `Scan`、`Join`、`Filter`、`Aggregate`、`Sort`、`Project`、`Limit`），由 planner 在执行前一次性构造。这样 `.explain` 可以打印同一棵计划，而不会复制解析或执行逻辑。

备选方案是直接在 `_exec_select` 中拼接宽行，改动较小但计划不可复用、名称解析容易散落；另一方案是一次性建设成本优化器和多种物理连接算法，超出 v0.2 的可验证范围，因此不采用。

### 2. 统一使用可选限定符的 ColumnRef 和 JoinKey

`ColumnRef` 表示 `name` 与可选 `qualifier`。裸列在单表查询中保持既有行为；多表查询中的裸列只有在所有输入表中唯一时才允许，多个表都存在时返回 `AmbiguousColumn`。表别名优先于原表名解析，重复别名和不存在的表在计划构造阶段失败。

`USING` 将列名解析到左右输入各自的同名键，并在输出 schema 中只保留一个合并键；`NATURAL` 在计划构造阶段按双方 schema 的共同列名生成等值 JoinKey，没有共同列时按 SQL 语义形成不匹配结果。ON、WHERE、投影、ORDER BY、GROUP BY、HAVING 和聚合参数全部复用同一 resolver，避免每个阶段出现不同的歧义规则。

### 3. 连接执行采用 nested-loop 变体，JOIN 路径与单表 fast path 分离

连接计划按 SQL 书写顺序左深构造。INNER 只输出匹配组合；LEFT/RIGHT 对无匹配侧补 `NULL`；FULL 同时保留左右两侧无匹配行；CROSS 输出笛卡尔积且不需要 ON/USING。RIGHT JOIN 可在执行层通过交换输入并恢复输出 schema 实现，但 LogicalPlan 必须保留用户声明的 JOIN 类型。外连接链必须按左关联顺序执行，不能把 outer semantics 静默改写为 inner。

单表且无 JOIN 的 Select 继续走 v0.1 既有 indexed/scan/aggregation 路径，减少回归面。JOIN 首版不强制新增索引 join；如实现者发现可安全复用现有唯一索引，必须保持逻辑结果与 nested-loop 一致并单独测试，不能改变计划契约。

### 4. 逻辑计划与执行器解耦

计划节点是纯数据对象，包含解析后的来源、连接类型、JoinKey/表达式和子节点，不持有 Pager、文件句柄或 Executor。执行器负责把计划节点交给 JOIN/过滤/聚合 helper；CLI 只构造并格式化计划，不执行 DML 或触发写事务。计划打印格式使用稳定的缩进树和节点名称，但不承诺成本估算或固定行数统计。

### 5. JOIN Row 输出采用无歧义列标签

显式投影按选择项产生列标签；`SELECT *` 在 JOIN 查询中按来源顺序展开。普通 JOIN 使用 `source.column` 标签；`USING` / `NATURAL` 的合并键只输出一个稳定标签，其余列仍使用来源限定标签。重复的显式输出标签也必须通过限定标签避免 `Row` 映射覆盖。单表查询继续保留 v0.1 裸列标签。Python API 为包含点号的标签提供映射式访问，属性访问只对合法的裸列名保持兼容。

## Risks / Trade-offs

- **[executor.py 继续膨胀]** → 将 resolver、plan、JOIN 执行和计划打印拆到小模块；`executor.py` 只保留 dispatch 与事务边界。
- **[外连接和 USING/NATURAL 的 NULL/合并键规则复杂]** → 先在 resolver 生成显式 JoinKey 和输出 schema，再由各连接 helper 消费；为每种连接类型和键形式建立矩阵测试。
- **[RIGHT/FULL 的未匹配行与输出顺序]** → 明确左深计划和稳定输出顺序，交换输入只作为内部优化，外部列标签和行序按 spec 测试锁定。
- **[聚合管线只接受裸列名]** → 先在 planner 中把所有引用解析为统一的列位置/标签，再由聚合 helper 消费合并 schema；为单表调用保留兼容适配。
- **[外连接 NULL 与现有三值比较不一致]** → 复用现有 NULL 比较规则，加入左右/full 无匹配、WHERE 过滤和 HAVING 的集成测试。
- **[单表回归]** → 明确保留单表早返回路径；每次 parser/executor 改动运行既有 parser、aggregation、index 和 full SQL 测试。
- **[输出列标签破坏 API 使用者]** → 只对 JOIN 结果启用限定标签；单表 `Row` 标签不变，并在 python-api delta spec 中记录映射访问、合并键和重复列行为。
- **[计划接口过早锁定]** → 计划节点只暴露 v0.2 所需的逻辑信息，不暴露物理实现细节，为后续优化器保留扩展空间。

## Migration Plan

1. 从当前 `main` 创建 `join-query` worktree/feature branch，先提交 tokenizer/parser/resolver/plan 的测试和实现，再提交连接执行及组合语义。
2. 所有新增代码只读现有 v0.1 数据格式；已有数据库文件无需迁移。
3. 通过单元、集成、property、E2E、覆盖率和 OpenSpec strict validation 后，将 delta spec 同步到 `openspec/specs/`，再合并到 v0.2 integration 分支。
4. 若 JOIN 实现回滚，删除 JOIN 入口和新增模块即可；保留已有 AST/Row 兼容字段，避免对 v0.1 文件和单表 API 产生迁移负担。

## Open Questions

- 逻辑计划的稳定文本格式由后续 `cli-enhancement` design 细化；本 change 只保证节点类型、子节点顺序、Join kind 和关键 JoinKey 字段可读取。
- 是否在实现中为等值 JOIN 增加索引 nested-loop 是可选优化，不影响本 change 的最低验收；如采用，需在 tasks 和测试中明确记录。

```

## openspec/changes/join-query/tasks.md

- Source: openspec/changes/join-query/tasks.md
- Lines: 1-56
- SHA256: 8ec56e082fbcbe1d57b52e20ee7c399184c8cfd6abb3f3b88dd9022ba080f5ce

```md
## 1. Tokenizer 与 AST 基础

- [ ] 1.1 先编写 tokenizer 回归测试：JOIN/INNER/LEFT/RIGHT/FULL/OUTER/CROSS/ON/USING/NATURAL 关键字大小写、限定名中的 `.`、非法连续限定符和错误位置。
- [ ] 1.2 扩展 tokenizer 的关键字和标点分类，保持 v0.1 字面量、注释和既有标点行为不变。
- [ ] 1.3 先编写 parser 测试：表别名、INNER/LEFT/RIGHT/FULL/CROSS JOIN、多级 JOIN、ON/USING/NATURAL、限定 SELECT/WHERE/ORDER BY/GROUP BY 列、复杂 ON 表达式和缺少键子句的错误。
- [ ] 1.4 新增不可变的 `TableRef`、`JoinClause`、`JoinKey`、`ColumnRef` AST 表示，并以兼容方式扩展 `Select`。
- [ ] 1.5 实现 FROM/JOIN/ON/USING/NATURAL 和可选限定列解析；完成 parser 单元测试及现有 parser/aggregation parser 回归。

## 2. 名称解析与合并 schema

- [ ] 2.1 先编写 resolver 测试：表/别名映射、重复别名、未知表、限定列、唯一裸列、歧义裸列、USING 缺失/类型不兼容和 NATURAL 共同列发现。
- [ ] 2.2 新建独立 resolver 模块，按 FROM/JOIN 左到右构造来源映射和合并 schema。
- [ ] 2.3 统一解析 SELECT、ON、WHERE、ORDER BY、GROUP BY、HAVING 和聚合参数，输出稳定的列位置/标签。
- [ ] 2.4 实现 USING 等值 JoinKey、NATURAL 同名 JoinKey 和合并键输出标签；定义 JOIN 错误契约并在错误层级中提供可诊断错误。

## 3. LogicalPlan 中间层

- [ ] 3.1 先编写 plan 构造测试：单表计划、左深多表计划、各种 Join kind、ON/USING/NATURAL key 字段、节点字段、子节点顺序和无副作用构造。
- [ ] 3.2 新建不可变 plan 模块，定义 Scan、Join、Filter、Aggregate、Sort、Project、Limit 等逻辑节点。
- [ ] 3.3 实现从 Select AST 到 LogicalPlan 的构造，集中完成名称解析、隐式 NATURAL key 生成和阶段顺序编排。
- [ ] 3.4 为单表 Select 保留 v0.1 indexed/scan/aggregation 路径或等价适配，并添加单表计划回归护栏。

## 4. INNER/CROSS JOIN 执行

- [ ] 4.1 先编写集成测试：两表等值连接、多级连接、复杂 ON、USING、NATURAL、空表和 CROSS 笛卡尔积，以及列顺序/标签。
- [ ] 4.2 新建 JOIN 执行 helper，实现事务读路由下的 nested-loop INNER/CROSS JOIN。
- [ ] 4.3 在 Executor SELECT dispatch 中仅为包含 JOIN 的查询委派 plan/JOIN helper，避免继续膨胀 `executor.py`。
- [ ] 4.4 验证 JOIN 路径不绕过 `_txn_read_page`、WAL 缓冲或 `_IndexPager`，并完成 ACID 读事务回归。

## 5. LEFT/RIGHT/FULL JOIN 与 NULL 语义

- [ ] 5.1 先编写 LEFT/RIGHT/FULL 测试：匹配、单侧无匹配、双方无匹配、右表多匹配、NULL、USING/NATURAL 和多级外连接。
- [ ] 5.2 实现无匹配侧的 NULL 补齐、RIGHT 输入交换与结果恢复、FULL 双侧未匹配行保留，并保持声明的 Join kind。
- [ ] 5.3 验证外连接后 WHERE、HAVING 和聚合对 NULL 的行为与规范一致，锁定稳定输出顺序。

## 6. JOIN 后查询阶段

- [ ] 6.1 先编写 JOIN + WHERE/投影/SELECT * 测试，覆盖限定列、USING/NATURAL 合并键、未知列、歧义列和输出顺序。
- [ ] 6.2 让过滤、投影和 wildcard 展开消费合并 schema 与 resolver，保留单表裸列标签。
- [ ] 6.3 先编写 JOIN + GROUP BY/HAVING/COUNT/SUM 等聚合测试，并将聚合 helper 适配为合并 schema。
- [ ] 6.4 先编写 JOIN + ORDER BY/LIMIT/OFFSET 测试，确保排序发生在限制之前并支持限定排序键。
- [ ] 6.5 回归现有 aggregation、group/having、sort、limit/offset 测试。

## 7. Python API 与计划消费接口

- [ ] 7.1 先编写 Database/Row JOIN API 测试：显式投影、各 JOIN 类型、`SELECT *`、USING/NATURAL 合并键、重复列名、限定标签映射访问、迭代和 repr。
- [ ] 7.2 让 JOIN Row 使用无歧义的限定列标签和单一合并键，并保持单表 Row 行为兼容。
- [ ] 7.3 暴露只读 LogicalPlan 构造入口，确保构造和格式化不写文件、不写 WAL、不提交事务。

## 8. 全面验证与文档

- [ ] 8.1 增加 JOIN parser、resolver、plan、执行、组合语义的 unit/integration/property/E2E 测试和 golden SQL，覆盖 ON/USING/NATURAL。
- [ ] 8.2 运行完整 pytest、覆盖率、pyflakes 和 OpenSpec strict validation；覆盖率不得低于 v0.1 基线。
- [ ] 8.3 审计新增模块行数和 `executor.py` 变化，确保 JOIN 逻辑保持模块化并记录偏差。
- [ ] 8.4 更新 `docs/MVP_LIMITATIONS.md`、README 或操作手册中的单表限制和 v0.2 JOIN 能力说明。
- [ ] 8.5 生成 JOIN change 验证报告，记录基线、测试结果、覆盖率、已知限制和与 `cli-enhancement` 的计划接口依赖。

```

## openspec/changes/join-query/specs/python-api/spec.md

- Source: openspec/changes/join-query/specs/python-api/spec.md
- Lines: 1-96
- SHA256: e0d87e97846670aacf536d75d38185ec8eae6caa9b59abc8766bcb628560dfa6

[TRUNCATED]

```md
## MODIFIED Requirements

### Requirement: execute method runs SQL statements

`Database.execute(sql)` SHALL parse the supplied SQL string, execute the resulting AST, and return a result value defined per statement type. SELECT statements MAY read from multiple tables using the JOIN capability. Existing DDL, DML, transaction, and multi-statement behavior SHALL remain compatible.

#### Scenario: SELECT returns list of Row
- **WHEN** executing `SELECT * FROM users`
- **THEN** the return value MUST be a `list[Row]`

#### Scenario: SELECT returns joined rows
- **WHEN** executing `SELECT u.id, o.id FROM users u JOIN orders o ON u.id = o.user_id`
- **THEN** the return value MUST be a `list[Row]`
- **AND** each row MUST contain both projected values without column-name collision

#### Scenario: DDL returns empty list
- **WHEN** executing `CREATE TABLE t(id INT)`
- **THEN** the return value MUST be `[]`

#### Scenario: DML returns empty list
- **WHEN** executing `INSERT INTO t VALUES (1)`
- **THEN** the return value MUST be `[]`

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

#### Scenario: JOIN name errors are explicit
- **WHEN** executing a JOIN with an unknown table, unknown qualified column, incompatible USING/NATURAL key, or ambiguous unqualified column
- **THEN** the system SHALL raise a documented TinyDB error identifying the source of the resolution failure

### Requirement: Row class provides column access

`Row` SHALL provide attribute access and mapping-style access by column name. Iteration SHALL yield column values in result-column order. For JOIN results, qualified labels such as `u.id` MUST be available through mapping-style access, USING/NATURAL merged keys MUST be available by their merged label, and no source's same-named column may be silently overwritten.

#### Scenario: Access by attribute for a single-table row
- **WHEN** iterating over a SELECT result with row having columns `id` and `name`
- **THEN** `row.id` MUST return the `id` column value
- **AND** `row.name` MUST return the `name` column value

#### Scenario: Iteration yields values in result order
- **WHEN** iterating `for value in row:`
- **THEN** values MUST yield in the order defined by the SELECT result columns

#### Scenario: Access qualified JOIN columns by mapping
- **WHEN** a JOIN result has columns `u.id` and `o.id`
- **THEN** `row["u.id"]` and `row["o.id"]` MUST return their respective values
- **AND** attribute access MUST remain available for labels that are valid Python attribute names

#### Scenario: Access a merged USING/NATURAL key
- **WHEN** a JOIN result is created with `USING (id)` or NATURAL matching `id`
- **THEN** the merged result column MUST be available under one stable `id`-compatible label
- **AND** the two source values MUST NOT appear as duplicate mapping keys

#### Scenario: Repr is human-readable
- **WHEN** calling `repr(row)` for a row `(1, 'alice', TRUE)`
- **THEN** the repr MUST contain `Row(id=1, name='alice', bool_col=True)` style output

#### Scenario: Equality compares by values
- **WHEN** comparing two `Row` instances with the same values
- **THEN** `row1 == row2` MUST be `True`
- **AND** comparing with different values MUST be `False`

### Requirement: ResolutionError is exposed and identifiable

`tinydb.ResolutionError` SHALL be importable from the top-level package and SHALL be a subclass of `tinydb.ExecutionError`. Specific name-resolution failures SHALL raise documented subtypes (e.g. `AmbiguousColumn`, `DuplicateAlias`, `UnknownSource`, `UnknownQualifiedColumn`, `MissingUsingKey`, `IncompatibleKeyTypes`).

#### Scenario: Ambiguous unqualified column raises ResolutionError
- **WHEN** executing `SELECT id FROM users u JOIN orders o`
- **THEN** the system SHALL raise `tinydb.AmbiguousColumn` (a `ResolutionError`) naming the column and the conflicting sources.


```

Full source: openspec/changes/join-query/specs/python-api/spec.md

## openspec/changes/join-query/specs/sql-join-query/spec.md

- Source: openspec/changes/join-query/specs/sql-join-query/spec.md
- Lines: 1-181
- SHA256: ba235ea3fd5dd0875f5b6e2192c9bff136e957acb5b2fe02a9408bb8fc111c69

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: Multi-table JOIN capability

The system SHALL parse and execute two-table and multi-table `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL JOIN`, and `CROSS JOIN` queries using the existing v0.1 database, catalog, type, and transaction infrastructure.

#### Scenario: Execute a two-table inner join
- **WHEN** executing `SELECT u.id, o.id FROM users AS u INNER JOIN orders AS o ON u.id = o.user_id`
- **THEN** the result MUST contain one row for each pair satisfying the ON expression
- **AND** the result MUST preserve the left-to-right source order

#### Scenario: Execute a chained multi-table join
- **WHEN** executing a query with `t1 JOIN t2 ON ... JOIN t3 ON ...`
- **THEN** the system MUST evaluate joins in written left-associative order
- **AND** each later ON expression MUST be able to reference any source already present in the left input and the newly joined right source

#### Scenario: Execute a left join with no match
- **WHEN** a left input row has no right input row satisfying `ON`
- **THEN** a `LEFT JOIN` MUST emit exactly one output row for that left input row
- **AND** every right-source column in that row MUST be `NULL`

#### Scenario: Execute right and full joins with unmatched rows
- **WHEN** a RIGHT or FULL JOIN has rows unmatched on one or both sides
- **THEN** RIGHT JOIN MUST preserve every right-side row and FULL JOIN MUST preserve every row from both sides
- **AND** columns from the missing side MUST be `NULL`

#### Scenario: Execute a cross join
- **WHEN** executing `SELECT * FROM users CROSS JOIN orders`
- **THEN** the result MUST contain the Cartesian product of both inputs
- **AND** the parser MUST NOT require an ON or USING clause for the CROSS JOIN

### Requirement: JOIN syntax and table references

The parser SHALL recognize `JOIN`, `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL JOIN`, `CROSS JOIN`, optional `OUTER`, optional `AS`, table aliases, `ON`, `USING`, and `NATURAL` in a SELECT FROM clause. Every non-CROSS ordinary JOIN SHALL contain exactly one ON or USING clause, while NATURAL JOIN derives keys without either clause.

#### Scenario: Parse a table alias
- **WHEN** parsing `SELECT u.id FROM users AS u`
- **THEN** the AST MUST retain the base table name `users` and alias `u`

#### Scenario: Parse all explicit join kinds
- **WHEN** parsing a FROM clause containing INNER, LEFT OUTER, RIGHT OUTER, FULL OUTER, and CROSS JOIN clauses
- **THEN** the AST MUST retain each join kind, right table reference, and source order

#### Scenario: Parse USING keys
- **WHEN** parsing `FROM users u JOIN orders o USING (id)`
- **THEN** the AST MUST retain the USING column list and MUST NOT require an ON expression

#### Scenario: Parse a natural join
- **WHEN** parsing `FROM users NATURAL LEFT JOIN profiles`
- **THEN** the AST MUST retain NATURAL and LEFT join mode without an explicit ON or USING clause

#### Scenario: Reject a JOIN without a key clause
- **WHEN** parsing `SELECT * FROM users JOIN orders`
- **THEN** the parser SHALL raise a positioned `ParseError` indicating that an ON or USING expression is required

### Requirement: Qualified column name resolution

The system SHALL resolve column references using an optional table name or alias qualifier. In a multi-source query, an unqualified column SHALL be accepted only when exactly one input source provides that column. USING and NATURAL key resolution SHALL use the same source metadata and SHALL reject missing or incompatible keys.

#### Scenario: Resolve an alias-qualified column
- **WHEN** a query references `u.id` and `users` has alias `u`
- **THEN** the resolver MUST bind the reference to the `users.id` source column

#### Scenario: Reject an ambiguous unqualified column
- **WHEN** both `users` and `orders` provide a column named `id` and the query references `id` without a qualifier
- **THEN** the system SHALL raise an `AmbiguousColumn`-compatible error naming the column

#### Scenario: Resolve USING keys
- **WHEN** both JOIN inputs provide a compatible column named `id` and the query uses `USING (id)`
- **THEN** the resolver MUST create an equality JoinKey for the two source columns
- **AND** the output schema MUST contain one merged `id` key label

#### Scenario: Resolve NATURAL keys
- **WHEN** a NATURAL JOIN has multiple compatible same-named columns
- **THEN** the resolver MUST create equality JoinKeys for every common name in deterministic schema order

#### Scenario: Reject an unknown qualified or join key column
- **WHEN** a query references `missing.id`, `u.missing`, or a USING column missing from either input
- **THEN** the system SHALL raise a clear positioned or execution-time error identifying the unknown source or column


```

Full source: openspec/changes/join-query/specs/sql-join-query/spec.md

## openspec/changes/join-query/specs/sql-minimal-parser/spec.md

- Source: openspec/changes/join-query/specs/sql-minimal-parser/spec.md
- Lines: 1-95
- SHA256: dec06c30422157848b673c47459147404b77c346148f7e73f43398b6e75cdd69

[TRUNCATED]

```md
## MODIFIED Requirements

### Requirement: Parser produces AST nodes

The parser SHALL consume a token stream and produce a typed AST node. Each supported statement type SHALL have a distinct AST node class. SELECT AST nodes SHALL retain table references, optional aliases, JOIN kinds, connection key forms, and qualified column references. Errors SHALL raise `ParseError` with line, column, and message.

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

#### Scenario: SELECT AST retains qualified JOIN structure
- **WHEN** parsing `SELECT u.id FROM users AS u LEFT JOIN orders o ON u.id = o.user_id`
- **THEN** the AST MUST contain a table reference for `users` with alias `u`
- **AND** MUST contain a LEFT JOIN clause whose right table is `orders` with alias `o`
- **AND** the selected and ON columns MUST retain their qualifiers

#### Scenario: SELECT AST retains USING and NATURAL structure
- **WHEN** parsing `SELECT * FROM users u NATURAL FULL JOIN profiles p` or `SELECT * FROM users u JOIN profiles p USING (id)`
- **THEN** the AST MUST retain the NATURAL or USING key form, join kind, and source order

### Requirement: SELECT parsing with WHERE col = literal

The parser SHALL recognize `SELECT` queries with a FROM table reference, optional alias, zero or more `INNER`, `LEFT`, `RIGHT`, `FULL`, or `CROSS` JOIN clauses, optional `ON`/`USING`/`NATURAL` key forms, optional qualified column references, and the existing WHERE/ORDER BY/LIMIT/OFFSET/GROUP BY/HAVING syntax. WHERE and JOIN expressions MUST use the existing expression grammar and MUST preserve single-table compatibility.

#### Scenario: Parse SELECT * from one table
- **WHEN** parsing `SELECT * FROM users`
- **THEN** the parser MUST emit a SELECT AST with one table reference, no joins, and a wildcard projection

#### Scenario: Parse SELECT with explicit columns
- **WHEN** parsing `SELECT id, name FROM users`
- **THEN** the parser MUST emit a SELECT AST with unqualified column references for `id` and `name`

#### Scenario: Parse SELECT with WHERE col = literal
- **WHEN** parsing `SELECT * FROM users WHERE id = 1`
- **THEN** the parser MUST emit a SELECT AST with an unqualified `id` column reference and the literal predicate

#### Scenario: Parse a qualified column
- **WHEN** parsing `SELECT u.id FROM users AS u`
- **THEN** the parser MUST emit a column reference with qualifier `u` and name `id`

#### Scenario: Parse explicit outer and cross joins
- **WHEN** parsing `SELECT * FROM users u LEFT OUTER JOIN orders o ON u.id = o.user_id RIGHT JOIN profiles p ON o.id = p.order_id FULL OUTER JOIN flags f ON p.id = f.profile_id CROSS JOIN audit a`
- **THEN** the parser MUST emit the join kinds and source order without requiring a key clause for CROSS JOIN

#### Scenario: Parse USING and NATURAL joins
- **WHEN** parsing `SELECT * FROM users u JOIN orders o USING (id)` or `SELECT * FROM users NATURAL LEFT JOIN profiles`
- **THEN** the parser MUST emit the corresponding USING column list or NATURAL marker

#### Scenario: Parse a composed ON expression
- **WHEN** parsing `SELECT * FROM users u LEFT JOIN orders o ON u.id = o.user_id AND (o.total > 10 OR o.priority = 1)`
- **THEN** the parser MUST retain the complete composed ON expression

#### Scenario: SELECT rejects missing FROM
- **WHEN** parsing `SELECT id`
- **THEN** the parser SHALL raise `ParseError` with message containing `"expected FROM"`

### Requirement: ParseError carries position and message

All parse-time errors SHALL raise `ParseError` with `line`, `col`, and human-readable `message` attributes, including malformed JOIN clauses and qualified names.

#### Scenario: Unexpected token reports position
- **WHEN** parsing `CREATE 123 (id INT)` (digit where identifier expected)
- **THEN** `ParseError.line` and `ParseError.col` MUST point to `123`
- **AND** message MUST contain `"expected table name"`

#### Scenario: Multiple statements separated by ; supported at top level
- **WHEN** parsing `CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1)`
- **THEN** the parser MUST emit a `StatementList` containing two AST nodes in source order

#### Scenario: JOIN requires an appropriate key clause

```

Full source: openspec/changes/join-query/specs/sql-minimal-parser/spec.md
