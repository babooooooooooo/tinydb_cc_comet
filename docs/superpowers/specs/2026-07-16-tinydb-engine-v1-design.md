---
comet_change: tinydb-engine-v1
role: technical-design
canonical_spec: openspec
---

# Design Doc: tinydb-engine-v1

> **关联文档**：[proposal.md](../../openspec/changes/tinydb-engine-v1/proposal.md) · [open-stage design.md](../../openspec/changes/tinydb-engine-v1/design.md) · [tasks.md](../../openspec/changes/tinydb-engine-v1/tasks.md)
>
> 本文档是 `tinydb-engine-v1` 的 **实现级深化设计**。open-stage `design.md` 给出高层架构与 D1-D5 决策；本文档落到 AST dataclass 字段、tokenizer 关键字表、parser 语法 EBNF、executor 伪代码、SlottedPage 交互细节、错误处理矩阵、测试矩阵与回归策略。
>
> 范围：parser + executor + tokenizer 三文件改动；不动 storage / catalog / pager / row_codec / type_system。
>
> 行数预算：`parser.py ≤ 750`、`executor.py ≤ 580`（uplift from 520；see task 13 §13.4）、`tokenizer.py ≤ 200`。

---

## 1. 上下文与目标

### 1.1 上下文（摘要，详见 proposal.md）

`tinydb-mvp` 已经能跑通 `CREATE / DROP / INSERT / SELECT * / SELECT WHERE col = lit / DELETE`。MVP 后立刻遇到的三个真实需求：

1. **修改数据**：当前必须靠 `DELETE + INSERT`，PK 一旦引入即撞。
2. **复合过滤**：当前 WHERE 仅支持 `col = lit`；复合条件（AND/OR/NOT）只能拉到客户端过滤。
3. **结果排序与切片**：SELECT 返回顺序由页内 slot 顺序决定，无法"前 10 条""按时间倒序"。

### 1.2 目标（本 change 必交付）

- G1. `UPDATE <table> SET <col=lit>[, ...] WHERE <expr>` 端到端可用；受影响行数可观察（通过 stdout/e2e 计数）。
- G2. WHERE 复合条件 `AND / OR / NOT` 任意嵌套；右值 literal 类型严格；AND/OR 短路求值。
- G3. SELECT 末尾可选 `ORDER BY <col>[ASC|DESC][, ...] [LIMIT N] [OFFSET N]`，三者均缺省时与 MVP 行为等价。
- G4. 保持向后兼容：MVP 既有 234 个测试必须全部通过；现有 INSERT/SELECT/DELETE/CREATE/DROP 解析路径不破坏。

### 1.3 非目标（明确不做，详见 proposal.md "Out of Scope"）

- 列约束、聚合、索引、事务、扩展类型、JOIN/子查询/视图/触发器 → 留后续 5 个 change。
- SET 右值允许表达式（不仅 literal）→ 推迟到 `tinydb-engine-v2`。
- ALTER TABLE / ALTER COLUMN → 永久 out。

---

## 2. 架构总览

### 2.1 数据流

```
SQL string
    │
    ▼
tokenizer.tokenize()                     ← 新增 11 个关键字
    │
    ▼ list[Token]
parser.parse()                           ← 新增 Update AST + 表达式树
    │
    ▼ StatementList
Database.execute() → Executor.execute()  ← dispatcher 扩展
    │
    ▼ AST node
_exec_create_table / _exec_insert / _exec_select / _exec_delete / _exec_update   ← 新增
    │
    ▼ Pager / Catalog / SlottedPage
```

### 2.2 模块改动一览

| 文件 | 改动类型 | 新增/修改行数估算 | 关键 API |
|------|---------|-------------------|---------|
| `src/tinydb/tokenizer.py` | 扩展 | +~15 行 | `KEYWORDS` 集合追加 11 项；token 行为不变 |
| `src/tinydb/parser.py` | 扩展 | +~150 行 | 新增 5 个 AST dataclass + 4 个 `_parse_*` 方法 + `expr()` 入口；dispatcher 新增 UPDATE |
| `src/tinydb/executor.py` | 扩展 | +~120 行 | 新增 `eval_expr` / `_exec_update` / `_apply_order_limit`；dispatcher 扩展 |
| `src/tinydb/database.py` | 0 改动 | 0 | `Row` 不动；DML 返回 `[]` 协议不变 |
| `src/tinydb/repl.py` | 0 改动 | 0 | REPL 已能渲染 `(no rows)`；UPDATE 返回 `[]` 走 OK 路径 |
| `tests/unit/test_engine_v1_parser.py` | 新增 | ~250 行 | AST roundtrip、关键字、优先级 |
| `tests/unit/test_engine_v1_executor.py` | 新增 | ~300 行 | eval_expr 真值表、UPDATE in-place、sort 稳定 |
| `tests/integration/test_engine_v1.py` | 新增 | ~250 行 | 端到端 UPDATE、跨页、复合 WHERE |
| `tests/e2e/sql/engine_v1/*.sql` | 新增 | 12 个 golden | 每条 5-15 行 SQL + `.expected.txt` |

### 2.3 关键不变式（必须维持）

1. **Row 不变性**：`Database.execute` 返回 `list[Row]`，DDL/INSERT/DELETE/UPDATE 全部返回 `[]`。
2. **存储格式不变**：不修改 `SlottedPage` 页布局；复用 `SlottedPage.update / delete / insert` 原语。
3. **catalog 不变**：不引入新字段；`next_page_id` 推进逻辑与 INSERT 共用 `_insert_row_into_chain`。
4. **strict type**：WHERE / SET 右值 literal 必须与列类型严格匹配；类型错抛 `ExecutionError("type mismatch: X vs Y")`。
5. **短路求值**：AND 遇 False 即返回 False；OR 遇 True 即返回 True；eval 抛错只在必要分支执行。

---

## 3. AST 节点详细设计

### 3.1 现有节点（保留不变）

```python
@dataclass
class CreateTable:
    name: str
    columns: list                 # list[tuple[str, str]]  (col, type)
    line: int
    col: int

@dataclass
class DropTable:
    name: str
    line: int
    col: int

@dataclass
class Insert:
    table: str
    columns: list                 # list[str]
    values: list                  # list[list[Any]]
    line: int
    col: int

@dataclass
class Delete:
    table: str
    where: Optional[tuple]         # 当前 MVP: Optional[(col, op, lit)]
    line: int
    col: int
```

### 3.2 `Select` 节点升级（向后兼容需小心）

**现状**（MVP）：

```python
@dataclass
class Select:
    table: str
    columns: list                 # list[str]
    where: Optional[tuple]         # (col, op, lit)
    line: int
    col: int
```

**升级后**（engine-v1）：

```python
@dataclass(frozen=True)
class Select:
    table: str
    columns: tuple[str, ...]
    where: Optional[Expr] = None   # 类型从 tuple 升级为 Expr
    order_by: tuple[OrderByItem, ...] = ()
    limit: Optional[int] = None
    offset: Optional[int] = None
    line: int = 0                  # 新增默认参数，向后兼容
    col: int = 0
```

**关键决策**：

- `frozen=True`：与 `Row` 一致，避免 evaluator 期间被重写（D5）。
- 全部新字段带默认值，MVP 现有实例化 `Select(table=..., columns=..., where=...)` 不破坏。
- `columns` 从 `list` 升级为 `tuple`：与 `order_by` 元组风格一致，避免 list in-place mutate。
- `line / col` 设默认 0：MVP 现有 `_parse_select` 已经传 `kw.line, kw.col`，升级后仍工作。

