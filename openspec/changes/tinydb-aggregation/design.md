# Design: tinydb-aggregation

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

聚合属于 SELECT 路径的语义扩展。MVP 已实现 scan → filter WHERE → project。聚合路径在 WHERE 后加入 group → aggregate → filter HAVING，最终再叠加 engine-v1 引入的 order/limit/offset。

## Goals / Non-Goals

**Goals：**
- 5 个标准聚合函数 + `COUNT(*)`
- GROUP BY 单列与多列
- HAVING 表达式可引用聚合结果（`SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept HAVING n > 5`）
- ORDER BY / LIMIT / OFFSET 在聚合结果上仍生效
- `SELECT dept, COUNT(*) FROM emp`（无 GROUP BY）走"全表作为单组"语义

**Non-Goals：**
- 不引入 DISTINCT（计划作为后续小增量）
- 不引入索引扫描聚合
- 不引入 JOIN 驱动聚合
- 不引入窗口函数 / ROLLUP / CUBE

## Architecture

### Parser

```
aggregate_call   = ( 'COUNT' '(' '*' ')' [ 'AS' IDENT ]
                   | ( 'COUNT' | 'SUM' | 'AVG' | 'MIN' | 'MAX' ) '(' expr ')' [ 'AS' IDENT ] )

select_statement = 'SELECT' select_items 'FROM' IDENT [ where ] [ 'GROUP' 'BY' col_list ] [ 'HAVING' expr ] [ order ] [ limit ] [ offset ]
select_items     = ( '*' | select_item (',' select_item)* )
select_item      = aggregate_call | IDENT [ 'AS' IDENT ]
col_list         = IDENT (',' IDENT)*
```

聚合别名：SELECT 列表中 `COUNT(*) AS n`，n 在 HAVING 中可作为列名引用。

### Executor 路径

```python
def execute_select(stmt, table, ...):
    rows = [r for r in scan_table(table) if eval_expr(stmt.where, r, ...)]

    if stmt.group_by or has_aggregate_call(stmt.columns):
        out = run_aggregation(rows, stmt, table.schema)
    else:
        out = rows

    if stmt.having:
        out = [r for r in out if eval_expr_on_aggregate_row(r, stmt.having, agg_aliases=stmt.aggregate_aliases)]
    if stmt.order_by:
        out = stable_sort(out, stmt.order_by, ...)
    if stmt.offset:
        out = out[stmt.offset:]
    if stmt.limit is not None:
        out = out[:stmt.limit]
    return project(out, stmt.columns)
```

聚合函数实现：

| 函数 | NULL 处理 | 输入类型 | 输出类型 |
|------|-----------|----------|----------|
| COUNT(*) | 不跳过任何行 | any | INT |
| COUNT(expr) | NULL 不计 | any | INT |
| SUM | 跳过 NULL | INT/FLOAT | INT/FLOAT |
| AVG | 跳过 NULL | INT/FLOAT | FLOAT |
| MIN / MAX | 跳过 NULL | 可比类型 | 与输入同 |

### 聚合行表示

`AggregateRow` = `dict[str, Any]`：
- GROUP BY 列作为组键展开为单字段
- 聚合结果以 alias（或默认 `count`、`sum_x` 等）作为字段
- HAVING 在 project 之前求值；可在 HAVING 中引用 alias

## Decisions

### D1: 聚合路径走 dict-like Row 而非 Row

- **选项 A**：聚合 row 仍是 `Row`（带固定列访问） ← 选 A
- **选项 B**：dict
- **理由**：`Row` 支持 `__getitem__` 与属性访问；聚合列 shape 与 base table 异构，用 `Row._fields=(*group_cols, *aliases)` 实例化，访问模式与 base 一致

### D2: COUNT(*) 通过聚合函数表查询

- 实现层面 `COUNT(*)` 不计 NULL 而计所有行，与 `COUNT(1)` 等价

### D3: HAVING 用单独 eval_expr，列名解析顺序：alias → 聚合名 → GROUP 列

- 选项 A：独立 `eval_aggregate_having_row`，三段解析 ← 选 A
- 选项 B：复用 base eval_expr
- 理由：避免与 base eval 在 None vs 未定义之间的语义模糊

### D4: AVG 类型转换明确化

- AVG(INT) → FLOAT；显式 `CAST` 暂不在范围

## Risks

- **R1**：parser 在聚合 + GROUP BY 组合下关键字冲突（`AS` / `HAVING` 等） → 关键测覆盖每种组合
- **R2**：HAVING 引用未在 SELECT 列出的聚合别名时静默回退 → 必须显式错（类似 SQL92）
- **R3**：聚合行 row_codec 反序列化在 GROUP BY 多列时丢列 → 单元测覆盖 group key sort 稳定性
- **R4**：engine-v1 ORDER BY 与聚合结果 shape 不同导致 Row 列访问错 → 集成测覆盖

## Test Plan

- 单元 `tests/unit/test_aggregation_parser.py`：AST 节点、别名解析
- 单元 `tests/unit/test_aggregation_executor.py`：5 函数真值表 + COUNT(*) + NULL 处理
- 单元 `tests/unit/test_group_by_having.py`：GROUP 边界（空组、单组、多组）、HAVING 引用别名
- 集成 `tests/integration/test_aggregation.py`：完整 SELECT...GROUP BY...HAVING...ORDER BY...LIMIT/OFFSET 链
- e2e golden `tests/e2e/sql/aggregation/`：~10 条 SQL
- 反向：MVP + engine-v1 + constraints 测试不回归
