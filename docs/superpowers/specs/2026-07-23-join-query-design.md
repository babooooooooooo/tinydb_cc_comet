---
comet_change: join-query
role: technical-design
canonical_spec: openspec
status: final
---

# Design: join-query

> **关联文档**：[proposal.md](../../../../openspec/changes/join-query/proposal.md) · [design.md](../../../../openspec/changes/join-query/design.md) · [tasks.md](../../../../openspec/changes/join-query/tasks.md)
> **Brainstorm checkpoint**：[brainstorm-summary.md](../../../../openspec/changes/join-query/.comet/handoff/brainstorm-summary.md)
> **Date**：2026-07-23
> **承接 change 名**：`join-query`
> **产物语言**：zh-CN

本文档对 `join-query` change 做深度技术设计，落实 open 阶段 proposal/design/tasks 的高层架构选择，并对接 v0.1 parser、executor、catalog、pager、WAL、B+Tree 与 transaction 基线。设计目标：在不重写存储与不破坏单表行为的前提下，引入 LogicalPlan 中间层、可观测的 JOIN 语义与无副作用的 `.explain` API。

---

## 1. Context

v0.1（`main@1ca8179`）已落地以下基线：

- `src/tinydb/tokenizer.py`（162 行）：关键字/标点/字面量分类已稳定，错误携带 `line/col/msg`。
- `src/tinydb/parser.py`（1192 行）：`Select` AST、`EqualsExpr/AndExpr/OrExpr/NotExpr` 表达式、`SelectItem/AggregateCall`（aggregation 集成）、`OrderByItem`、`_parse_select` 单表 `FROM` 解析；`StatementList` 顶层多语句。
- `src/tinydb/executor.py`（1718 行）：5-phase aggregation pipeline、`_exec_select` dispatch、`_exec_indexed_select` / `_exec_aggregate_select` / `_exec_scan_select` 三条 fast path；已通过 `_executor_drop.py` / `_executor_snapshot.py` / `_executor_sort.py` / `_index_pager.py` / `_schema.py` 五个 helper 模块降低单文件膨胀。
- `src/tinydb/database.py`：`Database.execute(sql)`、冻结 dataclass `Row`（`values` / `columns` 元组对齐）、事务边界由 `Executor._exec_in_txn` 提供。
- `src/tinydb/errors.py`：`TinydbError` / `ParseError` / `ExecutionError` / `ConstraintViolation` / `PageFull` / `CatalogFull` / `InvalidDatabaseFile` / `UnsupportedSchemaVersion` / `SchemaMismatch`。
- 存储：Pager、B+Tree、Catalog、WAL、Recovery、Transaction、type codec registry。

**核心约束**：

1. `Select` AST 当前只持有 `table: str`（单表名）与裸 `columns: tuple[str,...]`。`WHERE`/`ORDER BY`/`GROUP BY`/`HAVING` 表达式沿用 `EqualsExpr/AndExpr/...` 树，但列名以裸列名绑定，不存在"限定列"概念。
2. `executor.py` 已经 1718 行（已超 1000 行预算），需要靠 helper 模块继续吸收新增逻辑。
3. JOIN 结果必须能继续走现有 WHERE、投影、ORDER BY、LIMIT/OFFSET、GROUP BY、HAVING、聚合；JOIN 路径不能绕过 `_txn_read_page`/`_txn_write_page`/`_IndexPager` 与 WAL。
4. `cli-enhancement` 计划在后续 change 引入 `.explain` 命令，依赖一个只构造不执行的稳定逻辑计划接口。

**本章不讨论 OpenSpec delta spec 范围（已在 `proposal.md`/`design.md`/`tasks.md` 锁定），只解释 v0.1 基线如何约束本次设计。**

---

## 2. Goals / Non-Goals

**Goals**

1. 两表及多表 `INNER`、`LEFT`、`RIGHT`、`FULL`、`CROSS JOIN`，并支持 `ON` / `USING (col,...)` / `NATURAL`。
2. 表名/表别名/限定列引用，集中完成未知表、未知列、重复别名、歧义列的诊断。
3. JOIN 结果继续进入现有 WHERE、投影、`ORDER BY`、`LIMIT/OFFSET`、`GROUP BY`、`HAVING` 与聚合；不修改这些阶段的单表语义。
4. 引入冻结 dataclass LogicalPlan 中间层（`Scan/Join/Filter/Aggregate/Sort/Project/Limit`），仅在执行前构造一次；构造不写文件、不写 WAL、不提交事务。
5. 暴露只读 `Database.explain_plan(sql) -> LogicalPlan` 供后续 `cli-enhancement` `.explain` 消费。
6. JOIN 路径复用现有 `_txn_read_page` / WAL 缓冲 / `_IndexPager`；不绕过 ACID。
7. 引入 `tinydb.ResolutionError`（`ExecutionError` 子类），覆盖未知表、未知限定列、歧义裸列、USING/NATURAL 缺失键、类型不兼容等名称解析错误。

**Non-Goals**