**MVP 兼容测试**：现有 `test_parser.py` 中所有 `assert stmt.statements[0].where == ("col", "=", lit)` 类型断言需相应更新为 `EqualsExpr(...)`。这是破坏性改动，但**只破坏测试**，不破坏 production。tasks §2.2 包含"旧 WHERE tuple 表达式替换为 EqualsExpr"的迁移测试。

### 3.3 新增节点

```python
# ---- 表达式 AST（WHERE/SET 右值共用）----

@dataclass(frozen=True)
class EqualsExpr:
    """MVP 兼容：`col = literal`"""
    column: str
    value: Any                     # 解析后为 int/float/str/bool

@dataclass(frozen=True)
class AndExpr:
    """短路 AND：`left AND right`"""
    left: "Expr"
    right: "Expr"

@dataclass(frozen=True)
class OrExpr:
    """短路 OR：`left OR right`"""
    left: "Expr"
    right: "Expr"

@dataclass(frozen=True)
class NotExpr:
    """`NOT operand`"""
    operand: "Expr"

# 类型别名（仅文档性，运行时即 Union）
Expr = EqualsExpr | AndExpr | OrExpr | NotExpr


# ---- SELECT 子句 ----

@dataclass(frozen=True)
class OrderByItem:
    """ORDER BY 一项：列名 + ASC/DESC"""
    column: str
    descending: bool               # False = ASC, True = DESC


# ---- UPDATE 语句 ----

@dataclass(frozen=True)
class Update:
    """UPDATE <table> SET <col=lit>[, ...] WHERE <expr>"""
    table: str
    sets: tuple[tuple[str, Expr], ...]   # 顺序敏感；SET 右值必须是 EqualsExpr 或 literal
    where: Optional[Expr]
    line: int
    col: int
```

**关键决策**：

- `sets` 元素类型为 `(str, Expr)` 而非 `(str, Any)`：保留未来扩展表达式右值的空间（D7 推迟），但 build 阶段 executor 只接受 `EqualsExpr`。
- `where` 类型升级后 `Delete.where` 同样升级（保持 executor 端 `eval_expr` 入口统一）。
- 所有表达式节点 `frozen=True`：避免 evaluator 期间 mutate（D5 + 未来并发）。

### 3.4 type checker 视角的隐式约束

`mypy --strict` 视角下（虽然项目未必开 strict）：

- `EqualsExpr.value` 类型 `Any` 是必要妥协：literal token 类型在解析时已知（int/float/str/bool），但 dataclass 字段不支持 Union[primitive]，因此用 `Any` 并在 executor / py_to_db 阶段做类型校验。
- `Expr` 是运行期 Union，不参与 mypy narrowing；executor 必须 `isinstance` 分发。

---

## 4. Tokenizer 改动

### 4.1 关键字表

**现状**（`src/tinydb/tokenizer.py:13-16`）：

```python
KEYWORDS = {
    "CREATE", "TABLE", "DROP", "INSERT", "INTO", "VALUES", "SELECT",
    "FROM", "WHERE", "DELETE", "INT", "TEXT", "FLOAT", "BOOL",
}
```

**新增**（11 项）：

```python
KEYWORDS = {
    "CREATE", "TABLE", "DROP", "INSERT", "INTO", "VALUES", "SELECT",
    "FROM", "WHERE", "DELETE", "INT", "TEXT", "FLOAT", "BOOL",
    # --- engine-v1 新增 ---
    "UPDATE", "SET",
    "AND", "OR", "NOT",
    "ORDER", "BY",
    "ASC", "DESC",
    "LIMIT", "OFFSET",
}
```

**关键决策**：

- 大小写不敏感（`text.upper()` 已实现），与 MVP 一致。
- 不引入 `IN / LIKE / BETWEEN` 等保留字：本 change 范围外。
- `TRUE / FALSE` 仍然输出 BOOL literal token，不进入 KEYWORDS（保留 MVP 行为，tokenizer.py:18 注释明示）。

### 4.2 标识符冲突测试

```python
# tests/unit/test_engine_v1_tokenizer.py
def test_keyword_update_rejected_as_identifier():
    with pytest.raises(ParseError):
        parse(tokenize("CREATE TABLE update (id INT)"))  # 'update' 应是关键字

def test_identifier_order_lower_is_allowed():
    # 小写 'order' 不在 KEYWORDS，应作为 IDENT
    tokens = tokenize("CREATE TABLE t (order INT, id INT)")
    assert tokens[-2].type == "IDENT" and tokens[-2].value == "order"
```

**R4 缓解**：MVP `CREATE TABLE` 列名允许任何 IDENT 串；tokenizer 升级后 `update / set / and / or / not / order / by / asc / desc / limit / offset` 全部升级为 KEYWORD，**列名含这些字面量（任意大小写匹配）的 CREATE TABLE 必须报错**。Build 阶段在 `_parse_create_table` 列名接受处增加 `if up in NEW_KEYWORDS: raise ParseError(...)`。

### 4.3 tokenizer 输出格式不变

token 的 `type/value/line/col` 四个字段都不变；下游 parser 与现有测试无需迁移。

---

## 5. Parser 语法详细设计

### 5.1 EBNF

```ebnf
(* 顶层 *)
program        = statement (";" statement)* ";"? EOF
statement      = create_table | drop_table | insert | select | delete | update

(* DDL 不变 *)
create_table   = "CREATE" "TABLE" IDENT "(" col_def ("," col_def)* ")"
col_def        = IDENT type_name
type_name      = "INT" | "TEXT" | "FLOAT" | "BOOL"

drop_table     = "DROP" "TABLE" IDENT

(* DML 不变 *)
insert         = "INSERT" "INTO" IDENT "(" col_list ")" "VALUES" value_list
select         = "SELECT" projection "FROM" IDENT [where_clause] [order_clause] [limit_clause] [offset_clause]
projection     = "*" | col_list
delete         = "DELETE" "FROM" IDENT [where_clause]

(* 新增 DML *)
update         = "UPDATE" IDENT "SET" assign ("," assign)* [where_clause]
assign         = IDENT "=" literal                        (* literal 不是 Expr，本 change 不允许表达式右值 *)

(* 表达式：WHERE / SET 右值共用 *)
expr           = or_expr
or_expr        = and_expr ("OR" and_expr)*               (* OR 优先级最低 *)
and_expr       = not_expr ("AND" not_expr)*              (* AND 优先级次之 *)
not_expr       = "NOT" not_expr | primary                (* NOT 优先级高于 AND/OR *)
primary        = "(" expr ")" | comparison
comparison     = IDENT "=" literal                       (* MVP 唯一比较；后续 change 扩 < > != *)

(* 子句 *)
where_clause   = "WHERE" expr
order_clause   = "ORDER" "BY" order_item ("," order_item)*
order_item     = IDENT ["ASC" | "DESC"]                  (* 默认 ASC *)
limit_clause   = "LIMIT" INT                             (* 必须非负整数 *)
offset_clause  = "OFFSET" INT                            (* 必须非负整数 *)
```

### 5.2 优先级表

| 优先级（高 → 低） | 算子 | 例子 | 说明 |
|-----------------|------|------|------|
| 1（最高） | `()`、`NOT` | `(a=1 OR b=2)`、`NOT a=1` | 显式括号；一元 NOT |
| 2 | `=` | `a = 1` | MVP 唯一比较 |
| 3 | `AND` | `a=1 AND b=2` | 左结合 |
| 4（最低） | `OR` | `a=1 OR b=2` | 左结合 |

**关键决策**：

- OR/AND/NOT 三层显式，而非 Pratt parser：实现简单，每层一个 `_parse_or_expr / _parse_and_expr / _parse_not_expr` 函数，递归即可。
- NOT 一元算子，无右结合：与 SQL 标准一致（`NOT NOT a=1` 合法）。
- `=` 仅出现在 `primary` 之后；MVP 路径 `_parse_where` 重构为 `_parse_expr()`。

