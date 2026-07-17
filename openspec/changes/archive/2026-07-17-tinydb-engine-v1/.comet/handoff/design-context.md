# Comet Design Handoff

- Change: tinydb-engine-v1
- Phase: design
- Mode: compact
- Context hash: c1c368a0ad60f98d644da03385ea1c493da4298d286321305202d86458576a09

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tinydb-engine-v1/proposal.md

- Source: openspec/changes/tinydb-engine-v1/proposal.md
- Lines: 1-62
- SHA256: a9046dd23bdf1ace226b595b71464e48508878fff933911ab4d33469f7c501ce

```md
# Proposal: tinydb-engine-v1

> **范围声明**：本 change 是 `tinydb-mvp` 之后**第一个引擎扩展里程碑**，补齐 SQL 读取与修改操作中的"形"部分：UPDATE、WHERE 复合条件、结果集排序与切片。完整提案见仓库根 `tinydb-proposal.md`。本 change **不改存储层**（页格式、catalog、pager 与 MVP 一致），仅在 `parser.py` 和 `executor.py` 上下刀；不引入索引；不做约束；不引入事务。

## Why

`tinydb-mvp` 已经能跑通 `CREATE / INSERT / SELECT * / SELECT WHERE col = lit / DELETE`。MVP 后立刻遇到的真实需求有三个：
1. 修改数据必须靠 `DELETE + INSERT`，代价是 PK 一旦引入就会撞——UPDATE 是基本盘
2. WHERE 只能写 `col = lit`，任何"近实时"过滤（用户状态 + 时间窗）都只能拉到客户端过滤
3. SELECT 返回顺序由页内 slot 顺序决定，无法实现"前 10 条""按时间倒序"

把这三个能力合一发版，是因为它们共享同一层代码（parser AST 节点 + executor 遍历），分三次交付只会让中间态多两个无法独立验证的"半完成 AST"。

## What Changes

- **新增** parser AST 节点：`Update`（含 SET 子句列表 + WHERE 子句）
- **新增** parser 表达式：`AndExpr` / `OrExpr` / `NotExpr`，组合现有 `EqualsExpr` / `Literal`
- **新增** parser 子句：`OrderByItem`（列名 + ASC/DESC）、`LimitClause`、`OffsetClause`（作为 `Select` 上的可选子句链）
- **新增** parser 关键字：`UPDATE`、`SET`、`AND`、`OR`、`NOT`、`ORDER`、`BY`、`ASC`、`DESC`、`LIMIT`、`OFFSET`
- **新增** tokenizer 关键字集合（保持向后兼容，新增 token 走保留字路径，不破坏现有脚本）
- **新增** executor：
  - `Update` 路径：定位匹配行 → 反序列化 → 修改列 → 重新编码 → 优先 `SlottedPage.update` in-place，等长或变短时整槽更新，变长时退化为 `delete + insert`（同事务内）
  - WHERE 复合条件：`filter_row(row, expr) -> bool`，递归 AND/OR/NOT 评估
  - SELECT 末尾链：先 `sort`（按 OrderByItem 列表稳定排序，None 排尾），再 `slice(OFFSET, OFFSET+LIMIT)`
- **新增** ROW 排序规则：NULL 视为末位（所有类型显式不支持 NULL，仅 BOOL falsy 不算 NULL）
- **扩展** `executor.py::Row` 不变性维持；UPDATE 返回受影响行数（DML 一致地返回 `list[Row]` 空表）
- **修改** `parser.py` 仅增加节点与关键字，不改现有 SELECT/INSERT/DELETE/CREATE/DROP 的解析路径
- **修改** `executor.py` 仅追加 `Update` / `SortSlice` / `RecursiveFilter` 三个操作，不动 scan / insert / delete 主路径

## Capabilities

### New Capabilities

- `sql-update-statement`：`UPDATE <table> SET <col=expr>[, ...] WHERE <expr>` 语法；expr 复用 `tinydb-engine-v1` 的复合表达式
- `sql-where-combinators`：WHERE 子句支持 `AND`、`OR`、`NOT` 任意嵌套；右侧 literal 类型仍严格（与 MVP 一致）
- `sql-select-order-limit`：SELECT 末尾可选 `ORDER BY <col>[ASC|DESC][, ...] [LIMIT N] [OFFSET N]`；三者都缺省时行为与 MVP 完全等价

### Modified Capabilities

- `sql-minimal-parser`：新增 AST 节点 / 关键字（保持向后兼容）
- `storage-engine`：仅复用其 `SlottedPage.update` / `delete` 原语，不动页格式
- `python-api`：`Database.execute` 返回类型签名不变（仍 `list[Row]`）

## Impact

- 受影响文件：`src/tinydb/parser.py`（+~150 行）、`src/tinydb/executor.py`（+~120 行）、`src/tinydb/tokenizer.py`（+~15 行关键字）
- 模块行数预算：
  - `parser.py` ≤ 750 行（从 600 上调）
  - `executor.py` ≤ 520 行（从 400 上调）
- 测试新增：单元 ~30 个、集成 ~10 个、e2e golden ~10 个新增 SQL
- 不引入新依赖；不破坏外部 API；不引入存储格式变更；不引入并发
- REPL 完全兼容（不需要任何 flag）

## Out of Scope（本 change 明确不做）

- 列约束（NOT NULL / PRIMARY KEY / UNIQUE）→ 留 `tinydb-constraints`
- 聚合（COUNT / SUM / AVG / GROUP BY / HAVING）→ 留 `tinydb-aggregation`
- 索引（B-tree）→ 留 `tinydb-engine-v2`（依赖 schema 约束 / 多页 catalog）
- 事务（BEGIN / COMMIT / ROLLBACK）→ 留 `tinydb-acid`（UPDATE 失败时无回滚，依赖 MVP "直接落盘" 行为）
- 扩展类型（VARCHAR / DECIMAL / DATE）→ 留 `tinydb-types`
- JOIN、子查询、视图、触发器、用户/权限、网络协议 → 永久 out
- ALTER TABLE、ALTER COLUMN → 永久 out（数据库 schema 变更是从零设计级问题）

```

