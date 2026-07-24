## 1. Tokenizer 与 AST 基础

- [x] 1.1 先编写 tokenizer 回归测试：JOIN/INNER/LEFT/RIGHT/FULL/OUTER/CROSS/ON/USING/NATURAL 关键字大小写、限定名中的 `.`、非法连续限定符和错误位置。
- [x] 1.2 扩展 tokenizer 的关键字和标点分类，保持 v0.1 字面量、注释和既有标点行为不变。
- [x] 1.3 编写 parser AST 测试，覆盖 TableRef / JoinClause / JoinKey / JoinOnPredicate / ColumnRef 结构与 NATURAL 前缀 / OUTER 可选 / USING 多列 / 列对列 ON / 缺键错误位置等边界。
- [x] 1.4 在 parser.py 添加 TableRef / JoinClause / JoinKey / JoinOnPredicate / ColumnRef AST 与 `[NATURAL] [kind] JOIN right [ON/USING]` 解析，保持 v0.1 单表 SELECT 与 aggregation / engine-v1 / acid / constraints parser 全部通过。
- [x] 1.5 把 JOIN AST / 限定列 / 错误位置策略回写到 Design Doc §5.1 引用并保留 JoinOnPredicate doc 补充为 follow-up deviation。

## 2. 名称解析与合并 schema

- [x] 2.1 编写 resolver 测试：表/别名映射、重复别名、未知表、限定列、唯一裸列、歧义裸列、USING 缺失/类型不兼容和 NATURAL 共同列发现（Task 4 已交付：14 测试，13 pass + 1 skip composite ON → Task 8）。
- [x] 2.2 新建独立 resolver 模块 `src/tinydb/resolver.py`，按 FROM/JOIN 左到右构造来源映射和合并 schema（Task 4 已交付：320 行，ResolvedSource / ResolvedPlan / _build_source_map / _resolve_using_or_natural / _make_resolver / resolve / _fold_expr）。
- [ ] 2.3 统一解析 SELECT、ON、WHERE、ORDER BY、GROUP BY、HAVING 和聚合参数，输出稳定的列位置/标签（Task 4 部分交付 WHERE/ON/GROUP/ORDER，SELECT projection 留给 Task 5/6，HAVING 留给 Task 6）。
- [x] 2.4 (partial) 实现 USING 等值 JoinKey 和 NATURAL 同名 JoinKey 共同列发现；合并键输出标签（Task 4 已交付：_resolve_using_or_natural）；错误层级 ResolutionError + 6 子类型（Task 3 已交付）。

## 3. LogicalPlan 中间层

- [x] 3.1 编写 plan 构造测试：单表计划、左深多表计划、各种 Join kind、ON/USING/NATURAL key 字段、节点字段、子节点顺序和无副作用构造（Task 5 已交付：9 测试，catalog/pager 副作用自由验证）。
- [x] 3.2 新建不可变 plan 模块 `src/tinydb/plan.py`，定义 Scan / Join / Filter / Aggregate / Sort / Project / Limit 等逻辑节点（Task 5 已交付：221 行，frozen dataclass + Union 类型别名 + format_plan）。
- [x] 3.3 (partial) 实现从 Select AST 到 LogicalPlan 的构造 `build_plan(ast, catalog)`，集中完成名称解析、隐式 NATURAL key 生成和阶段顺序编排（Task 5 已交付：单 JOIN 完整，multi-JOIN keys/expr 切分留给 Task 6/7，Aggregate group_keys/aggregates 实际填充留给 Task 6/8）。
- [ ] 3.4 为单表 Select 保留 v0.1 indexed/scan/aggregation 路径或等价适配，并添加单表计划回归护栏（Task 5 已实现单表 plan；执行层适配留给 Task 6/8）。

## 4. INNER/CROSS JOIN 执行