### 5.3 parser 代码骨架

```python
# 在 _Parser 内新增

def _parse_expr(self) -> Expr:
    return self._parse_or_expr()

def _parse_or_expr(self) -> Expr:
    left = self._parse_and_expr()
    while self._peek_kw("OR"):
        self.advance()
        right = self._parse_and_expr()
        left = OrExpr(left=left, right=right)
    return left

def _parse_and_expr(self) -> Expr:
    left = self._parse_not_expr()
    while self._peek_kw("AND"):
        self.advance()
        right = self._parse_not_expr()
        left = AndExpr(left=left, right=right)
    return left

def _parse_not_expr(self) -> Expr:
    if self._peek_kw("NOT"):
        self.advance()
        return NotExpr(operand=self._parse_not_expr())  # 右结合
    return self._parse_primary()

def _parse_primary(self) -> Expr:
    if self._peek_punct("("):
        self.advance()
        inner = self._parse_expr()
        self.expect("PUNCT", ")")
        return inner
    return self._parse_comparison()  # 现状 IDENT = literal

def _peek_kw(self, kw: str) -> bool:
    t = self.peek()
    return t.type == "KEYWORD" and t.value == kw

def _peek_punct(self, p: str) -> bool:
    t = self.peek()
    return t.type == "PUNCT" and t.value == p
```

### 5.4 SELECT 子句扩展

`_parse_select` 升级后骨架：

```python
def _parse_select(self) -> Select:
    kw = self.expect_keyword("SELECT")
    cols = self._parse_projection()
    self.expect_keyword("FROM")
    table_t = self.peek()
    if table_t.type != "IDENT":
        raise ParseError(table_t.line, table_t.col, "expected table name")
    table = self.advance().value

    where = self._parse_where()       # 已重构为返回 Expr
    order_by = self._parse_order_by() # 新增；返回 tuple[OrderByItem, ...]
    limit = self._parse_limit()       # 新增；返回 int | None
    offset = self._parse_offset()     # 新增；返回 int | None

    return Select(
        table=table, columns=tuple(cols), where=where,
        order_by=order_by, limit=limit, offset=offset,
        line=kw.line, col=kw.col,
    )
```

`_parse_order_by` 关键点：

```python
def _parse_order_by(self) -> tuple[OrderByItem, ...]:
    if not self._peek_kw("ORDER"):
        return ()
    self.advance()
    self.expect_keyword("BY")
    items = []
    while True:
        ct = self.peek()
        if ct.type != "IDENT":
            raise ParseError(ct.line, ct.col, "expected column in ORDER BY")
        col = self.advance().value
        desc = False
        if self._peek_kw("ASC"):
            self.advance()
        elif self._peek_kw("DESC"):
            self.advance()
            desc = True
        items.append(OrderByItem(column=col, descending=desc))
        if self._peek_punct(","):
            self.advance()
            continue
        break
    return tuple(items)
```

### 5.5 UPDATE 解析

```python
def _parse_update(self) -> Update:
    kw = self.expect_keyword("UPDATE")
    tt = self.peek()
    if tt.type != "IDENT":
        raise ParseError(tt.line, tt.col, "expected table name")
    table = self.advance().value
    self.expect_keyword("SET")

    sets: list[tuple[str, Expr]] = []
    while True:
        ct = self.peek()
        if ct.type != "IDENT":
            raise ParseError(ct.line, ct.col, "expected column name in SET")
        col = self.advance().value
        self.expect("PUNCT", "=")
        # SET 右值必须 literal（不允许表达式）
        lit_tok = self.advance()
        if lit_tok.type not in _LITERAL_TYPES:
            raise ParseError(lit_tok.line, lit_tok.col,
                             "SET right-hand side must be a literal")
        sets.append((col, EqualsExpr(column=col, value=lit_tok.value)))
        if self._peek_punct(","):
            self.advance()
            continue
        break

    if not sets:
        raise ParseError(kw.line, kw.col, "UPDATE requires at least one SET assignment")

    where = self._parse_where()  # 复用
    return Update(table=table, sets=tuple(sets), where=where,
                  line=kw.line, col=kw.col)
```

### 5.6 dispatcher 升级

```python
def parse_statement(self) -> Any:
    t = self.peek()
    if t.type != "KEYWORD":
        raise ParseError(t.line, t.col, f"expected statement, got {t.type}")
    kw = t.value
    if kw == "CREATE": return self._parse_create_table()
    if kw == "DROP":   return self._parse_drop_table()
    if kw == "INSERT": return self._parse_insert()
    if kw == "SELECT": return self._parse_select()
    if kw == "DELETE": return self._parse_delete()
    if kw == "UPDATE": return self._parse_update()      # 新增
    raise ParseError(t.line, t.col, f"unexpected keyword {kw}")
```

### 5.7 错误处理矩阵

| 输入 | 期望错误 | 错误位置 |
|------|---------|---------|
| `SELECT * FROM t WHERE OR a=1` | `ParseError: expected column in WHERE` | OR 关键字处 |
| `SELECT * FROM t WHERE a=1 OR` | `ParseError: expected expression` | EOF 处 |
| `SELECT * FROM t WHERE NOT` | `ParseError: expected expression` | EOF 处 |
| `SELECT * FROM t WHERE (a=1` | `ParseError: expected ')'` | EOF 处 |
| `SELECT * FROM t ORDER BY` | `ParseError: expected column in ORDER BY` | BY 后 EOF |
| `SELECT * FROM t ORDER BY a ASC EXTRA` | `ParseError: expected end of statement` | EXTRA 标识符 |
| `SELECT * FROM t LIMIT -1` | token 阶段先识别为 INT 负数？测试覆盖 | tokenizer 已识别；executor 校验非负 |
| `UPDATE t SET =1 WHERE a=1` | `ParseError: expected column name in SET` | `=` 处 |
| `UPDATE t SET a=1 a=2` | `ParseError: expected ',' or end of SET` | 第二个 `a` 处 |
| `UPDATE` | `ParseError: expected table name` | EOF 处 |

---

## 6. Executor 改动详细设计

### 6.1 表达式求值器 `eval_expr`

```python
def eval_expr(expr: Expr, row: list[Any], schema: list[tuple[str, str]]) -> bool:
    """递归评估 WHERE 表达式；AND/OR 短路；strict type 校验。

    Raises:
        ExecutionError: 列不存在、列类型与 literal 不匹配。
    """
    if isinstance(expr, EqualsExpr):
        col_idx = next(
            (i for i, (n, _) in enumerate(schema) if n == expr.column),
            None,
        )
        if col_idx is None:
            raise ExecutionError(f"unknown column {expr.column!r}")
        col_type = schema[col_idx][1]
        lit_type = _python_type_to_db_type(expr.value)
        if col_type != lit_type:
            raise ExecutionError(
                f"type mismatch: column {expr.column!r} {col_type} vs literal {lit_type}"
            )
        return row[col_idx] == expr.value

    if isinstance(expr, AndExpr):
        # 短路：left 为 False 直接返回；右侧含副作用才评估（目前无副作用）
        return eval_expr(expr.left, row, schema) and eval_expr(expr.right, row, schema)

    if isinstance(expr, OrExpr):
        return eval_expr(expr.left, row, schema) or eval_expr(expr.right, row, schema)

    if isinstance(expr, NotExpr):
        return not eval_expr(expr.operand, row, schema)

    raise ExecutionError(f"unsupported expression: {type(expr).__name__}")
```

**关键决策**：

