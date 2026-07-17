# Tasks: tinydb-engine-v1

> **实施起点**：`proposal.md` + `design.md` + `specs/*.md` 已确认。
> **TDD 模式**：每个任务遵循"红 → 绿 → 重构"循环。

## 1. Parser：表达式节点

- [x] 1.1 编写 `tests/unit/test_engine_v1_parser.py::test_and_or_not_associativity` 等 AST 节点测，红
- [x] 1.2 在 `src/tinydb/parser.py` 新增 `AndExpr/OrExpr/NotExpr` dataclass；不接入语法，仅节点存在
- [x] 1.3 编写 `test_update_statement_minimal`（UPDATE 表 SET col=lit），红
- [x] 1.4 在 `parser.py` 接入 UPDATE 语法（含 `SET` 关键字），绿

## 2. Parser：WHERE 复合条件

- [x] 2.1 编写 `test_or_short_circuits_left_true`、`test_and_short_circuits_left_false` 等真值表测，红
- [x] 2.2 在 `parser.py` 实现 AND/OR/NOT 优先级（OR < AND < NOT < primary < comparison）
- [x] 2.3 编写 `test_not_expr_negates`，绿；测试嵌套 `a = 1 AND NOT (b = 2 OR c = 3)`

## 3. Parser：ORDER BY / LIMIT / OFFSET

- [x] 3.1 编写 `test_order_by_asc_desc_multi_key`、`test_limit_offset_chain` AST 测，红
- [x] 3.2 在 `parser.py` 接入 `ORDER BY <col>[ASC|DESC][, ...]`、`LIMIT N`、`OFFSET N`
- [x] 3.3 在 `Select` dataclass 上扩展三个可选字段（保持默认值为 backward-compatible）

## 4. Tokenizer：关键字

- [x] 4.1 编写 `test_tokenizer_keyword_<NAME>` 测每个新关键字区分大小写、不识别为标识符，红
- [x] 4.2 在 `tokenizer.py` 增加 keyword 表：`UPDATE SET AND OR NOT ORDER BY ASC DESC LIMIT OFFSET`
- [x] 4.3 编写 `test_tokenizer_keyword_conflict_with_column` 显式拒绝 `UPDATE AS COLUMN`

## 5. Executor：表达式求值

- [x] 5.1 编写 `tests/unit/test_engine_v1_executor.py::test_eval_expr_*`，红
- [x] 5.2 在 `executor.py` 新增 `eval_expr(expr, row, schema) -> bool`，实现 AND 短路、OR 短路、NOT 递归
- [x] 5.3 在 SELECT executor 主路径上把 `where` 替换为 `eval_expr`（保持 MVP `col = lit` 行为）

## 6. Executor：UPDATE 实现

- [x] 6.1 编写 `test_executor_update_in_place_no_grow`、`test_executor_update_grows_calls_delete_insert`，红
- [x] 6.2 在 `executor.py` 新增 `execute_update(stmt, pager, catalog)`；路径：scan → filter → apply sets → encode → try in-place update → fallback
- [x] 6.3 让 `Database.execute` 调度 `Update` AST 节点

## 7. Executor：排序与切片

- [x] 7.1 编写 `test_executor_select_sorts_and_slices`、`test_select_order_by_stable_when_tied`，红
- [x] 7.2 在 SELECT 路径末尾加 `sort` + `slice(offset, offset+limit)`；sort key 为 `(value, slot_id)`，DESC 取负
- [x] 7.3 LIMIT/OFFSET/ORDER BY 单独存在也要能跑通（验证边界）

## 8. 测试与回归

- [x] 8.1 重跑 MVP 既有 234 个测试必须全部通过
- [x] 8.2 新增 e2e golden：`tests/e2e/sql/engine_v1/` 含 UPDATE、复合 WHERE、ORDER+LIMIT
- [x] 8.3 模块行数回归：`parser.py ≤ 750`、`executor.py ≤ 520`
- [x] 8.4 覆盖率 ≥ 90% across project；变更模块 100%