1. 不实现 CTE、子查询、视图、`UNION`、分布式连接、cost-based 优化器。
2. 不重写 Pager、Catalog、WAL、B+Tree、type codec、磁盘格式。
3. 不实现 hash/merge join；首版 nested-loop 正确性优先。
4. 不在 v0.2 引入硬性 JOIN 行数上限；大结果集 OOM 风险在 `MVP_LIMITATIONS.md` 声明。
5. 不实现 CLI `.explain` 命令；只暴露 plan API。
6. 不为 v0.1 已存在数据库文件提供 JOIN 索引迁移；新能力对 v0.1 存储只读。

---

## 3. Architecture

### 3.1 数据流（high level）

```
SQL ─▶ tokenizer ─▶ parser(Select AST)
                     │
                     ▼
            resolver（来源映射 / 合并 schema / USING-NATURAL key）
                     │
                     ▼
            build_plan(ast, catalog) ─▶ LogicalPlan（冻结节点树）
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
  executor dispatch         Database.explain_plan
        │                         │
   JOIN 路径：                  仅构造并格式化
   _join_executor.             不执行 DML
   execute_plan(plan)          不写文件
        │
        ▼
  物化宽行 ─▶ Filter / Aggregate / Sort / Project / Limit
                              │
                              ▼
                       list[Row]
```

### 3.2 模块分解（架构方案 B）

`executor.py` 1718 行已经接近预算。本 change 把 JOIN 路径拆到三个新模块，`executor.py` 仅保留 dispatch：

| 模块 | 职责 | 预计行数 |
|------|------|----------|
| `src/tinydb/resolver.py`（新） | 来源映射、合并 schema、USING/NATURAL JoinKey 解析、所有列引用统一解析；定义 `ResolutionError`。 | 350-450 |
| `src/tinydb/plan.py`（新） | 不可变 `LogicalPlan` 节点 dataclass、`build_plan(ast, catalog)`、plan 文本格式化（供后续 `.explain`）。 | 250-350 |
| `src/tinydb/_join_executor.py`（新） | 事务读路由下的 nested-loop INNER/CROSS/LEFT/RIGHT/FULL，USING/NATURAL Coalesce 合并，物化宽行。 | 350-450 |
| `src/tinydb/executor.py`（修改） | `_exec_select` 仅 dispatch；包含 JOIN 时调用 `_join_executor.execute_plan(plan)`，否则走既有 `_exec_indexed_select` / `_exec_aggregate_select` / `_exec_scan_select` 之一。 | +30-50 行（不超过 1800） |

辅助改动：

- `src/tinydb/parser.py`：扩展 `_parse_select` 的 FROM/JOIN/ON/USING/NATURAL 子句；新增 `TableRef / JoinClause / JoinKey / ColumnRef` AST（frozen dataclass）并以兼容方式扩展 `Select`。
- `src/tinydb/tokenizer.py`：增加 `JOIN/INNER/LEFT/RIGHT/FULL/OUTER/CROSS/ON/USING/NATURAL` 关键字和 `.` 标点；保持现有字面量、注释、字符串转义行为不变。
- `src/tinydb/errors.py`：新增 `ResolutionError(ExecutionError)`，构造时携带 source/column/position 上下文。
- `src/tinydb/__init__.py`：再导出 `ResolutionError`、新增的 AST 节点、`LogicalPlan`、`build_plan`、`explain_plan`。
- `src/tinydb/database.py`：新增 `Database.explain_plan(sql) -> LogicalPlan`（构造并返回 plan，不写文件、不提交事务）。

### 3.3 单表 fast path 保留策略

- `_exec_select` 入口判断 `len(stmt.joins) == 0` 且无 aggregation / group_by / having 时，继续走 v0.1 `_exec_indexed_select` / `_exec_aggregate_select` / `_exec_scan_select`，**不进入 plan 构造**。这一分支的测试回归面是 v0.1 全部单表测试。
- 含 JOIN 的查询必须先调 `build_plan` 构造 plan，再交给 `_join_executor.execute_plan`。这一路径独立可测。
- aggregation / group_by 在 JOIN 后由 plan 顶层 `Aggregate` 节点承担；为单表 aggregation 保留兼容入口。

### 3.4 LogicalPlan 中间层

```python
@dataclass(frozen=True)
class Scan:
    table: str          # 解析后的基表名
    alias: Optional[str]
    schema: tuple[str, ...]   # 列名（按 Catalog）
    source_id: str              # 内部唯一 source 标识

@dataclass(frozen=True)
class Join:
    kind: str                   # "INNER" | "LEFT" | "RIGHT" | "FULL" | "CROSS"
    left: LogicalPlan
    right: LogicalPlan
    keys: tuple[JoinKey, ...]   # 0 = CROSS；ON 表达式转 Resolve 后再 fold 成等值/非等值
    on_expr: Optional[object]   # ON 表达式（已解析为列位置 + literal）
    natural: bool = False

@dataclass(frozen=True)
class Filter:
    source: LogicalPlan
    predicate: object   # 已解析为列位置 + literal/op

@dataclass(frozen=True)
class Aggregate:
    source: LogicalPlan
    group_keys: tuple[int, ...]   # 列在 source 输出 schema 的位置
    aggregates: tuple[AggregateCall, ...]

@dataclass(frozen=True)
class Sort:
    source: LogicalPlan
    keys: tuple[tuple[int, bool], ...]  # (col_index, descending)

@dataclass(frozen=True)
class Project:
    source: LogicalPlan
    items: tuple[tuple[str, object], ...]   # (label, source-expr)
    star: bool = False

@dataclass(frozen=True)
class Limit:
    source: LogicalPlan
    limit: Optional[int]
    offset: Optional[int]
```