- Python `and / or` 已短路；无需手写 if-else 提前返回。
- `EqualsExpr` 内部类型校验；AND/OR/NOT 不重复校验——子节点求值失败会冒泡。
- 无列类型推断 / 隐式转换（D3 strict type）。

### 6.2 UPDATE 实现 `_exec_update`

```python
def _exec_update(self, stmt: Update) -> list:
    """UPDATE <table> SET ... WHERE <expr>。返回 []（DML 协议）。
    
    路径：scan → filter by eval_expr → apply sets → encode →
          try SlottedPage.update in-place → fallback delete + insert。
    """
    ti = self.catalog.get_table(stmt.table)
    if ti is None:
        raise ExecutionError(f"table {stmt.table!r} does not exist")
    schema = ti.schema

    # 1) 校验 SET 列存在 + 右值类型匹配
    col_name_to_idx = {n: i for i, (n, _) in enumerate(schema)}
    for col_name, expr in stmt.sets:
        if col_name not in col_name_to_idx:
            raise ExecutionError(f"unknown column {col_name!r}")
        if not isinstance(expr, EqualsExpr):
            raise ExecutionError("SET right-hand side must be a literal")
        col_type = schema[col_name_to_idx[col_name]][1]
        lit_type = _python_type_to_db_type(expr.value)
        if col_type != lit_type:
            raise ExecutionError(
                f"type mismatch: column {col_name!r} {col_type} vs literal {lit_type}"
            )

    # 2) Scan + 收集 (page_id, slot_id, decoded_values) 匹配行
    matches: list[tuple[int, int, list[Any]]] = []
    for sid, vals, pid in self._scan_table(ti):
        if stmt.where is None or eval_expr(stmt.where, vals, schema):
            matches.append((pid, sid, vals))

    # 3) 按 page 分组，避免同一页扫描期间被修改
    by_page: dict[int, list[tuple[int, list[Any]]]] = {}
    for pid, sid, vals in matches:
        by_page.setdefault(pid, []).append((sid, vals))

    # 4) 逐页 in-place 更新；变长时 fallback
    for pid, sid_vals_list in by_page.items():
        raw = self.pager.read_page(pid)
        page = SlottedPage.from_bytes(pid, raw)
        for sid, vals in sid_vals_list:
            new_vals = list(vals)
            for col_name, expr in stmt.sets:
                new_vals[col_name_to_idx[col_name]] = expr.value
            new_bytes = encode_row(new_vals, schema)

            old_slot = page.slots[sid]
            if len(new_bytes) <= old_slot.length:
                try:
                    page.update(sid, new_bytes)
                    continue
                except PageFull:
                    pass
            # fallback：变长或 in-place 失败 → delete + insert
            # 同一扫描内：先 delete 原 slot，再 insert 新 row
            if old_slot.flags & FLAG_SPILL_START:
                self._free_overflow_chain(pid)
            page.delete(sid)
            try:
                page.insert(new_bytes)
            except PageFull:
                # 当前页无法容纳新 row：调用 _insert_row_into_chain 走 chain
                # 注意：page 已经 delete 了原 slot，必须先 flush 再 chain insert
                self.pager.write_page(pid, page.to_bytes())
                self.pager.flush()
                self._insert_row_into_chain(ti, new_bytes)
                # 此后 page 不再被本批次其他 sid_vals 引用（同一 page 的 sid_vals
                # 还在循环，但 page 已经被外部替换）。重新读 page 实例不安全
                # —— 因此改为：先记录 fallbacks，最后统一 flush。
                # 实际实现见 §6.3 改进版。
        self.pager.write_page(pid, page.to_bytes())
    self.pager.flush()
    return []
```

### 6.3 UPDATE 实现 v2（处理 chain fallback 的正确性）

上述 §6.2 在 fallback 到 `_insert_row_into_chain` 时与同 page 的其他 sid_vals 存在竞态。改进：

```python
def _exec_update(self, stmt: Update) -> list:
    ti = self.catalog.get_table(stmt.table)
    if ti is None:
        raise ExecutionError(f"table {stmt.table!r} does not exist")
    schema = ti.schema
    self._validate_update_sets(stmt, schema)   # §6.2 步骤 1 抽出

    # 收集 matches
    matches: list[tuple[int, int, list[Any]]] = []
    for sid, vals, pid in self._scan_table(ti):
        if stmt.where is None or eval_expr(stmt.where, vals, schema):
            matches.append((pid, sid, vals))

    # 按 page 分组；逐页独立处理
    by_page: dict[int, list[tuple[int, list[Any]]]] = {}
    for pid, sid, vals in matches:
        by_page.setdefault(pid, []).append((sid, vals))

    for pid, sid_vals_list in by_page.items():
        page = SlottedPage.from_bytes(pid, self.pager.read_page(pid))
        pending_chain_inserts: list[bytes] = []  # 变长的 row 在本批次 chain insert
        for sid, vals in sid_vals_list:
            new_vals = list(vals)
            for col_name, expr in stmt.sets:
                new_vals[self._col_idx(schema, col_name)] = expr.value
            new_bytes = encode_row(new_vals, schema)

            old_slot = page.slots[sid]
            grew = len(new_bytes) > old_slot.length
            if not grew:
                try:
                    page.update(sid, new_bytes)
                    continue
                except PageFull:
                    grew = True
            # grew == True：原 slot delete + 新 row insert
            if old_slot.flags & FLAG_SPILL_START:
                self._free_overflow_chain(pid)
            page.delete(sid)
            # 优先本页 insert；本页满则排队到 chain
            try:
                page.insert(new_bytes)
            except PageFull:
                pending_chain_inserts.append(new_bytes)

        # 先 flush 当前 page（delete + 部分 insert 已落地）
        self.pager.write_page(pid, page.to_bytes())
        # 再处理 chain insert（可能推进 next_page_id，需 catalog flush）
        for new_bytes in pending_chain_inserts:
            self._insert_row_into_chain(ti, new_bytes)

    self.pager.flush()
    return []
```

**关键决策**：

- 单 page 内 mutating 期间不重读 page；fallback 推迟到 `pending_chain_inserts` 集中处理。
- `_free_overflow_chain` 必须在 `page.delete(sid)` 之前调用：溢出页指针存在 data page 的 slot 中，delete 后读 slot.flags 仍可（已 tombstone，但 flags 字段保留），但代码可读性更好是先 free 再 delete。
- `page.update(sid, ...)` 在 `len(new_bytes) == old_slot.length` 时一定成功（直接复用同长度尾空间）；在 `len(new_bytes) < old_slot.length` 时成功但浪费空间（D2 决策接受，compaction 推迟）。
- `page.update` 抛 `PageFull` 实际上不会发生（in-place 写永远只追加），但防御性捕获——理论上 `update` 不增加 free_space，理论不会 PageFull。**测试覆盖**：test_executor_update_in_place_shrink_no_pagefull。

### 6.4 SELECT chain 实现

`_exec_select` 升级：