- [x] 4.1 先编写集成测试：两表等值连接、多级连接、复杂 ON、USING、NATURAL、空表和 CROSS 笛卡尔积，以及列顺序/标签。
- [x] 4.2 新建 JOIN 执行 helper，实现事务读路由下的 nested-loop INNER/CROSS JOIN。
- [x] 4.3 在 Executor SELECT dispatch 中仅为包含 JOIN 的查询委派 plan/JOIN helper，避免继续膨胀 `executor.py`。
- [x] 4.4 验证 JOIN 路径不绕过 `_txn_read_page`、WAL 缓冲或 `_IndexPager`，并完成 ACID 读事务回归。

**Task 6 closeout**: commit `4009b94` (impl) + `a1692c7` (pyflakes fix) + `39b0c61` (coalesce reverse-map + DEV-2 marker). Reviewer spec ✅ + code-quality APPROVED_WITH_CONCERNS. Carry-over follow-ups: **I-1** chained USING/NATURAL merge correctness (Task 7 adds USING×USING chained test) and **I-3** silent `_eval_aggregate` (Task 8 raises NotImplementedError for GROUP BY over JOIN).

## 5. LEFT/RIGHT/FULL JOIN 与 NULL 语义

- [x] 5.1 先编写 LEFT/RIGHT/FULL 测试：匹配、单侧无匹配、双方无匹配、右表多匹配、NULL、USING/NATURAL 和多级外连接。
- [x] 5.2 实现无匹配侧的 NULL 补齐、RIGHT 直接实现（取代 swap-recurse 方案）、FULL 双侧未匹配行保留，并保持声明的 Join kind。
- [x] 5.3 验证外连接后 WHERE、HAVING 和聚合对 NULL 的行为与规范一致，锁定稳定输出顺序（property 测试 + strict-left-deep-insertion）。

**Task 7 closeout**: commit `86bac80` (impl) + `eb27baa` (RIGHT JOIN column-order bugfix + docstring honesty). 5 unit + 6 integration + 2 property tests pass; full suite 765 pass + 1 skip (Task 8 follow-up). Reviewer caught a CRITICAL RIGHT JOIN column-order regression (swap-recurse produced right-first columns on `SELECT *`); fix agent replaced with direct `_nested_loop_right` and added regression test. Module budgets: `_join_executor.py` 600/500 (+20%), `resolver.py` 453/230 (+97%) — formal deviations recorded for verify report (chained JOIN I-1 fix and WHERE position remap underestimated in plan). `outer_kind` ResolvedPlan field remains unwired (deferred to follow-up).

## 6. JOIN 后查询阶段

- [x] 6.1 先编写 JOIN + WHERE/投影/SELECT * 测试，覆盖限定列、USING/NATURAL 合并键、未知列、歧义列和输出顺序。
- [x] 6.2 让过滤、投影和 wildcard 展开消费合并 schema 与 resolver，保留单表裸列标签。
- [x] 6.3 先编写 JOIN + GROUP BY/HAVING/COUNT/SUM 等聚合测试，并将聚合 helper 适配为合并 schema。
- [x] 6.4 先编写 JOIN + ORDER BY/LIMIT/OFFSET 测试，确保排序发生在限制之前并支持限定排序键。
- [x] 6.5 回归现有 aggregation、group/having、sort、limit/offset 测试。

**Task 8 closeout**: commit `cd1a512` (impl) + `630c176` (dead-code cleanup). 8 unit + 25 integration + 2 property tests pass; full suite 773 pass + 1 skip (baseline 765+1 → +8 from new tests). Reviewer spec PASS + code-quality APPROVED_WITH_CONCERNS (4 MINOR: dead `_resolve_order_key` removed; ambiguous bare column error message; HAVING not yet `qualifier.col` parser-aware; `AggregateCall.arg` tuple-length branching). Module budgets: `_join_executor.py` 671/700 (within), `resolver.py` 559/500 (+59 — NEW deviation: T8 fold of GROUP BY/HAVING/ORDER BY + `_merged_position` helper), `plan.py` 223/350 (within), `parser.py` 1520/1300 (+220 — pre-existing Task 2 C3 deviation, +11 net from T8). Coverage 91.95%. Task 6 I-3 follow-up completed: GROUP BY over JOIN now properly aggregates (not silent fallback).

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