`build_plan(ast: Select, catalog: Catalog) -> LogicalPlan` 是无副作用函数：只读 AST + Catalog 元数据，不触碰 Pager/WAL/Transaction。`Database.explain_plan(sql)` 在 tokenize+parse 之后调用 `build_plan`，调用前后 `pager.page_count()` / `wal.size()` / `catalog` 状态不变（由 property 测试断言）。

---

## 4. Decisions

### 4.1 LogicalPlan + 物化宽行（执行模型）

- 首版不引入懒迭代器。`_join_executor.execute_plan(plan)` 直接物化宽行（tuple 列表）。
- 宽行的列位置与 `LogicalPlan.output_schema` 严格对齐；Filter / Aggregate / Sort / Project / Limit 都消费 `(rows, schema)` 对。
- 后续可在此基础上叠加迭代器；接口设计预留 `def execute_plan(self, plan) -> tuple[list[tuple], tuple[str, ...]]`，未来可换返回 generator 而不破坏 plan / resolver 契约。

**取舍**：物化意味着大结果集内存占用与 v0.1 aggregation 路径相同（已用 list[tuple]），不引入新内存模型。MVP_LIMITATIONS.md 显式声明。

### 4.2 外连接顺序契约：strict-left-deep-insertion

- 连接计划按 SQL 书写顺序左深嵌套：`((t1 ⋈ t2) ⋈ t3) ⋈ t4`。
- LEFT JOIN 未匹配右行：紧跟其左行后追加一行（右列全部 NULL）。
- RIGHT JOIN：执行层把 right/left 交换，调用 LEFT JOIN helper，再按 LEFT 的 schema 顺序恢复；但 `LogicalPlan.kind` 仍记为 `RIGHT`（不可交换的契约）。
- FULL JOIN：先按 LEFT 规则输出匹配行与左未匹配行；然后追加右未匹配行（按该侧 source 的扫描顺序）。
- 无 ORDER BY 时输出顺序可测试锁定为"匹配 + 左未匹配 + 右未匹配"的拼接顺序；这是 property 测试断言的对象。

### 4.3 NATURAL JOIN 共同列缺失：退化为 CROSS JOIN

- `NATURAL JOIN` 在 resolver 阶段尝试发现左右 source 的共同列；若无共同列：
  - 不报错；
  - `LogicalPlan.kind` 仍为用户声明的 join kind（如 `NATURAL LEFT JOIN`），但 `keys` 设为空 tuple；
  - 执行层走 CROSS 路径（笛卡尔积），并按 SQL 语义保留 LEFT/RIGHT/FULL 的 NULL 补齐；
  - 错误信息（仅在调试模式/显式开启 strict_natural 时）提示"按 CROSS JOIN 处理"。

**取舍**：与 SQL 标准"无共同列 → 1 行（笛卡尔积）"语义对齐，避免破坏 JOIN × NATURAL 矩阵测试；同时为后续严格模式留接口。

### 4.4 USING/NATURAL 合并键：Coalesce 取值

- resolver 在 `LogicalPlan` 上为合并键生成一个 `MergedKey(label, left_idx, right_idx)` 记录。
- 执行层取值规则：先 left_idx 位置；若该值是 NULL，则取 right_idx 位置；两侧都 NULL 时输出 NULL。
- 输出 schema：合并键只出现一次，标签为未限定列名（USING `(id)` → 输出列 `id`）；其余列沿用 `source.column` 限定标签。
- LEFT/RIGHT/FULL 未匹配行：缺失侧对应位置用 None 标记。
- ORDER BY / GROUP BY 引用合并键时，与引用该未限定列等价。

### 4.5 名称解析：来源映射 + 限定列 + 唯一裸列 + 歧义报错

- resolver 维护 `source_map: dict[source_id, source_metadata]`，source_id 是表别名优先于表名的规范化键。
- 限定列 `qualifier.column` 必须在 source_map 中唯一命中；缺失或命中多个时报 `ResolutionError`。
- 裸列名：
  - 单 source 时行为不变（保留 v0.1 兼容）；
  - 多 source 时只在唯一 source 提供该列名时通过；多个 source 同时提供时报 `AmbiguousColumn(column='id', sources=('u','o'))`（`ResolutionError` 子类型）。