```python
def _exec_select(self, stmt: Select) -> list[list[Any]]:
    ti = self.catalog.get_table(stmt.table)
    if ti is None:
        raise ExecutionError(f"table {stmt.table!r} does not exist")
    schema = ti.schema

    # Named-column projection validation
    proj_idx: list[int] = []
    if stmt.columns != ("*",):
        name_to_idx = {n: i for i, (n, _) in enumerate(schema)}
        for cname in stmt.columns:
            if cname not in name_to_idx:
                raise ExecutionError(f"unknown column {cname!r}")
            proj_idx.append(name_to_idx[cname])

    # WHERE + SELECT 链顺序：filter → order → offset → limit
    rows: list[tuple[int, list[Any], int]] = []   # (slot_id, vals, pid) for stable sort
    for sid, vals, pid in self._scan_table(ti):
        if stmt.where is None or eval_expr(stmt.where, vals, schema):
            rows.append((sid, vals, pid))

    if stmt.order_by:
        rows = self._stable_sort(rows, stmt.order_by, schema)

    if stmt.offset:
        rows = rows[stmt.offset:]
    if stmt.limit is not None:
        rows = rows[:stmt.limit]

    # Project
    out: list[list[Any]] = []
    for _sid, vals, _pid in rows:
        if stmt.columns == ("*",):
            out.append(list(vals))
        else:
            out.append([vals[i] for i in proj_idx])
    return out

def _stable_sort(
    rows: list[tuple[int, list[Any], int]],
    items: tuple[OrderByItem, ...],
    schema: list[tuple[str, str]],
) -> list[tuple[int, list[Any], int]]:
    """Python stable sort by (value, slot_id); DESC 通过取负或反转。"""
    name_to_idx = {n: i for i, (n, _) in enumerate(schema)}

    def key(row):
        sid, vals, _pid = row
        parts = []
        for it in items:
            col_idx = name_to_idx.get(it.column)
            if col_idx is None:
                raise ExecutionError(f"unknown column {it.column!r} in ORDER BY")
            v = vals[col_idx]
            # type check
            col_type = schema[col_idx][1]
            try:
                py_to_db(v, col_type)
            except (TypeError, ValueError) as e:
                raise ExecutionError(f"column {it.column!r}: {e}") from e
            # ASC: (0, v, sid); DESC: (1, NEG v or REVERSE v, sid)
            if it.descending:
                parts.append((1, _reverse_key(v), sid))
            else:
                parts.append((0, v, sid))
        return tuple(parts)
    return sorted(rows, key=key)


# 类型安全的反转 key
def _reverse_key(v: Any) -> Any:
    if isinstance(v, bool):
        return not v
    if isinstance(v, (int, float)):
        return -v
    if isinstance(v, str):
        # 字符串 DESC 用 sorted reverse；保留 v 自身，sort flag 放在前面即可
        return v   # 因 sort flag 已是 (1, ...)，desc 时整体下沉，无需再反转字符串
    raise ExecutionError(f"unsupported DESC type: {type(v).__name__}")
```

**关键决策**：

- **稳定排序保证**：Python `sorted` 已稳定；次键用 `(0/1, value, slot_id)`，slot_id 保证主键相等时插入序保留（D5）。
- **DESC 实现**：用 sort flag `(0 vs 1)` 分组 + 数值取负；字符串 DESC 走 sort flag 区分即可（DESC 字符串排首，ASC 字符串排尾；sort flag 保证分组正确，再 stable 排序）。
- **类型校验**：sort 前 `py_to_db(v, col_type)` 失败抛 ExecutionError；保证参与比较的 v 已是 db 类型。
- **复杂度**：n log n，n ≤ 10k 行可接受（D4）；索引推迟到 `tinydb-engine-v2`。

### 6.5 LIMIT / OFFSET 校验

```python
def _exec_select(self, stmt: Select) -> list[list[Any]]:
    ...
    if stmt.offset is not None and stmt.offset < 0:
        raise ExecutionError(f"OFFSET must be non-negative, got {stmt.offset}")
    if stmt.limit is not None and stmt.limit < 0:
        raise ExecutionError(f"LIMIT must be non-negative, got {stmt.limit}")
    ...
```

**边界**：

- LIMIT 0：返回 `[]`（slice[:0]）。
- OFFSET > rows：返回 `[]`（slice[OFFSET:] 自然空）。
- OFFSET == rows：返回 `[]`。
- LIMIT > rows：返回全部剩余行（slice[:LIMIT] 自然短）。

### 6.6 executor dispatcher 升级

```python
def execute(
    self, stmt: Union[CreateTable, DropTable, Insert, Select, Delete, Update],
) -> Union[list, list[list[Any]]]:
    dispatch = {
        CreateTable: self._exec_create_table,
        DropTable:   self._exec_drop_table,
        Insert:      self._exec_insert,
        Select:      self._exec_select,
        Delete:      self._exec_delete,
        Update:      self._exec_update,    # 新增
    }
    handler = dispatch.get(type(stmt))
    if handler is None:
        raise ExecutionError(f"unsupported statement: {type(stmt).__name__}")
    return handler(stmt)
```

### 6.7 SELECT 中 `columns == ["*"]` vs `columns == ("*",)`

升级后 `columns` 是 `tuple`；现有 executor `_exec_select` 与 `database.py` 中的 `if stmt.columns == ["*"]` 必须改为 `if stmt.columns == ("*",)`。这是 build 阶段的统一修复点。

### 6.8 错误处理矩阵（executor 层）

| 输入 | 期望错误 | 类型 |
|------|---------|------|
| `UPDATE no_such_table SET a=1` | `unknown table 'no_such_table'` | ExecutionError |
| `UPDATE t SET x=1`（x 不在 schema） | `unknown column 'x'` | ExecutionError |
| `UPDATE t SET a='x'`（a 是 INT） | `type mismatch: column 'a' INT vs literal TEXT` | ExecutionError |
| `UPDATE t SET a=1 WHERE b=2`（b 不在 schema） | `unknown column 'b'` | ExecutionError |
| `UPDATE t SET a=1 WHERE a=2 OR b='x'`（b 是 INT） | `type mismatch: column 'b' INT vs literal TEXT` | ExecutionError |
| `SELECT * FROM t ORDER BY x`（x 不在 schema） | `unknown column 'x' in ORDER BY` | ExecutionError |
| `SELECT * FROM t LIMIT -1` | `OFFSET/LIMIT must be non-negative` | ExecutionError |
| `SELECT * FROM t ORDER BY a ASC DESC` | parser 拒绝（DESC 后不能再 ASC） | ParseError |

---

## 7. Storage 交互

### 7.1 复用的 SlottedPage 原语

| 原语 | 用途 | 行 |
|------|------|-----|
| `SlottedPage.update(sid, bytes)` | in-place UPDATE（等长或变短） | slotted_page.py:177 |
| `SlottedPage.delete(sid)` | UPDATE fallback 第一步 | slotted_page.py:169 |
| `SlottedPage.insert(bytes)` | UPDATE fallback 第二步；同 page 可容纳时 | slotted_page.py:132 |
| `SlottedPage.from_bytes / to_bytes` | 已有；UPDATE 路径读 / 写复用 | slotted_page.py:80, 102 |
| `FLAG_SPILL_START / FLAG_TOMBSTONE` | overflow 链管理；UPDATE 前先 `_free_overflow_chain` | slotted_page.py:25-26 |

**未复用的原语**：无。`SlottedPage.compact` / `SlottedPage.split` 不在本 change 范围（compaction 留 `tinydb-engine-v2`）。

### 7.2 PageFull 触发条件

`SlottedPage.update(sid, new_bytes)` 抛 `PageFull` 的条件（slotted_page.py:188）：

```python
if len(row_bytes) > s.length:
    raise PageFull(...)
```

即 `new_bytes` 比当前 slot 容量大。**实际不会抛 PageFull**，因为 update 是 append-only，不增加 free_space。但防御性 catch 是好习惯（executor §6.3）。

### 7.3 写时序

```
单 page 内：
  for sid, vals in sid_vals_list:
      page.update(sid, new_bytes)   # append 到 data area
      # 或 grew == True：
      page.delete(sid)              # 标 tombstone，slot offset = 0xFFFF
      page.insert(new_bytes)        # append 到 data area，新 slot
  pager.write_page(pid, page.to_bytes())
  pager.flush()

跨 page：
  grew == True 且本 page 满：
    self.pager.write_page(pid, page.to_bytes())   # 先 flush delete
    self._insert_row_into_chain(ti, new_bytes)    # 走 chain（可能推进 next_page_id）
```

