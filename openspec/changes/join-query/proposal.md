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