- ORDER BY / GROUP BY / HAVING / 聚合参数 全部走同一 resolver；不存在"阶段差异"。
- 重复别名（如 `FROM t AS u JOIN t AS u`）在 resolver 阶段报 `DuplicateAlias`（`ResolutionError` 子类型），附带 source 1 和 source 2 的 token 位置。

### 4.6 错误类型契约

| 阶段 | 错误类型 | 父类 | 触发场景 |
|------|----------|------|----------|
| tokenizer | `TokenError` / `ParseError` | `TinydbError` | 关键字错、`.` 在不合法位置、JOIN 缺 ON/USING |
| resolver | `ResolutionError`（含 `AmbiguousColumn`/`DuplicateAlias`/`UnknownSource`/`UnknownQualifiedColumn`/`MissingUsingKey`/`IncompatibleKeyTypes`） | `ExecutionError` | 未知表、未知列、歧义列、缺失 USING 键、左右 USING 列类型不兼容 |
| plan 构造 | `PlanError` | `ExecutionError` | 物化 plan 时发现的内部错误（防御性，正常路径不触发） |
| 执行期 | `ExecutionError` | — | 行级 NULL 比较、ROW read 失败、外连接后 WHERE/HAVING 异常 |

`tinydb.ResolutionError` 在 `__init__.py` 再导出；`python-api` delta spec 增加对应 scenario。

### 4.7 ROW 输出：source.column 限定标签 + 单一合并键

- 普通 JOIN：`row["u.id"]` / `row["o.id"]` 各自可访问；属性访问对合法 Python 标识符的标签仍兼容（如 `row.id` 当且仅当 source 中没有同名歧义）。
- USING/NATURAL 合并键：`row["id"]` 单一访问；不会同时存在 `row["u.id"]` 和 `row["o.id"]`。
- `SELECT *` 多表：按 source 书写顺序展开为 `source1.col1, source1.col2, ..., source2.col1, ...`；不含合并键重复。
- 单表查询：行为完全不变（仍为裸列标签）。
- `Row.__getattr__` 在 `name in columns` 时返回 values；否则 `AttributeError`。`Row.__getitem__` 在 JOIN 路径下增加映射访问（`database.py` 增补，详见 §5.3）。

### 4.8 测试策略

- **单元**：tokenizer 关键字 / 标点；parser FROM/JOIN/ON/USING/NATURAL/AST 结构；resolver 来源映射 / 合并 schema / 各种错误；plan 构造无副作用（property：`pager.page_count` 不变、`wal.size` 不变、`catalog.tables` key 不变）；JoinKey Coalesce 合并逻辑。
- **集成**：两表 INNER、LEFT/RIGHT/FULL 无匹配、FULL 双侧无匹配、USING/NATURAL、空表 × JOIN、CROSS 笛卡尔积、多级 JOIN 左深顺序、NATURAL 无共同列 → CROSS。
- **组合**：JOIN × WHERE / 投影 / `SELECT *` / GROUP BY / HAVING / COUNT/SUM / ORDER BY / LIMIT / OFFSET；property 测试 strict-left-deep-insertion 锁定输出顺序。
- **错误诊断**：`pytest.raises(ResolutionError)` 覆盖未知表、未知限定列、歧义裸列、USING 缺失、左右键类型不兼容、重复别名。
- **回归**：v0.1 单表 SELECT/INSERT/UPDATE/DELETE/aggregation/index/ACID/wal 全部测试必须通过。
- **API**：`Database.execute` / `Database.explain_plan` / `Row` 映射访问 / `repr` / `==`。
- **E2E / Golden SQL**：`tests/e2e/join_queries.sql` 覆盖 INNER/LEFT/RIGHT/FULL/CROSS/USING/NATURAL × 多级 / 聚合 / 排序。
- **覆盖率**：整体覆盖率不低于 v0.1 基线（≈ 93%）；新模块单独 ≥ 85%。
- **Lint / Strict**：`pyflakes` clean；`openspec validate --strict` 通过。

### 4.9 工作区与并行

- v0.2 三大能力中，JOIN 与并发控制、CLI 增强存在显式接口耦合（`cli-enhancement` 消费本 change 的 plan API）。本 change 不依赖 CLI 实现，但需在 plan 文本格式上与 CLI 协同。
- 推荐分支：`feature/20260723/join-query`（基于 `main@1ca8179`）。是否启用 worktree 隔离取决于同时进行的其他 change 计划；本 change 不强制。

---

## 5. Module & API Detail

### 5.1 tokenizer / parser 扩展

- `tokenizer.py` 新增关键字 token：
  ```
  JOIN INNER LEFT RIGHT FULL OUTER CROSS ON USING NATURAL AS
  ```
  并增加 `Token(type="PUNCT", value=".")` 用于限定名。保留现有字面量、字符串、注释、`;` 处理路径。