**关键**：fallback 时**先 flush page 再 chain insert**。chain insert 可能修改 catalog（推进 next_page_id），触发 catalog flush。两阶段 flush 之间不存在 page 状态不一致——MVP 不引入事务，crash 半成品可接受（D2 + R2）。

### 7.4 catalog flush 时机

`_insert_row_into_chain` 内部：

```python
if pid == ti.next_page_id:
    new_pid = self.pager.alloc_page()
    ti.next_page_id = new_pid
    self.pager.write_page(1, self.catalog.to_bytes())
    self.pager.flush()
```

UPDATE 路径复用这段代码；catalog flush 仍由 INSERT 路径统一负责，UPDATE 不直接动 catalog。

---

## 8. 测试矩阵

### 8.1 单元测试 `tests/unit/test_engine_v1_parser.py`（~250 行）

| Test ID | 名称 | 覆盖 |
|---------|------|------|
| U-PAR-01 | `test_parse_and_or_not_associativity` | `a=1 AND b=2 OR c=3` 解析为 `Or(And(EQ a 1, EQ b 2), EQ c 3)` |
| U-PAR-02 | `test_parse_not_with_parens` | `NOT (a=1 OR b=2)` 解析正确 |
| U-PAR-03 | `test_parse_and_or_precedence` | `a=1 OR b=2 AND c=3` 解析为 `Or(EQ a 1, And(EQ b 2, EQ c 3))` |
| U-PAR-04 | `test_parse_not_right_associative` | `NOT NOT a=1` 解析为 `Not(Not(EQ a 1))` |
| U-PAR-05 | `test_parse_where_unterminated_or` | `WHERE a=1 OR` → ParseError |
| U-PAR-06 | `test_parse_where_unterminated_not` | `WHERE NOT` → ParseError |
| U-PAR-07 | `test_parse_where_missing_rparen` | `WHERE (a=1` → ParseError "expected )" |
| U-PAR-08 | `test_parse_update_basic` | `UPDATE t SET a=1 WHERE b=2` AST shape |
| U-PAR-09 | `test_parse_update_multi_set` | `UPDATE t SET a=1, b=2` 多列 |
| U-PAR-10 | `test_parse_update_no_set_raises` | `UPDATE t` → ParseError |
| U-PAR-11 | `test_parse_update_set_rhs_expr_raises` | `UPDATE t SET a=a+1` → ParseError |
| U-PAR-12 | `test_parse_select_order_by_asc` | 默认 ASC |
| U-PAR-13 | `test_parse_select_order_by_desc` | `ORDER BY a DESC` |
| U-PAR-14 | `test_parse_select_order_by_multi_key` | `ORDER BY a ASC, b DESC` |
| U-PAR-15 | `test_parse_select_limit_offset` | `LIMIT 10 OFFSET 5` |
| U-PAR-16 | `test_parse_select_limit_only` | `LIMIT 10` |
| U-PAR-17 | `test_parse_select_offset_only` | `OFFSET 5`（用户允许） |
| U-PAR-18 | `test_parse_select_order_limit_offset_chain` | 全链 |
| U-PAR-19 | `test_parse_keyword_update_as_table_name_raises` | `CREATE TABLE update (id INT)` 拒绝 |
| U-PAR-20 | `test_parse_keyword_set_as_column_name_raises` | `CREATE TABLE t (set INT)` 拒绝 |
| U-PAR-21 | `test_parse_keyword_lowercase_order_as_column_ok` | 小写 order 列名仍 OK |
| U-PAR-22 | `test_parse_select_no_clause_unchanged` | MVP 路径不变 |

### 8.2 单元测试 `tests/unit/test_engine_v1_executor.py`（~300 行）

| Test ID | 名称 | 覆盖 |
|---------|------|------|
| U-EXE-01 | `test_eval_expr_equals_basic` | EQ 求值 |
| U-EXE-02 | `test_eval_expr_and_short_circuits_left_false` | `False AND (raise)` 不 raise |
| U-EXE-03 | `test_eval_expr_or_short_circuits_left_true` | `True OR (raise)` 不 raise |
| U-EXE-04 | `test_eval_expr_not_negates` | `NOT (a=1)` |
| U-EXE-05 | `test_eval_expr_unknown_column_raises` | ExecutionError |
| U-EXE-06 | `test_eval_expr_type_mismatch_raises` | ExecutionError "type mismatch" |
| U-EXE-07 | `test_eval_expr_nested_and_or_not` | `a=1 AND NOT (b=2 OR c=3)` |
| U-EXE-08 | `test_executor_update_in_place_no_grow` | 等长 UPDATE；验证 page.slots[sid].length 不变 |
| U-EXE-09 | `test_executor_update_in_place_shrink` | 缩短 UPDATE；in-place append |
| U-EXE-10 | `test_executor_update_grow_in_same_page` | 同页内 grow：delete + insert |
| U-EXE-11 | `test_executor_update_grow_chain_page` | 跨页 grow：delete + _insert_row_into_chain |
| U-EXE-12 | `test_executor_update_no_where_updates_all` | 无 WHERE 全表 UPDATE |
| U-EXE-13 | `test_executor_update_compound_where` | `WHERE a=1 AND b=2` 仅更新复合匹配 |
| U-EXE-14 | `test_executor_update_set_unknown_column_raises` | ExecutionError |
| U-EXE-15 | `test_executor_update_set_type_mismatch_raises` | ExecutionError |
| U-EXE-16 | `test_executor_select_sorts_and_slices` | ORDER BY + LIMIT + OFFSET |
| U-EXE-17 | `test_executor_select_order_by_stable_when_tied` | 主键相等时按 slot_id 稳定 |
| U-EXE-18 | `test_executor_select_order_by_desc` | DESC 单列 |
| U-EXE-19 | `test_executor_select_order_by_multi_key` | `ORDER BY a ASC, b DESC` |
| U-EXE-20 | `test_executor_select_limit_zero` | LIMIT 0 返回 `[]` |
| U-EXE-21 | `test_executor_select_offset_beyond_rows` | OFFSET > rows 返回 `[]` |
| U-EXE-22 | `test_executor_select_order_then_offset_then_limit` | 链顺序正确 |
| U-EXE-23 | `test_executor_select_offset_negative_raises` | ExecutionError |
| U-EXE-24 | `test_executor_select_order_by_unknown_column_raises` | ExecutionError |
| U-EXE-25 | `test_executor_select_order_by_type_invalid_raises` | FLOAT inf/NaN 不存在（MVP 拒绝） |

### 8.3 集成测试 `tests/integration/test_engine_v1.py`（~250 行）

| Test ID | 名称 | 覆盖 |
|---------|------|------|
| I-V1-01 | `test_update_end_to_end` | CREATE + INSERT + UPDATE + SELECT 端到端 |
| I-V1-02 | `test_update_persists_after_reopen` | UPDATE → close → reopen → SELECT 验证 |
| I-V1-03 | `test_update_compound_where_multi_page` | 数据多页；UPDATE 跨 page |
| I-V1-04 | `test_update_grow_falls_back_to_chain` | grow UPDATE 走 chain；catalog next_page_id 推进 |
| I-V1-05 | `test_select_order_limit_chain_top_n` | "前 10 条按时间倒序" 真实场景 |
| I-V1-06 | `test_select_complex_where_e2e` | `WHERE (a=1 OR b=2) AND NOT c=3` |
| I-V1-07 | `test_select_offset_pagination` | OFFSET 翻页 |
| I-V1-08 | `test_select_all_features_chain` | UPDATE + SELECT chain 全开 |
| I-V1-09 | `test_delete_then_update_same_row` | DELETE → INSERT → UPDATE 同 PK 路径 |
| I-V1-10 | `test_update_spill_row` | UPDATE 后 row 跨页 spill |

