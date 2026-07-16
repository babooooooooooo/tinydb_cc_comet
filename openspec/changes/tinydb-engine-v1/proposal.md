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