- `parser.py` 新增 AST（frozen dataclass）：
  ```python
  @dataclass(frozen=True)
  class TableRef:
      name: str
      alias: Optional[str]
      line: int
      col: int

  @dataclass(frozen=True)
  class JoinKey:
      """USING/NATURAL 等值键；left_col/right_col 是解析后位置（int, int）"""
      left_col: int
      right_col: int
      label: str                # 合并键标签
      source_left: str          # source_id
      source_right: str

  @dataclass(frozen=True)
  class JoinClause:
      kind: str                 # INNER/LEFT/RIGHT/FULL/CROSS
      right: TableRef
      on_expr: Optional[object] = None
      using_keys: tuple[str, ...] = ()
      natural: bool = False
      line: int = 0
      col: int = 0

  @dataclass(frozen=True)
  class ColumnRef:
      qualifier: Optional[str]
      name: str
      line: int = 0
      col: int = 0
  ```
- `Select` 增加字段（保留向后兼容默认值）：
  ```python
  from_: TableRef = None
  joins: tuple[JoinClause, ...] = ()
  ```
  并把 `columns` / `select_items` / `group_by` / `having` / `order_by` 中所有"列名字符串"逐步迁移到 `ColumnRef`（在 aggregator 集成中已经演进；JOIN change 在 resolver 阶段把所有字符串 ColumnRef 化为带 qualifier 的统一形态）。
- 解析错误：JOIN 无 ON/USING/NATURAL（且非 CROSS）→ `ParseError`，指向 JOIN 关键字；非法 `.` 用法（`..foo` / `.foo` / `foo.`）→ `ParseError`。

### 5.2 resolver 契约

```python
@dataclass(frozen=True)
class ResolvedSource:
    source_id: str            # 规范化：alias 优先，否则 table
    table_name: str
    alias: Optional[str]
    schema: tuple[str, ...]   # 列名
    column_pos: dict[str, int]   # name → 列位置

@dataclass(frozen=True)
class ResolvedPlan:
    sources: tuple[ResolvedSource, ...]
    output_schema: tuple[str, ...]
    merged_keys: tuple[JoinKey, ...]
    column_resolver: Callable[[ColumnRef], tuple[int, ResolvedSource]]   # (位置, source)
    on_resolved: tuple[object, ...]   # ON 表达式已 fold 为 (left_pos, op, right_pos/literal)
    where_resolved: object            # 已 fold
    select_resolved: tuple            # (label, source-expr)
    order_resolved: tuple
    group_resolved: tuple
    having_resolved: object
    aggregate_resolved: tuple

def resolve(ast: Select, catalog: Catalog) -> ResolvedPlan: ...
```

`ResolutionError` 子类型：
- `UnknownSource(qualifier_or_name)`：`FROM` / `JOIN` 中的表名或别名不在 catalog；
- `UnknownQualifiedColumn(qualifier, column)`：限定列名在 source_map 中无对应；
- `AmbiguousColumn(column, sources)`：裸列名命中多个 source；
- `DuplicateAlias(alias, source1, source2)`：同一别名指向多个 source；
- `MissingUsingKey(column, side)`：USING 列表中的列在某一侧 source 缺失；
- `IncompatibleKeyTypes(left_type, right_type)`：USING/NATURAL 共同列类型不可比较（由 type_system codec registry 校验）。

### 5.3 `_join_executor.execute_plan` 契约

```python
def execute_plan(self, plan: LogicalPlan, txn=None) -> tuple[list[tuple], tuple[str, ...]]:
    """Execute plan within active read txn; return (rows, output_schema)."""
```

- 走 `executor._txn_read_page` / `_txn_write_page` / WAL 缓冲（只读 JOIN 时仅读路径）。
- 输入 plan 已通过 `build_plan` 构造；executor 不再次解析名称。
- 输出 schema 与 `plan.output_schema` 一致；每行 tuple 长度严格 = `len(output_schema)`。
- LEFT/RIGHT/FULL 输出顺序按 strict-left-deep-insertion。
- 内部 helper：`_nested_loop_inner`、`_nested_loop_left`、`_nested_loop_full`、`_apply_using_coalesce`、`_apply_natural_keys`。

### 5.4 LogicalPlan 暴露给 CLI

```python
class Database:
    def explain_plan(self, sql: str) -> LogicalPlan:
        tokens = tokenize(sql)
        stmts = parse(tokens)
        last = stmts.statements[-1]
        if not isinstance(last, Select):
            raise ExecutionError("explain_plan: only SELECT is supported")
        catalog = self.catalog
        return build_plan(last, catalog)
```

`LogicalPlan.format()` 输出稳定的缩进文本（仅供调试 / `.explain` 打印）：
```
Project(label=u.id, expr=ColumnRef(u,id))
└─ Join(INNER, on=u.id=o.user_id)
   ├─ Scan(users AS u, schema=(id,name))
   └─ Scan(orders AS o, schema=(id,user_id,total))
```

`format()` 不做成本估算、不承诺固定行数。CLI 后续 change 决定是否增强。

### 5.5 Database.execute 行为变化