### 8.4 e2e golden `tests/e2e/sql/engine_v1/*.sql`（12 条）

| 文件名 | SQL 概要 |
|-------|---------|
| `01_update_basic.sql` | `UPDATE t SET x=10 WHERE y=2`；SELECT 验证 |
| `02_update_multi_columns.sql` | `UPDATE t SET a=1, b='x' WHERE id=5` |
| `03_update_no_where.sql` | `UPDATE t SET flag=TRUE`（全表） |
| `04_update_persist.sql` | UPDATE + `-- REOPEN` + SELECT |
| `05_update_type_mismatch.sql` | `UPDATE t SET x='oops' WHERE y=1` 期望 ERROR |
| `06_select_compound_where.sql` | `SELECT * FROM t WHERE a=1 AND (b=2 OR c=3)` |
| `07_select_not_in_where.sql` | `SELECT * FROM t WHERE NOT (a=1)` |
| `08_select_order_asc.sql` | `ORDER BY x ASC LIMIT 5` |
| `09_select_order_desc.sql` | `ORDER BY x DESC LIMIT 5` |
| `10_select_order_multi_key.sql` | `ORDER BY a ASC, b DESC` |
| `11_select_offset_pagination.sql` | `LIMIT 3 OFFSET 6` |
| `12_update_grow_fallback.sql` | 长 TEXT 列 grow → chain insert |

每条 `.sql` 配套 `.expected.txt`，由 `tests/e2e/conftest.py` 收集比对（已有 runner，零改动）。

### 8.5 MVP 回归（tasks §8.1）

MVP 既有 234 个测试必须全部通过。关键改动可能影响的位置：

- `tests/unit/test_parser.py`：所有 `assert stmt.where == ("col", "=", lit)` 需改为 `EqualsExpr(column="col", value=lit)`。这是**测试迁移**，不是 production 迁移。
- `tests/integration/test_executor.py`：UPDATE 路径不直接受影响，但 `SELECT WHERE` 相关断言需迁移。
- `tests/unit/test_database_api.py` / `test_full_sql_lifecycle.py`：Row 不变，断言不变。

`pytest -x tests/` 全绿是 design 阶段退出条件之一（tasks §8.1）。

---

## 9. 性能与复杂度

### 9.1 parser

| 语句 | 复杂度 | 说明 |
|------|--------|------|
| SELECT WHERE expr | O(n) tokens | 递归下降；每层 O(k) tokens |
| SELECT ORDER BY k 项 | O(k) tokens | 单次扫描 |
| UPDATE SET n 列 | O(n) tokens | 单次扫描 |
| 完整 SELECT 链 | O(n + k) tokens | n = 总 token 数；k = ORDER BY 项数 |

### 9.2 executor

| 操作 | 复杂度 | 备注 |
|------|--------|------|
| UPDATE 单行 in-place | O(row_bytes) | append；常数项 |
| UPDATE 单行 grow fallback | O(row_bytes) | delete + insert；常数项 |
| UPDATE 全表（m 行） | O(m × (scan + eval + update)) | scan O(m)；eval O(expr 树深)；update O(1) |
| SELECT WHERE | O(m × eval) | 同 MVP 路径 |
| SELECT ORDER BY k 项 | O(m log m × k) | sorted 调用；次键 (slot_id) |
| SELECT LIMIT/OFFSET | O(m) | slice 操作 |

**n ≤ 10k** 时全部可接受；索引推迟到 `tinydb-engine-v2`（D4）。

### 9.3 内存峰值

- 排序：O(m) 行在内存中（D4 trade-off 接受；不引入外排序）。
- UPDATE fallback：同 page 内多 row 同时在内存中；page 数 × rows/page（~32 行）= ~32 行峰值。

---

## 10. 错误处理总览

| 层 | 错误类型 | 抛出条件 | 用户契约 |
|----|---------|---------|---------|
| tokenizer | `TokenError` | 字符无法识别 / 文本未闭合 | "line X, col Y: ..." |
| parser | `ParseError` | 语法错 / 关键字冲突列名 / LIMIT/OFFSET 非整数 | "line X, col Y: ..." |
| executor | `ExecutionError` | 表/列不存在 / 类型不匹配 / UPDATE 失败 / PageFull | 单行字符串 |
| executor | `PageFull`（内部捕获） | UPDATE grow + 同页满 | 退化为 chain insert |
| row_codec | `ValueError`（内部捕获） | 编码长度错 / buffer 截断 | 通过 `ExecutionError` 重抛 |

`Database.execute` 不重映射；上层（REPL、e2e runner）按 `TinydbError` 基类捕获后渲染为单行 `ERROR: <Class>: <msg>`。

---

## 11. 向后兼容与迁移

### 11.1 production 代码兼容

| 变更点 | 兼容性 | 影响范围 |
|-------|-------|---------|
| `Select.where` 类型 `tuple` → `Expr` | **破坏**（API 字段类型变） | `_exec_select / _exec_delete` 同步升级 |
| `Select.columns` 类型 `list` → `tuple` | **破坏** | `database.py` `Row` 包装处的 `last.columns == ["*"]` 需改为 `("*",)` |
| `Delete.where` 同 Select | **破坏** | `_exec_delete` 同步升级 |
| 新增 `Select.order_by / limit / offset` | 向后兼容（默认空） | 无 |
| 新增 `Update` AST | 向后兼容（dispatch 增量） | 无 |
| Tokenizer KEYWORDS 集合 | **影响**：旧脚本中 `update / set / and / ...` 作为列名/表名的 DDL 必须报错（任务 §4.3 显式覆盖） | 旧 DDL 抛 ParseError，是预期行为 |

### 11.2 测试代码迁移

需修改的测试断言：

- `tests/unit/test_parser.py` 中所有 `stmt.where == ("col", "=", lit)` 改为 `EqualsExpr(...)`。
- `tests/integration/test_executor.py` 中 SELECT/DELETE WHERE 断言同步迁移。
- `tests/integration/test_full_sql_lifecycle.py` 验证完整 SQL 生命周期，更新 e2e golden。
- `tests/e2e/sql/happy_path/*.sql`：MVP golden 不涉及 UPDATE/WHERE 复合/ORDER/LIMIT，零修改。

**build 阶段第一步**（task §1）：先迁移 test_parser.py 与 test_executor.py WHERE tuple 断言到 `EqualsExpr`，跑通 MVP 234 测试，确保零行为变化。这是后续 feature 的 TDD 红→绿起点。

### 11.3 数据文件兼容

存储格式（SlottedPage 布局）不变；catalog JSON 字段不变；schema_version 不变；现有 `.db` 文件无需迁移。

---

## 12. 风险与缓解

| ID | 风险 | 缓解 |
|----|------|------|
| R1 | parser 改动破坏现有路径 | tasks §8.1 MVP 234 测试全绿 + tasks §1 迁移测试先跑通 |
| R2 | UPDATE 跨页 fallback 崩溃半成品 | 文档披露到 `MVP_LIMITATIONS.md`；`tinydb-acid` 兜底 |
| R3 | AND/OR 短路错 | U-EXE-02/03 显式测试 `False AND (raise)` / `True OR (raise)` |
| R4 | 关键字加入后旧脚本破坏 | U-PAR-19/20 显式测试；e2e golden `engine_v1/05_update_type_mismatch` 负面验证 |
| R5 | ORDER BY 类型不可比 | `py_to_db` 预校验；U-EXE-25 覆盖 |
| R6 | LIMIT/OFFSET 负数 | parser 端识别 INT 字面量（负号已被 tokenizer 处理）；executor 端校验 |
| R7 | UPDATE 跨页 fallback 与同页其他行竞态 | §6.3 改进版：fallback 推迟到 page flush 后 chain insert |
| R8 | None 排尾 + BOOL falsy 不算 NULL | strict type 路径下不会出现 None；test_order_by_no_nulls 显式断言 |
| R9 | 模块行数膨胀 | tasks §8.3 显式 grep 行数 |

