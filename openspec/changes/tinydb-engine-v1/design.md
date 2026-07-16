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
not_expr    = 'NOT' not_expr | primary
primary     = '(' expr ')' | comparison
comparison  = IDENT '=' literal
```

UPDATE 语法：

```
update      = 'UPDATE' IDENT 'SET' assign (',' assign)* where_clause?
assign      = IDENT '=' literal         # literal 不是表达式，左值只接受字面量
```

### Executor 路径

```python
def execute_update(stmt: Update, pager, catalog):
    table = catalog.get(stmt.table)
    affected = 0
    for row in scan_table(pager, table):
        if not eval_expr(stmt.where, row, table.schema):
            continue
        new_values = apply_sets(row, stmt.sets)
        new_bytes = row_codec.encode_row(new_values, table.schema)
        try:
            table.update_row_in_place(row.slot_id, new_bytes)
        except PageFullOrLonger:
            table.delete(row.slot_id)
            table.insert_row(new_bytes)
        affected += 1
    return []

def execute_select(stmt: Select, ...):
    rows = [r for r in scan_table(...) if eval_expr(stmt.where, r, ...)]
    if stmt.order_by:
        rows = stable_sort(rows, stmt.order_by, table.schema)
    if stmt.offset:
        rows = rows[stmt.offset:]
    if stmt.limit is not None:
        rows = rows[:stmt.limit]
    return project(rows, stmt.columns)
```

### 排序 KeyFn

```python
def sort_key(row, items, schema):
    return tuple(
        (-ordering_flag(row[col]), row[col] if not None else SENTINEL)
        for ord_item in items
    )
```

NULL 不存在：MVP 列均为 NOT NULL（语义），遇到类型不匹配抛 `ExecutionError`；sort key 用 `(descending_flag, value)` 实现 ASC/DESC + 稳定次键（slot id）。

## Decisions

### D1: 表达式树而非布尔列

- **选项 A**：表达式树（`AndExpr/OrExpr/NotExpr`） ← 选 A
- **选项 B**：仅加 `and_flag / or_flag` 字段
- **理由**：MVP WHERE 已是表达式节点；引入表达式树保持同构；boolean flag 路线会随 NOT 引入快速退化

### D2: UPDATE 没有事务

- **选项 A**：in-place update + delete/insert fallback，不引入回滚 ← 选 A
- **选项 B**：引入事务保护（→ `tinydb-acid`）
- **理由**：本 change 不与 ACID 耦合；UPDATE 失败崩溃半成品状态由 `tinydb-acid` 单独兜底

### D3: 表达式 strict type 不放松

- **选项 A**：右值 literal 与列类型严格一致（与 MVP WHERE 一致） ← 选 A
- **选项 B**：SET 右值允许跨类型隐式转换
- **理由**：与 MVP strict typing 一致；放宽是 `tinydb-types` 的事

### D4: SELECT 排序算法用 Python stable sort

- **选项 A**：Python `sorted(rows, key=...)`，key 为 `(value, slot_id)` ← 选 A
- **选项 B**：实现归并排序
- **理由**：MVP 无大表；稳定排序通过 slot_id 次键保证；复杂度 O(n log n) 在 n ≤ 10k 内可接受

### D5: ORDER BY 解析一次后只读

- Select AST freeze 后 `order_by` 不变；不再做 lazy rewrite

## Risks

- **R1**：parser 改动可能破坏现有 SELECT/INSERT/DELETE 路径 → 既有 234 个测试必须全绿
- **R2**：UPDATE 撞 PageFull 时 delete/insert 中途崩溃可能丢数据 → 文档明确披露在 `docs/MVP_LIMITATIONS.md`，由 `tinydb-acid` 修复
- **R3**：AND/OR 短路语义错误（先评估 False OR ...）→ 单元测试显式构造 2 测 1 评场景
- **R4**：parser 关键字加入后旧脚本 USING/UPDATE 列名会被识别为关键字 → 在 tokenizer 上加 `KEYWORD_TABLE` 白名单（标识符冲突时显式报错）

## Test Plan

- 单元覆盖（`tests/unit/test_engine_v1_parser.py`）：AST roundtrip，每个新关键字一对一测
- 单元覆盖（`tests/unit/test_engine_v1_executor.py`）：AND/OR/NOT 真值表，ORDER BY 稳定多键，LIMIT/OFFSET 边界（OFFSET=0, LIMIT=0, LIMIT>rows）
- 集成覆盖（`tests/integration/test_engine_v1.py`）：UPDATE in-place、UPDATE 增长碰撞 fallback、SELECT 链式 ORDER+LIMIT+OFFSET、复合 WHERE 多页跨 slot
- e2e golden：`tests/e2e/sql/engine_v1/` 新增 ~10 条 SQL（覆盖每个新增语法特性）
- 反向测试：MVP 既有路径 234 个测试全部继续通过（不变更）
