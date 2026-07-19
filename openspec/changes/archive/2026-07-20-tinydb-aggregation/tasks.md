# Tasks: tinydb-aggregation

## 1. Parser 聚合关键字

- [ ] 1.1 编写 `tests/unit/test_aggregation_parser.py::test_tokenizer_count_sum_avg_min_max`，红
- [ ] 1.2 在 `tokenizer.py` 加 `COUNT` `SUM` `AVG` `MIN` `MAX` `GROUP` `HAVING` 关键字
- [ ] 1.3 编写 `test_parser_aggregate_call_with_alias`，红

## 2. Parser AST 节点

- [ ] 2.1 在 `parser.py` 新增 `AggregateCall(func: str, arg: Expr | '*', alias: str | None)`
- [ ] 2.2 编写 `test_parser_select_with_aggregate_call_and_alias`，绿
- [ ] 2.3 在 `Select` dataclass 增加 `group_by: tuple[str, ...]` 与 `having: Expr | None` 字段

## 3. Parser GROUP BY / HAVING

- [ ] 3.1 编写 `test_parser_group_by_single_multi_column`，红
- [ ] 3.2 在 `parse_select` 接入 `GROUP BY col_list`
- [ ] 3.3 编写 `test_parser_having_with_alias_reference`，红
- [ ] 3.4 接入 `HAVING expr`；expr 上下文允许 alias 列名

## 4. Executor 聚合核心

- [ ] 4.1 在 `executor.py` 新增 `apply_aggregate(rows, group_by, select_items, schema) -> list[dict]`
- [ ] 4.2 编写 `test_aggregate_count_star_with_group_by`，红
- [ ] 4.3 实现 `COUNT(*)` 不计 NULL、计所有行
- [ ] 4.4 编写 `test_aggregate_sum_avg_min_max`，红
- [ ] 4.5 实现 SUM/AVG/MIN/MAX，NULL 跳过；AVG 转 FLOAT
- [ ] 4.6 编写 `test_aggregate_no_group_by_single_group`，红
- [ ] 4.7 实现"无 GROUP BY 时整张表视为单组"

## 5. HAVING 与 ORDER 链

- [ ] 5.1 编写 `test_having_alias_filter_only_aggregate_rows`，红
- [ ] 5.2 实现 `apply_having(rows, expr, aliases)`
- [ ] 5.3 在 SELECT executor 主路径接入 group → aggregate → having → order → limit/offset 链
- [ ] 5.4 编写 `test_full_select_chain_aggregate_with_order_limit`，绿

## 6. 异常与边界

- [ ] 6.1 编写 `test_aggregate_in_where_raises_error`，红（聚合函数不能出现在 WHERE）
- [ ] 6.2 实现：WHERE 解析期检测到 AggregateCall 即抛 ParseError
- [ ] 6.3 编写 `test_having_reference_unknown_alias_raises`，绿
- [ ] 6.4 编写 `test_group_by_empty_table_returns_one_empty_row`，绿

## 7. 回归

- [ ] 7.1 MVP 既有 234 测试、engine-v1 测试、constraints 测试全绿
- [ ] 7.2 e2e golden `tests/e2e/sql/aggregation/`，覆盖每种聚合 + 链式子句组合
- [ ] 7.3 模块行数：`parser.py ≤ 830`、`executor.py ≤ 820`
- [ ] 7.4 覆盖率 ≥ 90%；新代码 100%