## openspec/changes/tinydb-engine-v1/design.md

- Source: openspec/changes/tinydb-engine-v1/design.md
- Lines: 1-178
- SHA256: 2ee731ff24287105a27397c293bcaf3a5a76ee46c78f2b0cc66844e29a0d4b37

[TRUNCATED]

```md
# Design: tinydb-engine-v1

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

`tinydb-mvp` 已经能跑通端到端最小 SQL。MVP 后第一个用户故事是"改一行 + 按条件过滤 + 取前 N 条按时间倒序"。这恰好是 SQL DML 三大基础能力，本 change 不动存储层，只在 parser 与 executor 上叠加。具体决策点：

1. WHERE 复合条件用表达式树还是布尔标志位？
2. UPDATE 失败时回滚还是 best-effort？
3. ORDER BY 多键稳定排序怎么做？

## Goals / Non-Goals

**Goals：**
- UPDATE 语法与 SQL92 方言最小子集对齐；受影响行数可观察
- WHERE 支持 AND/OR/NOT 任意嵌套；短路语义（AND 遇 False 即返回，OR 遇 True 即返回）
- ORDER BY 多列稳定排序（次键仅在主键相等时比较），NULL 一律排末位
- LIMIT / OFFSET 与 ORDER BY 配套使用；OFFSET 单独存在也允许（语义：跳过前 N 条）
- UPDATE 实现优先 in-place，长度增长时退化为 delete + insert，不引入事务

**Non-Goals（本期不做）：**
- 不引入事务（UPDATE 失败中途可能留下部分状态）
- 不引入约束（UPDATE 可能违反假设的 NOT NULL，仅以 ReferenceError 抛错）
- 不引入索引（ORDER BY 走 Python sort，与 MVP 全表扫描同复杂度）
- 不引入新的存储格式
- 不引入 JOIN / 子查询 / 视图

## Architecture

### AST 新增节点

```python
@dataclass(frozen=True)
class Update:
    table: str
    sets: tuple[tuple[str, Expr], ...]   # 顺序敏感
    where: Expr | None

@dataclass(frozen=True)
class AndExpr:
    left: Expr
    right: Expr

@dataclass(frozen=True)
class OrExpr:
    left: Expr
    right: Expr

@dataclass(frozen=True)
class NotExpr:
    operand: Expr

@dataclass(frozen=True)
class OrderByItem:
    column: str
    descending: bool
```

`Select` 增加可选字段（不破坏现有实例化）：

```python
@dataclass(frozen=True)
class Select:
    table: str
    columns: tuple[str, ...]
    where: Expr | None = None
    order_by: tuple[OrderByItem, ...] = ()
    limit: int | None = None
    offset: int | None = None
```

### Parser 语法扩展

WHERE 优先级（自上而下）：

```
expr        = or_expr
or_expr     = and_expr ('OR' and_expr)*
and_expr    = not_expr ('AND' not_expr)*