- 含 JOIN 的 `Select`：先 `build_plan` → `_join_executor.execute_plan` → 走统一 Filter/Aggregate/Sort/Project/Limit helper（与现有 `_exec_aggregate_select` 共享输出路径）。
- 结果 `list[Row]` 中，`Row.columns` 是 `plan.output_schema` 的拷贝；`Row.values` 是 plan 输出 tuple。
- `Database.execute` 自身不持有事务边界；JOIN 与现有路径一样由 `Executor._exec_in_txn` 提供事务包装。
- 异常传播：`ParseError` → `ResolutionError`/`ExecutionError` → `ConstraintViolation`（如果后续阶段触发）按原顺序向外抛。

### 5.6 Row 映射访问

- 在 `database.py` `Row` 中增加 `__getitem__`：`return self.values[self.columns.index(name)]`；列名不存在时 `KeyError`。
- 属性访问保持 `__getattr__` 现有行为（合法 Python 标识符可命中）。
- 单表 Row 行为不变（`columns` 是裸列名，`__getitem__("name")` 也能命中）。
- `repr` 仍输出 `Row(col=value, ...)`；JOIN 行亦同。

### 5.7 `__init__.py` 再导出

```python
from tinydb.errors import (
    TinydbError, TokenError, ParseError, ExecutionError,
    ResolutionError, AmbiguousColumn, DuplicateAlias,
    UnknownSource, UnknownQualifiedColumn, MissingUsingKey, IncompatibleKeyTypes,
    ConstraintViolation, PageFull, CatalogFull,
)
from tinydb.resolver import resolve, ResolvedPlan, ResolvedSource
from tinydb.plan import LogicalPlan, build_plan, Scan, Join, Filter, Aggregate, Sort, Project, Limit
from tinydb.parser import (
    TableRef, JoinClause, JoinKey, ColumnRef,
    # 既有 AST
    CreateTable, DropTable, Insert, Delete, Select, Update,
    EqualsExpr, AndExpr, OrExpr, NotExpr, OrderByItem,
    AggregateCall, SelectItem,
)
```

---

## 6. Risks / Trade-offs

| Risk | 触发条件 | 缓解 |
|------|----------|------|
| executor.py 继续膨胀 | JOIN 路径塞进主文件 | 三个独立模块（resolver / plan / _join_executor）；`executor.py` 仅 +30-50 行 |
| 外连接 NULL/合并键复杂 | LEFT/RIGHT/FULL/USING/NATURAL 组合 | resolver 阶段统一生成 JoinKey + output schema；矩阵测试覆盖 |
| RIGHT/FULL 未匹配行顺序 | 用户期望 vs nested-loop 物理顺序 | strict-left-deep-insertion 契约；property 测试断言 |
| 聚合 helper 只接裸列名 | JOIN 后聚合失败 | plan 阶段把所有引用解析为列位置/标签；plan 输出 schema 已是合并 schema |
| WHERE/HAVING 对外连接 NULL | 三值语义分歧 | 复用 v0.1 NULL 比较；新增左未匹配/右未匹配/全未匹配集成测试 |
| 单表回归 | parser/executor 改动影响既有路径 | 单表 fast path 早返回；保留 v0.1 全部单表测试 |
| API 标签破坏 | v0.1 用户假设裸列名 | 单表 Row 不变；JOIN Row 限定标签；python-api delta spec 显式记录 |
| plan 接口过早锁定 | 后续 hash/merge join 难以接入 | 节点只暴露逻辑信息；不暴露物理实现细节 |
| 物化宽行 OOM | 大结果集 | MVP_LIMITATIONS.md 声明；`max_join_rows` 作为后续 follow-up |
| 类型系统对 USING 列类型校验 | 与 v0.1 codec registry 协同 | `validate_compare_types` 复用；type 不兼容报 `IncompatibleKeyTypes` |
| WAL/ACID 绕过风险 | JOIN helper 直接走 `Pager.read_page` | 测试断言 JOIN 路径必走 `_txn_read_page`；ACID 回归用例覆盖 |

---

## 7. Spec Patch（回写到 delta spec）

设计阶段发现 OpenSpec delta spec 缺少以下边界场景；本节同时作为对 delta spec 的 patch 内容（运行时由 agent 写回三个 spec 文件）：

### 7.1 `openspec/changes/join-query/specs/sql-join-query/spec.md`

追加 requirement：

