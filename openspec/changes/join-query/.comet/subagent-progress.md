# Subagent 派发进度（join-query）

- Change: join-query
- Worktree: /home/lz/projects/tinydb-worktrees/tinydb-join-query
- Branch: feature/20260723/join-query
- Plan: docs/superpowers/plans/2026-07-23-join-query.md
- Design Doc: docs/superpowers/specs/2026-07-23-join-query-design.md
- base-ref (plan header): 1ca8179b1fd9864102704d396e8e976a0d49d168
- Plan commit (post-plan/change skeleton): e8d0be8
- build_mode: subagent-driven-development
- tdd_mode: tdd
- review_mode: thorough (每个 task 派发 reviewer + spec+quality 合并 + 最终完整 reviewer)
- isolation: worktree
- subagent_dispatch: confirmed

## 派发循环状态

| Plan Task | 阶段 | 提交 | RED/GREEN | reviewer 状态 | review-fix 轮次 | 风险信号 | 勾选 |
|-----------|------|------|-----------|---------------|----------------|----------|------|
| Task 1 Tokenizer 关键字与 `.` 标点 | done | 2417ecb | 7 fail → 29 pass (tokenizer) + 698 pass (regression) | APPROVED_WITH_CONCERNS (MINOR: spec_id markers deferred to Task 2) | 0 | partial: input parsing | [x] |
| Task 2 Parser AST + FROM/JOIN 解析 | done | 961af68 | 14 fail → 30 pass (parser) + 712 pass (full) | APPROVED_WITH_CONCERNS (MINOR parser.py 1509 vs ≤1300; JoinOnPredicate doc补充; ORDER/GROUP qualifier test gaps) | 0 | parser.py +209 over budget | [x] |
| Task 3 ResolutionError 子类型 | done | b081c6e | 8 fail → 8 pass (join_errors) + 720 pass (full) | APPROVED_WITH_CONCERNS (MINOR test unused pytest import; missing attr/message regression asserts; ACCEPT errors.py 140/140 at budget) | 0 | public API: 6 new errors + 4 AST re-exports | [x] |
| Task 4 Resolver 模块 | done | 05dc6b1 | 14 fail → 13 pass + 1 skip (resolver) + 733 pass (full) | APPROVED_WITH_CONCERNS (IMPORTANT _fold_equals_expr 4-tuple → Task 7.3 3-tuple; MINOR multi-NATURAL outer_kind clobbering; 4 MINOR cosmetic; ACCEPT 4 plan deviations) | 0 | resolver.py 320/450 budget; cross-module imports stable | [x] |
| Task 5 LogicalPlan 中间层 | done | 8433fe0 | 9 fail → 9 pass (plan) + 742 pass (full) | APPROVED_WITH_CONCERNS (PREVENTIVE-C1 multi-JOIN keys/expr 切分→Task 6/7; PREVENTIVE-C2 Aggregate 空壳→Task 6/8; 4 Plan deviations accepted; Limit bottom-up) | 0 | plan.py 221/350 budget; public API 9 new exports | [x] |
| Task 6 INNER/CROSS JOIN 执行 | done | 4009b94 + a1692c7 + 39b0c61 | 13 pass + 1 skip (join tests) + 755 pass + 2 skip (full) | spec ✅; quality APPROVED_WITH_CONCERNS (I-1 chained USING→Task 7 test; I-2 nested-next() fixed in 39b0c61; I-3 silent _eval_aggregate→Task 8 fail-loud; I-4 DEV-2 marker fixed in 39b0c61) | 0 | executor.py +22/30 budget; _join_executor.py 411/450; 6 NEW file | [x] |
| Task 7 LEFT/RIGHT/FULL + USING/NATURAL Coalesce | done | 86bac80 + eb27baa | 14+6+2=22 join tests pass + 765 full + 1 skip | spec ❌→✅ after fix (CRITICAL RIGHT JOIN column-order on SELECT * fixed in eb27baa via direct _nested_loop_right; MODERATE _fold_equals_expr docstring + dead 3-tuple branch removed) | 1 | _join_executor.py 600/500 (+20%); resolver.py 453/230 (+97%) — formal deviations recorded | [x] |
| Task 8 JOIN 后阶段 (WHERE/GROUP/HAVING/ORDER/LIMIT) | done | cd1a512 + 630c176 | 8 pass (integration join_post_phases) + 773 pass + 1 skip (full) | spec ✅; quality APPROVED_WITH_CONCERNS (MINOR: dead _resolve_order_key removed in 630c176; ambiguous bare column error msg; HAVING qualifier.col not yet parser-aware; AggregateCall.arg tuple-length branching) | 0 | _join_executor.py 671/700 (within); resolver.py 559/500 (+59 NEW deviation: T8 fold expansion); plan.py 223/350 (within); parser.py 1520/1300 (+220 pre-existing T2 C3 +11); coverage 91.95%; Task 6 I-3 follow-up completed | [x] |
| Task 9 Python API (Row.__getitem__ + explain_plan) | done | 2c617f5 | 6 pass (join_row_api) + 4 pass (explain_plan) + 783 pass + 1 skip (full) | spec ✅; quality APPROVED_WITH_CONCERNS (MINOR DV-T9-1: explain_plan("") raises IndexError instead of documented error → Task 10 follow-up) | 0 | database.py 155/160 (within); __init__.py 35/35 (at); coverage 92.01%; Row.__getitem__ (T6) verified unchanged | [x] |
| Task 10 错误传播 + 完整回归 + 文档 | done | 3262279 + cbbac20 + 0e0e221 + 1798bdb + d0c00f8 + a82e449 | 8 pass (e2e golden) + 796 pass + 1 skip (full); coverage 92.36% (new modules ≥85%) | spec ✅; quality APPROVED_WITH_CONCERNS (final reviewer: 15/15 §11 acceptance PASS; 2 MINOR follow-ups: LogicalPlan.format() instance method, SELECT-clause ValueError vs UnknownQualifiedColumn); DV-T9-1 fixed in 3262279 | 0 | 6 deviations recorded in verify report §5; pyflakes clean; OpenSpec CLI missing → manual delta spec verification (DV-T10-2) | [x] |