```

Full source: openspec/changes/tinydb-engine-v1/design.md

## openspec/changes/tinydb-engine-v1/tasks.md

- Source: openspec/changes/tinydb-engine-v1/tasks.md
- Lines: 1-54
- SHA256: a947401166bea887c987ddf0584f976bab6052632bbbce5556ab1e3703e86a0e

```md
# Tasks: tinydb-engine-v1

> **实施起点**：`proposal.md` + `design.md` + `specs/*.md` 已确认。
> **TDD 模式**：每个任务遵循"红 → 绿 → 重构"循环。

## 1. Parser：表达式节点

- [ ] 1.1 编写 `tests/unit/test_engine_v1_parser.py::test_and_or_not_associativity` 等 AST 节点测，红
- [ ] 1.2 在 `src/tinydb/parser.py` 新增 `AndExpr/OrExpr/NotExpr` dataclass；不接入语法，仅节点存在
- [ ] 1.3 编写 `test_update_statement_minimal`（UPDATE 表 SET col=lit），红
- [ ] 1.4 在 `parser.py` 接入 UPDATE 语法（含 `SET` 关键字），绿

## 2. Parser：WHERE 复合条件

- [ ] 2.1 编写 `test_or_short_circuits_left_true`、`test_and_short_circuits_left_false` 等真值表测，红
- [ ] 2.2 在 `parser.py` 实现 AND/OR/NOT 优先级（OR < AND < NOT < primary < comparison）
- [ ] 2.3 编写 `test_not_expr_negates`，绿；测试嵌套 `a = 1 AND NOT (b = 2 OR c = 3)`

## 3. Parser：ORDER BY / LIMIT / OFFSET

- [ ] 3.1 编写 `test_order_by_asc_desc_multi_key`、`test_limit_offset_chain` AST 测，红
- [ ] 3.2 在 `parser.py` 接入 `ORDER BY <col>[ASC|DESC][, ...]`、`LIMIT N`、`OFFSET N`
- [ ] 3.3 在 `Select` dataclass 上扩展三个可选字段（保持默认值为 backward-compatible）

## 4. Tokenizer：关键字

- [ ] 4.1 编写 `test_tokenizer_keyword_<NAME>` 测每个新关键字区分大小写、不识别为标识符，红
- [ ] 4.2 在 `tokenizer.py` 增加 keyword 表：`UPDATE SET AND OR NOT ORDER BY ASC DESC LIMIT OFFSET`
- [ ] 4.3 编写 `test_tokenizer_keyword_conflict_with_column` 显式拒绝 `UPDATE AS COLUMN`

## 5. Executor：表达式求值

- [ ] 5.1 编写 `tests/unit/test_engine_v1_executor.py::test_eval_expr_*`，红
- [ ] 5.2 在 `executor.py` 新增 `eval_expr(expr, row, schema) -> bool`，实现 AND 短路、OR 短路、NOT 递归
- [ ] 5.3 在 SELECT executor 主路径上把 `where` 替换为 `eval_expr`（保持 MVP `col = lit` 行为）

## 6. Executor：UPDATE 实现

- [ ] 6.1 编写 `test_executor_update_in_place_no_grow`、`test_executor_update_grows_calls_delete_insert`，红
- [ ] 6.2 在 `executor.py` 新增 `execute_update(stmt, pager, catalog)`；路径：scan → filter → apply sets → encode → try in-place update → fallback
- [ ] 6.3 让 `Database.execute` 调度 `Update` AST 节点

## 7. Executor：排序与切片

- [ ] 7.1 编写 `test_executor_select_sorts_and_slices`、`test_select_order_by_stable_when_tied`，红
- [ ] 7.2 在 SELECT 路径末尾加 `sort` + `slice(offset, offset+limit)`；sort key 为 `(value, slot_id)`，DESC 取负
- [ ] 7.3 LIMIT/OFFSET/ORDER BY 单独存在也要能跑通（验证边界）

## 8. 测试与回归

- [ ] 8.1 重跑 MVP 既有 234 个测试必须全部通过
- [ ] 8.2 新增 e2e golden：`tests/e2e/sql/engine_v1/` 含 UPDATE、复合 WHERE、ORDER+LIMIT
- [ ] 8.3 模块行数回归：`parser.py ≤ 750`、`executor.py ≤ 520`
- [ ] 8.4 覆盖率 ≥ 90% across project；变更模块 100%

```