```markdown
### Requirement: Outer join output ordering is stable

`LEFT`, `RIGHT`, and `FULL JOIN` MUST emit rows in `strict-left-deep-insertion` order: matching combinations follow the left-deep nested-loop input order; `LEFT` unmatched rows immediately follow their left row; `RIGHT`/`FULL` unmatched right-side rows are appended after all matching rows in the right-side scan order.

#### Scenario: LEFT emits unmatched rows adjacent to their left row
- **WHEN** executing `SELECT u.id, o.id FROM users u LEFT JOIN orders o ON u.id = o.user_id`
- **AND** user `1` has no matching order
- **THEN** the result MUST contain a row with `u.id = 1` and `o.id = NULL`
- **AND** that row MUST appear immediately after the last matched row of user `1` (or first if no match).

#### Scenario: FULL preserves right unmatched in scan order
- **WHEN** executing `SELECT u.id, o.id FROM users u FULL JOIN orders o ON u.id = o.user_id`
- **AND** some orders reference users that do not exist
- **THEN** the result MUST contain a row for each unmatched right order
- **AND** those rows MUST appear after all matched combinations and left-unmatched rows
- **AND** the unmatched right rows MUST be in `orders` source scan order.

### Requirement: NATURAL JOIN with no common columns degrades to CROSS

When a `NATURAL JOIN` has no common column between the two sources, the join MUST behave as a `CROSS JOIN` (Cartesian product) without raising an error. The user-declared outer join kind (LEFT/RIGHT/FULL) still applies for NULL padding.

#### Scenario: NATURAL with empty common column set
- **WHEN** executing `SELECT * FROM users NATURAL LEFT JOIN audit`
- **AND** `users` and `audit` share no column name
- **THEN** the result MUST be the Cartesian product of the two inputs
- **AND** unmatched-side NULL padding MUST follow LEFT semantics.

### Requirement: USING and NATURAL merged keys use coalesce semantics

`USING (col, ...)` and `NATURAL` merged keys MUST emit a single output column whose value is taken from the left source first; if that value is `NULL`, the right source value is used; if both are `NULL`, the merged key is `NULL`. The merged key label MUST be the unqualified column name. Outer-join unmatched rows use `NULL` on the missing side.

#### Scenario: Coalesce chooses non-null side
- **WHEN** executing `SELECT * FROM users u LEFT JOIN profiles p USING (id)`
- **AND** a left row has `id = 1` with `NULL` profile value and the right row has `id = 1` with non-NULL value
- **THEN** the merged `id` column MUST equal the right source value.

#### Scenario: Both sides null yields null
- **WHEN** both left and right merged-key values are `NULL`
- **THEN** the merged key MUST be `NULL`.
```

### 7.2 `openspec/changes/join-query/specs/python-api/spec.md`

追加 requirement：

```markdown
### Requirement: ResolutionError is exposed and identifiable

`tinydb.ResolutionError` SHALL be importable from the top-level package and SHALL be a subclass of `tinydb.ExecutionError`. Specific name-resolution failures SHALL raise documented subtypes (e.g. `AmbiguousColumn`, `DuplicateAlias`, `UnknownSource`, `UnknownQualifiedColumn`, `MissingUsingKey`, `IncompatibleKeyTypes`).

#### Scenario: Ambiguous unqualified column raises ResolutionError
- **WHEN** executing `SELECT id FROM users u JOIN orders o`
- **THEN** the system SHALL raise `tinydb.AmbiguousColumn` (a `ResolutionError`) naming the column and the conflicting sources.

#### Scenario: Missing USING key raises ResolutionError
- **WHEN** executing `SELECT * FROM users u JOIN orders o USING (missing_col)`
- **THEN** the system SHALL raise `tinydb.MissingUsingKey` (a `ResolutionError`) identifying the missing column.

### Requirement: JOIN Row supports mapping-style access by qualified label

For JOIN results, `Row` MUST expose a `__getitem__` mapping by output-column label. Qualified labels such as `u.id` and merged USING/NATURAL labels such as `id` MUST be reachable through `row["u.id"]` and `row["id"]`. Attribute access SHALL remain available for labels that are valid Python identifiers and are not ambiguous.

#### Scenario: Mapping access by qualified label
- **WHEN** a JOIN result row has output columns `u.id` and `o.id`
- **THEN** `row["u.id"]` and `row["o.id"]` MUST return the corresponding values.

#### Scenario: Mapping access by merged key
- **WHEN** a JOIN result row has the merged USING/NATURAL key `id`
- **THEN** `row["id"]` MUST return the coalesced value
- **AND** the source-side qualified labels MUST NOT be reachable as separate mapping keys.
```

### 7.3 `openspec/changes/join-query/specs/sql-minimal-parser/spec.md`

追加 requirement：

```markdown
### Requirement: NATURAL JOIN automatically discovers common columns

The parser SHALL recognize `NATURAL [INNER|LEFT|RIGHT|FULL] JOIN` and emit a join clause marked as natural. The resolver SHALL compute the natural key set by intersecting the column names of the two sources in deterministic schema order.

#### Scenario: NATURAL JOIN emits natural marker
- **WHEN** parsing `SELECT * FROM users NATURAL LEFT JOIN profiles`
- **THEN** the AST MUST retain a natural join marker and the LEFT mode
- **AND** the resolver MUST compute the natural key set from the common column names of `users` and `profiles` in catalog schema order.
```

---

## 8. Test Plan Outline

