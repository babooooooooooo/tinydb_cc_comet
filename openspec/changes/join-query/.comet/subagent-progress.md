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
| Task 6 INNER/CROSS JOIN 执行 | pending | — | — | — | 0 | — | ☐ |
| Task 7 LEFT/RIGHT/FULL + USING/NATURAL Coalesce | pending | — | — | — | 0 | — | ☐ |
| Task 8 JOIN 后阶段 (WHERE/GROUP/HAVING/ORDER/LIMIT) | pending | — | — | — | 0 | — | ☐ |
| Task 9 Python API (Row.__getitem__ + explain_plan) | pending | — | — | — | 0 | — | ☐ |
| Task 10 错误传播 + 完整回归 + 文档 | pending | — | — | — | 0 | — | ☐ |

## 当前阶段

`dispatch Task 2`：re-dispatch after plan-fix（见「Plan 修复」节）。

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