---

## 13. 模块行数预算

| 模块 | 当前 | 目标 | 备注 |
|------|------|------|------|
| `parser.py` | 369 | ≤ 750 | 增量 +150 行（新 AST + 4 个 _parse_*） |
| `executor.py` | 400 | ≤ 580（uplift from 520） | 增量 +180 行（eval_expr + _exec_update with chain fallback + sort/slice + chain insert routing） |
| `tokenizer.py` | 131 | ≤ 200 | 增量 +15 行（KEYWORDS 集合 + 1 行注释） |

build 阶段每个 PR 完成后运行：

```bash
wc -l src/tinydb/parser.py src/tinydb/executor.py src/tinydb/tokenizer.py
```

确保不超预算。**预算来源**：proposal.md "模块行数预算"。

---

## 14. 实施任务映射

| Task (tasks.md) | 设计章节 | 关键产物 |
|----------------|---------|---------|
| §1 Parser 表达式节点 | §3.3, §5.3 | `EqualsExpr / AndExpr / OrExpr / NotExpr` + 优先级入口 |
| §2 Parser WHERE 复合条件 | §5.3, §5.6 | `_parse_expr / _parse_or_expr / ...` |
| §3 Parser ORDER BY / LIMIT / OFFSET | §5.4 | `_parse_order_by / _parse_limit / _parse_offset` |
| §4 Tokenizer 关键字 | §4.1 | KEYWORDS 集合 + 冲突测试 |
| §5 Executor 表达式求值 | §6.1 | `eval_expr` + 短路 + type 校验 |
| §6 Executor UPDATE | §6.3 | `_exec_update` + v2 fallback |
| §7 Executor 排序与切片 | §6.4 | `_stable_sort` + LIMIT/OFFSET |
| §8 测试与回归 | §8 全部 | U-PAR-* / U-EXE-* / I-V1-* / e2e golden |

---

## 15. 退出条件（design 阶段）

设计文档必须满足：

- [x] 覆盖 AST 节点、语法 EBNF、错误处理、测试矩阵（§3, §5, §6, §8, §10）
- [x] D1-D5 决策落实到实现细节（§3.3 节点选型、§6.1 短路、§6.3 fallback、§6.4 sort key）
- [x] 模块行数预算与 proposal 对齐（§13）
- [x] 风险清单 R1-R9 + 缓解（§12）
- [x] 测试矩阵含单元 / 集成 / e2e 三层（§8.1-§8.4）

退出前运行：

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
node /home/lz/.agents/skills/comet/scripts/comet-state.mjs set tinydb-engine-v1 design_doc docs/superpowers/specs/2026-07-16-tinydb-engine-v1-design.md
node /home/lz/.agents/skills/comet/scripts/comet-handoff.mjs tinydb-engine-v1 design --write
node /home/lz/.agents/skills/comet/scripts/comet-guard.mjs tinydb-engine-v1 design --apply
```

guard 通过后 `.comet.yaml` 自动推进到 `phase: build`。

---

## 附录 A：与 MVP 现有测试的差异点

`tests/unit/test_parser.py` 当前断言形式（迁移前 → 迁移后）：

| 迁移前 | 迁移后 |
|--------|--------|
| `assert stmt.where == ("col", "=", 1)` | `assert stmt.where == EqualsExpr(column="col", value=1)` |
| `assert stmt.where is None` | 不变 |
| `assert stmt.columns == ["*"]` | `assert stmt.columns == ("*",)` |

`tests/integration/test_executor.py` 同步迁移；`tests/integration/test_database_api.py` 不变（Row 协议未变）。

## 附录 B：executor 中 `columns == ("*",)` 的一致性

涉及三处统一修改：

1. `_exec_select`：`if stmt.columns == ["*"]` → `if stmt.columns == ("*",)`。
2. `database.py` line 74：`if last.columns == ["*"]` → `if last.columns == ("*",)`。
3. 任何 `tests/` 中 `stmt.columns` 字面量断言：list → tuple。

build 阶段第一步统一修复并 grep `columns == \[` 确认零残留。

## 附录 C：UPDATE 失败的可观察性

MVP DML（INSERT/DELETE）返回 `[]`；UPDATE 同理。受影响行数通过以下路径观察：

1. **e2e golden**：`UPDATE ...; SELECT ...` 在同一脚本，golden 文本含两段输出。
2. **REPL**：UPDATE 显示 `OK`，用户另起 SELECT 验证。
3. **单元/集成测试**：直接调 `Executor._exec_update` 后再 SELECT，对比 row 数。

**不引入** UPDATE 返回受影响行数（如 SQLite 的 `cur.rowcount`），因为：
- 与 MVP DML 协议不一致；
- 增加 `Database.execute` 返回类型的 union 复杂度；
- 推迟到 `tinydb-engine-v2` 与统计信息一起做。

## 附录 D：ASC vs DESC 字符串排序

`_reverse_key` 中字符串 DESC 不做反转，依赖外层 sort flag `(1, v, sid)`：

- ASC `(0, 'apple', 3)` vs DESC `(1, 'apple', 5)`：分组 0 < 1，ASC 在前。
- 同为 DESC `(1, 'apple', 3)` vs `(1, 'banana', 5)`：tuple 排序 `(1, 'apple', ...) < (1, 'banana', ...)`，'apple' 在前 → DESC 中 'banana' 反而排前 ❌。

**修正**：字符串 DESC 需通过排序时反转比较器或对每列单独 reverse sort。简化实现：

```python
def _stable_sort(rows, items, schema):
    # 拆为 ASC 与 DESC 两组；ASC 在前 stable sort，DESC 在后 stable sort 后 reverse
    # 复杂度不变（仍是 O(m log m)），但代码分支增多
    ...
```

**v1 实现选择**：当前 §6.4 用单一 key + `(flag, value, sid)` 仅对数值列正确；字符串 DESC 测试需要单独处理。**build 阶段在 U-EXE-18 中显式覆盖并修正实现**：

```python
def _stable_sort(rows, items, schema):
    """Python stable sort by multi-key; DESC 通过 reverse sorted groups."""
    # Strategy: 按 OrderByItem 列表分桶：第一个 DESC 之前的项全部 ASC 排序；
    # 第一个 DESC 处切换方向，后续保持。
    # 简化：对每行构造 (value, slot_id) tuple，按 items 顺序两两比较。
    # 复杂度 O(m log m * k)；n ≤ 10k 时仍可接受。
    from functools import cmp_to_key
    name_to_idx = {n: i for i, (n, _) in enumerate(schema)}

    def cmp(r1, r2):
        for it in items:
            i1 = r1[1][name_to_idx[it.column]]
            i2 = r2[1][name_to_idx[it.column]]
            if i1 < i2:
                return 1 if it.descending else -1
            if i1 > i2:
                return -1 if it.descending else 1
        return 0  # 完全相等；Python sorted 稳定，按 slot_id 保留

    return sorted(rows, key=cmp_to_key(cmp))
```

**最终实现用 cmp_to_key** ——支持任意类型（D4 trade-off 仍是 Python stable sort，仅 key 函数从 tuple 改为 cmp）。修订此段以修正附录 D 缺陷。

---

**文档版本**：2026-07-16 design 阶段定稿 · 与 open-stage `design.md` D1-D5 决策一致 · 衔接 build 阶段。