- `tests/unit/test_tokenizer.py` 增：JOIN/INNER/LEFT/RIGHT/FULL/OUTER/CROSS/ON/USING/NATURAL 关键字、`.` 标点、非法连续 `.`。
- `tests/unit/test_parser.py` 增：表别名、JOIN 种类、ON 复杂表达式、USING/NATURAL、限定列、JOIN 缺 ON/USING 报错。
- `tests/unit/test_resolver.py`（新）：来源映射、合并 schema、JoinKey Coalesce、各 `ResolutionError` 子类型。
- `tests/unit/test_plan.py`（新）：plan 构造无副作用 property、节点字段、子节点顺序、strict-left-deep-insertion 形状。
- `tests/unit/test_join_executor.py`（新）：INNER/CROSS/LEFT/RIGHT/FULL/USING/NATURAL/Coalesce/空表/多级。
- `tests/integration/test_join_execution.py`（新）：与 Database.execute 集成，覆盖 ON/USING/NATURAL × 各 JOIN kind × NULL/无匹配。
- `tests/integration/test_join_post_phases.py`（新）：JOIN + WHERE / GROUP BY / HAVING / COUNT / SUM / ORDER BY / LIMIT / OFFSET。
- `tests/integration/test_join_row_api.py`（新）：限定列映射访问、合并键、`SELECT *`、迭代、`repr`、`==`。
- `tests/integration/test_explain_plan.py`（新）：`Database.explain_plan` 返回 plan、`pager.page_count` 不变、`wal.size` 不变。
- `tests/property/test_join_order.py`（新）：property 测试断言 LEFT/RIGHT/FULL 输出顺序 = strict-left-deep-insertion。
- `tests/e2e/test_join_queries.py`（新）：golden SQL 覆盖全矩阵。
- 回归：v0.1 单表 / aggregation / index / ACID / WAL 全部测试保持 pass。

---

## 9. Migration / Rollback

1. 工作区分支：`feature/20260723/join-query`（基于 `main@1ca8179`）；若与 `concurrency-control` / `cli-enhancement` 并行，按工作区隔离协议（worktree 或并行分支 + 集成分支）协调。
2. 落地顺序：
   - Tokenizer + AST 扩展；
   - Resolver + LogicalPlan（含 property 无副作用断言）；
   - `_join_executor` INNER/CROSS；
   - LEFT/RIGHT/FULL + USING/NATURAL + Coalesce；
   - JOIN × WHERE / 聚合 / 排序 / LIMIT；
   - Python API（`Row.__getitem__`、`explain_plan`、errors re-export）；
   - 验证 + 文档 + 验证报告。
3. 集成：与其他 v0.2 change 合并到 integration 分支后再发布。
4. 回滚：删除 `resolver.py` / `plan.py` / `_join_executor.py` 并还原 `executor.py` 与 `parser.py` 改动；保留 v0.1 单表 / aggregation / ACID 路径无影响。JOIN 入口在 `_exec_select` 内分支判定，删除即可。

---

## 10. Open Questions（已确认）

设计阶段 7 个澄清问题在 `brainstorm-summary.md` §"已确认的完整技术设计（A–H 节）"全部确认。本节保留为审计追踪，不阻塞 build。

- 多表外连接输出顺序 → strict-left-deep-insertion
- NATURAL 无共同列 → 退化为 CROSS JOIN
- USING/NATURAL 合并键取值 → Coalesce
- 名称解析错误类型 → `ResolutionError`（`ExecutionError` 子类）
- 实现架构 → 方案 B（resolver + plan + _join_executor）
- 内存策略 → 不加硬限 + MVP_LIMITATIONS 声明
- 设计方案 → 已定稿为本文档

---

## 11. Acceptance

- 所有 v0.1 测试在 `feature/20260723/join-query` 上保持 pass。
- 新模块（resolver / plan / _join_executor）单测覆盖率 ≥ 85%；整体覆盖率 ≥ 93%（v0.1 基线）。
- OpenSpec strict validation 通过：`openspec validate --strict` 全绿。
- `Database.explain_plan` 在 JOIN、单表、aggregation 上输出稳定 plan；property 测试断言无副作用。
- 完整矩阵（5 JOIN kind × {ON, USING, NATURAL, CROSS} × {空表 / 单匹配 / 多匹配 / 无匹配 / NULL} × 单级 / 多级）通过集成测试。
- 输出顺序的 property 测试对 100+ 随机生成的表/查询断言 strict-left-deep-insertion。
- 文档：`docs/MVP_LIMITATIONS.md` 增补 JOIN 内存限制；README / 操作手册增补 JOIN 用法章节。
- 验证报告：`docs/superpowers/reports/2026-07-23-join-query-verify.md` 记录基线 / 结果 / 偏差。

---

## 12. References

- OpenSpec delta specs：
  - `openspec/changes/join-query/proposal.md`
  - `openspec/changes/join-query/design.md`
  - `openspec/changes/join-query/tasks.md`
  - `openspec/changes/join-query/specs/sql-join-query/spec.md`
  - `openspec/changes/join-query/specs/sql-minimal-parser/spec.md`
  - `openspec/changes/join-query/specs/python-api/spec.md`
- v0.1 基线：`main@1ca8179`。
- 关联 change：`concurrency-control`（事务并发控制，独立 worktree）、`cli-enhancement`（消费本 change 的 LogicalPlan / explain_plan API）。
- 历史 design 模板参考：`docs/superpowers/specs/2026-07-19-tinydb-acid-design.md`、`docs/superpowers/specs/2026-07-21-type-codec-and-catalog-cleanup-design.md`。