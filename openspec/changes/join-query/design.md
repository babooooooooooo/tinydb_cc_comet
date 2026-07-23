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
