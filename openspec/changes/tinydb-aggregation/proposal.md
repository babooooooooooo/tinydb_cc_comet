# Proposal: tinydb-aggregation

> **范围声明**：本 change 在 `tinydb-mvp` 之上引入 SQL 聚合函数（`COUNT` / `SUM` / `AVG` / `MIN` / `MAX`）与 `GROUP BY` / `HAVING` 子句。**不改存储层**，仅在 SELECT 路径上叠加聚合层；不引入事务；不引入窗口函数；不引入 JOIN。

## Why

教学型嵌入式数据库若不支持 `COUNT(*)` 与 `GROUP BY` —— 即无法回答"按部门统计人数"这类最简单的 BI 需求。聚合属于 SELECT 路径的语义扩展，与 parser/executor 的 read path 同层。ORDER BY/LIMIT/OFFSET（在 `tinydb-engine-v1` 中引入）与 GROUP BY/HAVING 组合时是真实 BI 工作量：本 change 与 engine-v1 合并后可表达完整只读分析层。

## What Changes

- **新增** SELECT 子句：`GROUP BY <col>[, ...]`、`HAVING <expr>`
- **新增** 聚合表达式：`COUNT(*)` / `COUNT(<expr>)` / `SUM(<expr>)` / `AVG(<expr>)` / `MIN(<expr>)` / `MAX(<expr>)`
- **新增** parser AST 节点：`GroupBy(cols)`、`Having(expr)`、`AggregateCall(func, arg, alias?)`
- **新增** executor：
  - SELECT 含 aggregate 或 GROUP BY 时进入聚合路径：scan → filter WHERE → group → aggregate → filter HAVING → order/limit/offset → project
  - 聚合路径整组扫描一次性完成，避免回表
  - 结果 row 不再维持列名细粒度结构，转为 `{alias: value}`（聚合输出列 shape 与 base table 不同）
- **新增** 错误：聚合表达式出现在 WHERE 而非 HAVING 时抛 `ExecutionError`
- **修改** SELECT AST 增加 `group_by: tuple[str, ...]`、`having: Expr | None` 字段

## Capabilities

### New Capabilities

- `sql-aggregate-functions`：`COUNT` / `SUM` / `AVG` / `MIN` / `MAX`，支持 `*` 与表达式参数
- `sql-group-by-having`：SELECT 增加 `GROUP BY` 与 `HAVING` 子句，HAVING 引用聚合别名

### Modified Capabilities

- `sql-minimal-parser`：SELECT 节点扩展字段
- `sql-select-order-limit`（来自 engine-v1）：与聚合路径兼容（聚合结果再排序/切片）
- `python-api`：`Database.execute` 返回 `list[Row]`（聚合输出行为 `Row`，列访问保持一致）

## Impact

- 受影响文件：`src/tinydb/parser.py`（+~80 行）、`src/tinydb/executor.py`（+~200 行）
- 模块行数：
  - `parser.py` ≤ 830 行
  - `executor.py` ≤ 820 行
- 测试：单元 ~35（聚合真值表、GROUP 边界、HAVING 表达式）、集成 ~12、e2e golden ~10
- 不引入依赖；不破坏外部 API

## Out of Scope

- JOIN（永久 out）
- 子查询（永久 out）
- 窗口函数、ROLLUP、CUBE（永久 out）
- DISTINCT（计划作为聚合路径上的小增量后续处理）
- 索引化的聚合（→ `tinydb-engine-v2`）