## 当前阶段

`dispatch Task 8 implementer`：JOIN 后阶段 (Filter / Project / Aggregate / Sort / Limit 在合并 schema 上消费) + GROUP BY/HAVING/ORDER BY 限定列支持 + 修正 `_eval_aggregate` 改为 fail-loud（I-3 follow-up） + Task 8 preventive follow-ups（per Plan §8.3-8.5）。

## Plan 修复（coordinator-only）

Task 2 implementer 报告 BLOCKED（4 处 Plan 与 Design Doc / OpenSpec 不一致）。协调者裁定：3 处为 Plan 写作 bug，1 处为 Plan scope gap。Design Doc §5.1 与 OpenSpec 不变；以下为 Plan Task 2 的修正：

1. **`JoinKey` 字段顺序 + 默认值**：调整为 `left_col: int / right_col: int / label: str / source_left: str / source_right: str`，移除默认值（与 Design Doc §5.1 一致）。
2. **NATURAL 关键字位置**：`_parse_join_clause` 重写为 `[NATURAL] [kind] JOIN right`，先消费可选 NATURAL，再消费可选 kind（INNER/LEFT/RIGHT/FULL/CROSS），最后期望 JOIN。捕获起始 token 用于错误位置与 JoinClause line/col。
3. **缺键错误位置**：改为指向 `first_tok`（NATURAL 或 JOIN 关键字）的 line/col，不再用 `peek()`（常为 EOF）。
4. **ON 谓词范围**：Task 2 仅实现基础列对列比较（新增 `JoinOnPredicate(left=ColumnRef, op=str, right=ColumnRef)`），复杂 AND/OR/NOT 由 Task 8 实现。当前路径遇到非比较 token 时抛 `ParseError` 提示。
5. **`_parse_join_predicate` / `_parse_qualified_column_ref` 新增**：配合 ON 谓词基础实现。

OpenSpec delta spec sql-minimal-parser / sql-join-query 无需变更（场景已对齐）。

## Model 选择策略

- implementer / 修复 agent：sonnet（plan 完整、有 spec，prose 描述为主）
- per-task reviewer (thorough)：sonnet（spec+quality 合并检查）
- final reviewer (thorough)：opus（最大 diff 范围 + 架构判断）

## 风险信号（comet-dispatch.md）

跨模块协调、安全敏感、并发/schema 迁移、public API 变更、单 task diff > 200 行 — 这些都需 implementer 自报。