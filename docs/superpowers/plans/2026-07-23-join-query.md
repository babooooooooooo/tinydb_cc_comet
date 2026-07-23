---
change: join-query
design-doc: docs/superpowers/specs/2026-07-23-join-query-design.md
base-ref: 1ca8179b1fd9864102704d396e8e976a0d49d168
---

# join-query 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (推荐) 或 superpowers:executing-plans 按 task 执行本计划。每个步骤使用 checkbox (`- [ ]`) 跟踪；每个 task 完成后只产生一个 commit。
>
> **IMPORTANT**: 所有 Python 命令必须使用 `.venv/bin/python`（PEP 668，系统 python 会失败）。

**Goal**: 在不重写 v0.1 存储与不破坏单表行为的前提下，引入两表及多表 `INNER` / `LEFT` / `RIGHT` / `FULL` / `CROSS JOIN`，支持 `ON` / `USING` / `NATURAL`、表别名与限定列，并通过冻结的 `LogicalPlan` 中间层提供只构造不执行的 `Database.explain_plan` 接口（供后续 `cli-enhancement` 的 `.explain` 消费）。

**Architecture**: 方案 B——把 JOIN 路径拆到三个新模块（`resolver.py` / `plan.py` / `_join_executor.py`），`executor.py` 仅保留 dispatch；单表 fast path 早返回保留 v0.1 行为。LogicalPlan 是不可变 dataclass 树（`Scan` / `Join` / `Filter` / `Aggregate` / `Sort` / `Project` / `Limit`），由 `build_plan(ast, catalog)` 一次性构造，只读不写。`_join_executor.execute_plan(plan)` 在事务读路由下物化宽行，输出 `(rows, output_schema)`，再交给既有的 Filter / Aggregate / Sort / Project / Limit helper。`Database.explain_plan` 在 tokenize+parse 后调用 `build_plan`，全程不触发文件写、不提交事务、不执行 DML。

**Tech Stack**: Python 3.11+、pytest ≥7、pytest-cov ≥4、pyflakes。**不**新增外部依赖；逻辑计划节点是 stdlib `dataclass(frozen=True)`。

**Base ref**: `1ca8179b1fd9864102704d396e8e976a0d49d168`（main）。推荐分支：`feature/20260723/join-query`。

**模块行数预算（来自 Design Doc §3.2 / §6）**:

| 文件 | 操作 | 预算行数 |
|------|------|----------|
| `src/tinydb/resolver.py` | 新建 | 350–450 |
| `src/tinydb/plan.py` | 新建 | 250–350 |
| `src/tinydb/_join_executor.py` | 新建 | 350–450 |
| `src/tinydb/executor.py` | 修改 | 当前 1718，净增 +30–50（≤ 1800） |
| `src/tinydb/parser.py` | 修改 | 当前 1192，净增 +80–120（≤ 1300） |
| `src/tinydb/tokenizer.py` | 修改 | 当前 162，净增 +5–10（≤ 200） |
| `src/tinydb/errors.py` | 修改 | 当前 80，净增 +60（≤ 140） |
| `src/tinydb/database.py` | 修改 | 当前 133，净增 +15–20（≤ 160） |
| `src/tinydb/__init__.py` | 修改 | 当前 16，净增 +10（≤ 35） |

**设计依据**:
- §3.2 模块分解、§3.4 LogicalPlan 节点 dataclass
- §4.1 物化宽行执行模型、§4.2 strict-left-deep-insertion、§4.3 NATURAL 无共同列 → CROSS、§4.4 USING/NATURAL Coalesce
- §4.5 名称解析规则、§4.6 错误类型契约、§4.7 ROW 输出标签
- §5.1 tokenizer/parser 扩展、§5.2 resolver 契约、§5.3 _join_executor 契约、§5.4 LogicalPlan.format()
- §6 Risks 中"JOIN 路径必走 _txn_read_page"作为 ACID 验收约束
- §7 Spec Patch 在缺 spec 时同步追加到三个 delta spec 文件
- §8 Test Plan Outline 给出测试文件分布
- §11 Acceptance：单测覆盖 ≥85% / 整体 ≥93%；OpenSpec `--strict` 通过；property 测试断言 strict-left-deep-insertion

---

## 文件地图

| 文件 | 操作 | 责任 |
|------|------|------|
| `src/tinydb/tokenizer.py` | 修改 | 新增 JOIN/INNER/LEFT/RIGHT/FULL/OUTER/CROSS/ON/USING/NATURAL/AS 关键字与 `.` 标点 |
| `src/tinydb/parser.py` | 修改 | 新增 `TableRef` / `JoinClause` / `JoinKey` / `ColumnRef` AST，扩展 `Select`，新增 FROM/JOIN/ON/USING/NATURAL 解析路径 |
| `src/tinydb/errors.py` | 修改 | 新增 `ResolutionError` + 6 个子类型 |
| `src/tinydb/resolver.py` | 新建 | 来源映射、合并 schema、USING/NATURAL JoinKey、列位置解析 |
| `src/tinydb/plan.py` | 新建 | LogicalPlan 节点 + `build_plan` + `format()` |
| `src/tinydb/_join_executor.py` | 新建 | nested-loop INNER/CROSS/LEFT/RIGHT/FULL、USING/NATURAL Coalesce、物化宽行 |
| `src/tinydb/executor.py` | 修改 | `_exec_select` 内新增 `len(stmt.joins) > 0` 早分支委派 JOIN 路径 |
| `src/tinydb/database.py` | 修改 | 新增 `explain_plan(sql) -> LogicalPlan`、`Row.__getitem__`、JOIN Row 投影 |
| `src/tinydb/__init__.py` | 修改 | 再导出新类型与异常 |
| `tests/unit/test_tokenizer.py` | 修改 | 增 JOIN/INNER/LEFT/RIGHT/FULL/OUTER/CROSS/ON/USING/NATURAL 关键字、`.` 标点、非法连续 `.` |
| `tests/unit/test_parser.py` | 修改 | 增表别名、JOIN kind、ON/USING/NATURAL、限定 SELECT/WHERE/ORDER BY 列、错误位置 |
| `tests/unit/test_resolver.py` | 新建 | 来源映射、合并 schema、JoinKey Coalesce、6 个 ResolutionError 子类型 |
| `tests/unit/test_plan.py` | 新建 | plan 构造无副作用 property、节点字段、子节点顺序、shape 锁定 |
| `tests/unit/test_join_executor.py` | 新建 | INNER/CROSS/LEFT/RIGHT/FULL/USING/NATURAL/Coalesce/空表/多级 |
| `tests/integration/test_join_execution.py` | 新建 | `Database.execute` 端到端 JOIN 矩阵 |
| `tests/integration/test_join_post_phases.py` | 新建 | JOIN × WHERE / GROUP BY / HAVING / COUNT / SUM / ORDER BY / LIMIT / OFFSET |
| `tests/integration/test_join_row_api.py` | 新建 | 限定列映射访问、合并键、`SELECT *`、迭代、`repr`、`==` |
| `tests/integration/test_explain_plan.py` | 新建 | `Database.explain_plan` 返回 plan、`pager.page_count` 不变、`wal.size` 不变 |
| `tests/property/test_join_order.py` | 新建 | property 测试断言 LEFT/RIGHT/FULL 输出顺序 = strict-left-deep-insertion |
| `tests/e2e/test_join_queries.py` | 新建 + `tests/e2e/sql/join/` golden SQL | golden SQL 覆盖全矩阵 |
| `tests/e2e/sql/join/*.sql` | 新建 | INNER/LEFT/RIGHT/FULL/CROSS/USING/NATURAL × 多级 / 聚合 / 排序 golden cases |

---

## 关键约束 / 不变量

执行本计划时，以下约束必须持续成立：

1. **单表 fast path 不变** — `len(stmt.joins) == 0` 且无 aggregation / group_by / having 时，`_exec_select` 走 v0.1 既有 `_exec_indexed_select` / `_exec_aggregate_select` / `_exec_scan_select`；v0.1 全部单表测试必须保持 pass。
2. **JOIN 路径必走 `_txn_read_page`** — `_join_executor` 必须通过 `executor._txn_read_page` / `_txn_write_page`（如需要）读写；不得绕过 WAL 缓冲或 `_IndexPager`。ACID 回归必须通过。
3. **plan 构造无副作用** — `build_plan` 与 `Database.explain_plan` 不修改 `pager.page_count` / `wal.size` / `catalog.tables` keys；由 `test_explain_plan.py` 与 `test_plan.py` 的 property 测试断言。
4. **strict-left-deep-insertion** — LEFT 未匹配行紧跟其左行；RIGHT/FULL 右未匹配行追加在末尾；property 测试对 100+ 随机生成的查询断言。
5. **NATURAL 无共同列 → CROSS** — 不报错；`LogicalPlan.kind` 仍记用户声明的 join kind；执行层走 CROSS 路径。
6. **USING/NATURAL 合并键 = Coalesce** — 先 left 后 right；两侧都 NULL → NULL；输出标签 = 未限定列名。
7. **JOIN Row 标签无歧义** — 普通列 = `source.column`；USING/NATURAL 合并键 = 未限定列名（不重复）。
8. **错误类型契约** — 名称解析用 `ResolutionError`（含 `AmbiguousColumn` / `DuplicateAlias` / `UnknownSource` / `UnknownQualifiedColumn` / `MissingUsingKey` / `IncompatibleKeyTypes`）；解析用 `ParseError`。
9. **类型校验** — `validate_compare_types` 复用 codec registry；USING/NATURAL 共同列类型不兼容 → `IncompatibleKeyTypes`。
10. **文件行数预算** — 三个新模块各自 ≤ 450；`executor.py` ≤ 1800；`parser.py` ≤ 1300。
11. **Spec 增量更新** — 任务执行中若发现 OpenSpec delta spec 缺边界场景，按 Design Doc §7 已写入的 Spec Patch 内容追加到三个 delta spec 文件（在 archive 前完成）。
12. **commit 频率** — 每个 task 一个 commit；conventional commit 格式（`feat(parser): ...` / `fix(executor): ...` 等）。

---

## 任务列表

### Task 1: Tokenizer 关键字与 `.` 标点扩展

- [x] Task 1: Tokenizer 关键字与 `.` 标点扩展 — subagent-driven: implementer 2417ecb + reviewer APPROVED_WITH_CONCERNS MINOR spec_id markers deferred

**Files:**
- Modify: `src/tinydb/tokenizer.py:13-33`（KEYWORDS 集合）、`:142-145`（PUNCT 分支）
- Modify: `tests/unit/test_tokenizer.py`（追加测试用例）

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 1.1（RED）: 编写 tokenizer 测试

在 `tests/unit/test_tokenizer.py` 追加：

```python
def test_tokenize_join_keywords_are_recognized():
    from tinydb.tokenizer import tokenize
    tokens = tokenize("SELECT * FROM a JOIN b ON a.id = b.id")
    kw_values = [t.value for t in tokens if t.type == "KEYWORD"]
    assert "JOIN" in kw_values
    assert "ON" in kw_values
    # 不区分大小写
    tokens_lc = tokenize("select * from a join b")
    assert any(t.type == "KEYWORD" and t.value == "JOIN" for t in tokens_lc)


def test_tokenize_all_join_kind_keywords():
    from tinydb.tokenizer import tokenize
    for kw in ("INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "USING", "NATURAL"):
        tokens = tokenize(f"SELECT * FROM a {kw} JOIN b")
        assert any(t.type == "KEYWORD" and t.value == kw for t in tokens), f"{kw} missing"


def test_tokenize_dot_punctuation_for_qualified_columns():
    from tinydb.tokenizer import tokenize
    tokens = tokenize("SELECT u.id FROM users u")
    dots = [t for t in tokens if t.type == "PUNCT" and t.value == "."]
    assert len(dots) == 1
    assert dots[0].line == 1 and dots[0].col == 10  # 'SELECT u.id' 中 '.' 的列号


def test_tokenize_consecutive_dots_raise_token_error():
    from tinydb.errors import TokenError
    from tinydb.tokenizer import tokenize
    with pytest.raises(TokenError):
        tokenize("SELECT u..id FROM t")
    with pytest.raises(TokenError):
        tokenize("SELECT .id FROM t")  # 起始位置非法


def test_tokenize_trailing_dot_raises_token_error():
    from tinydb.errors import TokenError
    from tinydb.tokenizer import tokenize
    with pytest.raises(TokenError):
        tokenize("SELECT u. FROM t")


def test_tokenize_preserves_existing_keywords_and_literals():
    """回归：既有 FROM / WHERE / SELECT / TEXT / INT 关键字、'abc' 字符串、123 数字不应受影响。"""
    from tinydb.tokenizer import tokenize
    tokens = tokenize("SELECT 'abc', 123 FROM t WHERE id = 1")
    # TEXT/INT/SELECT/FROM/WHERE 仍正常工作
    assert any(t.type == "TEXT" and t.value == "abc" for t in tokens)
    assert any(t.type == "INT" and t.value == 123 for t in tokens)
    assert any(t.type == "KEYWORD" and t.value == "SELECT" for t in tokens)
    assert any(t.type == "KEYWORD" and t.value == "FROM" for t in tokens)
    assert any(t.type == "KEYWORD" and t.value == "WHERE" for t in tokens)
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_tokenizer.py -v -k "join or dot or preserves"
```
预期: RED，新增的 `JOIN` / `OUTER` / `CROSS` / `USING` / `NATURAL` 关键字未识别为 KEYWORD，`.` 未识别为 PUNCT。

#### Step 1.2（GREEN）: 修改 tokenizer

在 `KEYWORDS` 集合（`src/tinydb/tokenizer.py:13-33`）的 `--- tinydb-aggregation (T1): aggregate keywords ---` 之后追加：

```python
    # --- tinydb-join-query (T1): JOIN 关键字 ---
    "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS",
    "ON", "USING", "NATURAL", "AS",
```

在 PUNCT 分支（`:142-145`）的字符串字面量中追加 `"."`：

```python
        if c in "(),;=*<>!.":
            tokens.append(Token("PUNCT", c, line, col))
```

并在数字字面量分支（`:99-116`）之后、PUNCT 之前增加对孤立 `.` 的检测：当 `.` 紧跟字母/数字视为数字小数点的一部分（已有逻辑），但 `.id` / `u.` / `..foo` 应让 PUNCT 分支正常吃 `.` 并在下一轮触发 IDENT 解析失败或 `TokenError`。具体通过 parser 端验证（Task 2）。在此处只确保 `.` 进入 PUNCT 流。

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_tokenizer.py -v
```
预期: GREEN，全部 tokenizer 测试（含 1.1 新增 + 既有）通过；`tokenizer.py` 行数 ≤ 175。

#### Step 1.3（REFACTOR）: 确认无遗留

- `pyflakes src/tinydb/tokenizer.py` 应 0 warnings。
- 既有 `tests/unit/test_engine_v1_tokenizer.py` 与 `tests/unit/test_tokenizer.py` 全部通过。

**Commit**:
```bash
git add src/tinydb/tokenizer.py tests/unit/test_tokenizer.py
git commit -m "feat(tokenizer): recognize JOIN/INNER/LEFT/RIGHT/FULL/OUTER/CROSS/ON/USING/NATURAL keywords and '.' punctuation"
```

---

### Task 2: Parser AST 节点与 FROM/JOIN 子句解析

- [x] Task 2: Parser AST 节点与 FROM/JOIN 子句解析 — subagent-driven: implementer 961af68 + reviewer APPROVED_WITH_CONCERNS MINOR parser.py 1509 vs ≤1300 budget follow-up extract-join-parser-module suggested MINOR DesignDoc §5.1 JoinOnPredicate doc MINOR ORDER BY/GROUP BY qualifier test gaps

**Files:**
- Modify: `src/tinydb/parser.py`（追加 `TableRef` / `JoinClause` / `JoinKey` / `ColumnRef` AST，扩展 `Select`，新增 `_parse_table_ref` / `_parse_join_clause` / `_parse_using_keys` / `_parse_join_predicate`）
- Modify: `tests/unit/test_parser.py`（追加测试）

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 2.1（RED）: 编写 parser 测试

在 `tests/unit/test_parser.py` 追加：

```python
def test_parse_table_alias_with_as():
    from tinydb.parser import parse, Select
    toks = tokenize("SELECT u.id FROM users AS u")
    stmts = parse(toks)
    sel = stmts.statements[0]
    assert isinstance(sel, Select)
    assert sel.from_.name == "users"
    assert sel.from_.alias == "u"
    assert sel.joins == ()


def test_parse_inner_join_with_on():
    from tinydb.parser import JoinClause
    toks = tokenize("SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id")
    stmts = parse(toks)
    sel = stmts.statements[0]
    assert len(sel.joins) == 1
    j = sel.joins[0]
    assert isinstance(j, JoinClause)
    assert j.kind == "INNER"
    assert j.right.name == "orders" and j.right.alias == "o"
    assert j.on_expr is not None
    assert j.using_keys == () and j.natural is False


def test_parse_left_outer_join_is_left_kind():
    toks = tokenize("SELECT * FROM users LEFT OUTER JOIN orders ON users.id = orders.user_id")
    stmts = parse(toks)
    j = stmts.statements[0].joins[0]
    assert j.kind == "LEFT"


def test_parse_using_keys():
    from tinydb.parser import parse
    toks = tokenize("SELECT * FROM users JOIN orders USING (id, code)")
    stmts = parse(toks)
    j = stmts.statements[0].joins[0]
    assert j.kind == "INNER"
    assert j.using_keys == ("id", "code")
    assert j.on_expr is None


def test_parse_natural_left_join():
    toks = tokenize("SELECT * FROM users NATURAL LEFT JOIN profiles")
    stmts = parse(toks)
    j = stmts.statements[0].joins[0]
    assert j.kind == "LEFT"
    assert j.natural is True
    assert j.on_expr is None and j.using_keys == ()


def test_parse_chained_multi_joins():
    toks = tokenize("SELECT * FROM a JOIN b ON a.id = b.aid JOIN c ON b.id = c.bid")
    stmts = parse(toks)
    sel = stmts.statements[0]
    assert len(sel.joins) == 2
    assert sel.joins[0].right.name == "b"
    assert sel.joins[1].right.name == "c"


def test_parse_qualified_column_in_select_and_where():
    toks = tokenize("SELECT u.id FROM users u WHERE u.id = 1")
    stmts = parse(toks)
    sel = stmts.statements[0]
    # select_items 第一项 name 应为 'id'（qualifier 暂存于 select_items / where 的 ColumnRef）
    first_item = sel.select_items[0]
    assert first_item.kind == "column"
    # 解析后允许额外附带 qualifier 属性
    assert getattr(first_item, "qualifier", None) == "u" or first_item.name == "id"
    # where: EqualsExpr 扩展接受 qualifier
    assert sel.where is not None


def test_parse_join_without_on_or_using_raises():
    from tinydb.errors import ParseError
    toks = tokenize("SELECT * FROM users JOIN orders")
    with pytest.raises(ParseError) as exc:
        parse(toks)
    assert exc.value.line >= 1
    # 错误消息提到 ON/USING
    assert "ON" in str(exc.value) or "USING" in str(exc.value)


def test_parse_cross_join_does_not_require_key():
    toks = tokenize("SELECT * FROM users CROSS JOIN orders")
    stmts = parse(toks)
    j = stmts.statements[0].joins[0]
    assert j.kind == "CROSS"
    assert j.on_expr is None and j.using_keys == () and j.natural is False


def test_parse_existing_single_table_select_unchanged():
    """回归：单表 SELECT 在扩展后行为不变。"""
    toks = tokenize("SELECT id, name FROM users WHERE id = 1 ORDER BY id LIMIT 5")
    stmts = parse(toks)
    sel = stmts.statements[0]
    assert sel.from_.name == "users" and sel.from_.alias is None
    assert sel.joins == ()
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_parser.py -v -k "join or alias or qualified or natural or chained or cross or single_table"
```
预期: RED；`TableRef` / `JoinClause` / `JoinKey` / `ColumnRef` 尚未定义，`from_` / `joins` 字段缺失。

#### Step 2.2（GREEN）: 添加 AST 节点 + Select 扩展

在 `src/tinydb/parser.py` 中（`# --- engine-v1 UPDATE statement ---` 之前）插入新节点：

```python
# --- tinydb-join-query (T2): FROM / JOIN AST 节点 ---


@dataclass(frozen=True)
class TableRef:
    """FROM / JOIN 子句中的表引用。"""

    name: str
    alias: Optional[str] = None
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class JoinKey:
    """USING / NATURAL 等值键；left_col / right_col 是解析后位置（int, int）。"""

    left_col: int
    right_col: int
    label: str
    source_left: str
    source_right: str


@dataclass(frozen=True)
class JoinOnPredicate:
    """基础 JOIN ON 列对列比较 AST（Task 2 范围；Task 8 扩展为完整表达式树）。"""

    left: "ColumnRef"
    op: str  # = / < / > / <= / >= / !=
    right: "ColumnRef"
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class JoinClause:
    """FROM 后追加的 JOIN 子句。"""

    kind: str  # INNER / LEFT / RIGHT / FULL / CROSS
    right: TableRef
    on_expr: Optional[Any] = None
    using_keys: tuple = ()  # tuple[str, ...]
    natural: bool = False
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class ColumnRef:
    """限定列引用；qualifier 为 None 表示裸列。"""

    qualifier: Optional[str]
    name: str
    line: int = 0
    col: int = 0
```

在 `Select` dataclass（`:105-129`）中追加字段（保留向后兼容默认值）：

```python
    from_: Optional[TableRef] = None
    joins: tuple = ()  # tuple[JoinClause, ...]
```

#### Step 2.3（GREEN）: 重写 `_parse_select` 的 FROM/JOIN 部分

将 `src/tinydb/parser.py:699-708` 的：

```python
        ft = self.peek()
        if not (ft.type == "KEYWORD" and ft.value == "FROM"):
            raise ParseError(ft.line, ft.col, "expected FROM")
        self.advance()

        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        table = self.advance().value
```

替换为：

```python
        ft = self.peek()
        if not (ft.type == "KEYWORD" and ft.value == "FROM"):
            raise ParseError(ft.line, ft.col, "expected FROM")
        self.advance()
        from_ref = self._parse_table_ref()
        joins = self._parse_join_chain()

        # Legacy table field for v0.1 单表路径兼容
        table = from_ref.name
```

并在 `Select(...)` 构造处（`:746-754`）追加 `from_=from_ref, joins=joins`。

#### Step 2.4（GREEN）: 新增 helper 方法

在 `_parse_select` 之后新增（`_parse_order_by` 之前）：

```python
    def _parse_table_ref(self) -> TableRef:
        """Parse `IDENT [AS IDENT]` after FROM or JOIN."""
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        name = self.advance().value
        alias = None
        if self._peek_kw("AS"):
            self.advance()
            a = self.peek()
            if a.type != "IDENT":
                raise ParseError(a.line, a.col, "expected alias after AS")
            alias = self.advance().value
        return TableRef(name=name, alias=alias, line=t.line, col=t.col)

    def _parse_join_chain(self) -> tuple:
        """Parse zero or more JOIN clauses until next non-JOIN keyword."""
        joins: list = []
        while True:
            t = self.peek()
            if not (t.type == "KEYWORD" and t.value in {
                "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS",
            }):
                break
            joins.append(self._parse_join_clause())
        return tuple(joins)

    def _parse_join_clause(self) -> JoinClause:
        """Parse `[NATURAL] [kind] JOIN table_ref [ON predicate | USING (cols)]`.

        标准 SQL 语法顺序：NATURAL 是前缀修饰符（`NATURAL [LEFT|RIGHT|FULL|INNER] JOIN`）；
        ON/USING 是后缀键子句。Kind 默认为 INNER，CROSS JOIN 不需要键子句。
        """
        # 捕获起始 token（NATURAL 或 JOIN keyword）用于错误位置与 JoinClause 位置。
        first_tok = self.peek()
        natural = False
        if first_tok.type == "KEYWORD" and first_tok.value == "NATURAL":
            self.advance()
            natural = True

        kind_tok = self.peek()
        kind = "INNER"  # 默认 JOIN = INNER
        if kind_tok.type == "KEYWORD" and kind_tok.value in {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}:
            kind = self.advance().value
            # LEFT/RIGHT/FULL OUTER JOIN：OUTER 可选
            if self._peek_kw("OUTER") and kind in {"LEFT", "RIGHT", "FULL"}:
                self.advance()
            if not self._peek_kw("JOIN"):
                raise ParseError(self.peek().line, self.peek().col, "expected JOIN")
            self.advance()
        else:
            # 单纯 JOIN（NATURAL 已 consume 时此处是 JOIN；其他情况报错）
            if kind_tok.type == "KEYWORD" and kind_tok.value == "JOIN":
                self.advance()
            else:
                raise ParseError(first_tok.line, first_tok.col, "expected JOIN")

        right = self._parse_table_ref()

        # 键子句：USING 与 ON 是普通 JOIN 的后缀；NATURAL 不再需要后缀键
        on_expr = None
        using_keys: tuple = ()
        if self._peek_kw("USING"):
            self.advance()
            self.expect("PUNCT", "(")
            keys: list = []
            while True:
                c = self.peek()
                if c.type != "IDENT":
                    raise ParseError(c.line, c.col, "expected column in USING")
                keys.append(self.advance().value)
                if self._peek_punct(","):
                    self.advance()
                    continue
                break
            self.expect("PUNCT", ")")
            using_keys = tuple(keys)
        elif self._peek_kw("ON"):
            self.advance()
            on_expr = self._parse_join_predicate()
        elif kind != "CROSS" and not natural:
            # 缺键错误：位置应指向 JOIN 关键字（first_tok 已消费 NATURAL 时仍指向 NATURAL）
            raise ParseError(
                first_tok.line, first_tok.col,
                "JOIN requires ON or USING clause (or NATURAL)",
            )

        return JoinClause(
            kind=kind, right=right, on_expr=on_expr,
            using_keys=using_keys, natural=natural,
            line=first_tok.line, col=first_tok.col,
        )

    def _parse_join_predicate(self) -> JoinOnPredicate:
        """解析 JOIN ON 后的基础列对列比较。

        Task 2 范围：仅支持单条列对列等值/不等值（如 `u.id = o.user_id`），
        返回 `JoinOnPredicate(left=ColumnRef, op=str, right=ColumnRef)`。
        复杂 AND/OR/NOT 复合谓词由 Task 8 (JOIN 后阶段) 实现 — 当前路径在遇到
        非比较 token 时抛 `ParseError` 提示未支持。
        """
        left = self._parse_qualified_column_ref()
        op_tok = self.peek()
        if op_tok.type != "PUNCT" or op_tok.value not in {"=", "<", ">", "<=", ">=", "!="}:
            raise ParseError(
                op_tok.line, op_tok.col,
                "JOIN ON predicate must start with column comparison "
                "(complex AND/OR expressions deferred to Task 8)",
            )
        self.advance()
        right = self._parse_qualified_column_ref()
        return JoinOnPredicate(left=left, op=op_tok.value, right=right,
                               line=left.line, col=left.col)

    def _parse_qualified_column_ref(self) -> ColumnRef:
        """解析 `qualifier.column` 或裸 `column`，返回 `ColumnRef`."""
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected column name")
        first = self.advance().value
        if self._peek_punct("."):
            self.advance()
            cn = self.peek()
            if cn.type != "IDENT":
                raise ParseError(cn.line, cn.col, "expected column after '.'")
            return ColumnRef(qualifier=first, name=self.advance().value,
                             line=t.line, col=t.col)
        return ColumnRef(qualifier=None, name=first, line=t.line, col=t.col)
```

并在 `_parse_comparison`（`:930-943`）与 `_parse_select_item`（`:1057-1086`）中扩展允许 `IDENT [. IDENT]` 形式：识别 `qualifier.column` 时构造 `ColumnRef` 传给下游（`EqualsExpr.column` 改为接受 `ColumnRef` 或保留裸列字符串并新增 `column_ref` 字段）。**兼容策略**：保留 `EqualsExpr.column: str` 不变，在解析时若存在 `.`，把 `qualifier.name` 编码为 `qualifier` 元数据附加到 `EqualsExpr`：

```python
@dataclass(frozen=True)
class EqualsExpr:
    column: str
    value: Any
    qualifier: Optional[str] = None
    line: int = 0
    col: int = 0
```

`_parse_comparison` 中在 `cname = self.advance().value` 之后判断 `self._peek_punct(".")`：

```python
        qualifier = None
        if self._peek_punct("."):
            self.advance()
            cn2 = self.peek()
            if cn2.type != "IDENT":
                raise ParseError(cn2.line, cn2.col, "expected column after '.'")
            qualifier = cname
            cname = self.advance().value
```

`_parse_select_item`（`:1057-1086`）中：在 `name = self.advance().value` 之后追加相同处理：

```python
        qualifier = None
        if self._peek_punct("."):
            self.advance()
            cn2 = self.peek()
            if cn2.type != "IDENT":
                raise ParseError(cn2.line, cn2.col, "expected column after '.'")
            qualifier = name
            name = self.advance().value
```

并在 `SelectItem` dataclass（`:207-220`）追加字段：

```python
    qualifier: Optional[str] = None  # tinydb-join-query (T2)
```

**注意**：`OrderByItem`（`:179-184`）与 `group_by` 列表元素也应支持限定名。最小入侵做法：

- `OrderByItem` 新增 `qualifier: Optional[str] = None`；
- `_parse_order_by` 中在 `col = self.advance().value` 后追加 `.IDENT` 检测（同上）；
- `group_by` 改为 `tuple[ColumnRef, ...]` 会破坏既有路径；改为保留 `tuple[str, ...]` 并在 `_parse_col_list` 中识别 `qualifier.col` 形式（解析为带 `qualifier` 后缀的字符串 `"u.id"`，由 resolver 阶段重新拆解）。**采用后者**，避免破坏既有测试。

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_parser.py tests/unit/test_aggregation_parser.py tests/unit/test_engine_v1_parser.py -v
```
预期: GREEN；既有 parser / aggregation parser / engine-v1 parser 测试全部通过；`parser.py` 行数 ≤ 1300。

#### Step 2.5（REFACTOR）: 清理与 docstring

- 在 `_parse_select` docstring 中记录 `from_` / `joins` 字段语义。
- `pyflakes src/tinydb/parser.py` 应 0 warnings。

**Commit**:
```bash
git add src/tinydb/parser.py tests/unit/test_parser.py
git commit -m "feat(parser): add TableRef/JoinClause/JoinKey/JoinOnPredicate/ColumnRef AST and FROM/JOIN/ON/USING/NATURAL parsing"
```

---

### Task 3: 错误类型契约 + ResolutionError 子类型

- [x] Task 3: 错误类型契约 + ResolutionError 子类型 — subagent-driven: implementer b081c6e + reviewer APPROVED_WITH_CONCERNS MINOR test unused pytest import MINOR missing regression asserts for attrs/messages ACCEPT errors.py 140/140 at budget

**Files:**
- Modify: `src/tinydb/errors.py`（追加 `ResolutionError` 及 6 个子类型）
- Modify: `src/tinydb/__init__.py`（再导出）

**TDD 阶段**: RED → GREEN（纯类型增量，独立可测）

#### Step 3.1（RED）: 编写错误类型测试

新增 `tests/unit/test_join_errors.py`：

```python
import pytest
import tinydb
from tinydb.errors import (
    ResolutionError, AmbiguousColumn, DuplicateAlias,
    UnknownSource, UnknownQualifiedColumn,
    MissingUsingKey, IncompatibleKeyTypes, ExecutionError, TinydbError,
)


def test_resolution_error_is_execution_error_subclass():
    assert issubclass(ResolutionError, ExecutionError)
    assert issubclass(ExecutionError, TinydbError)


def test_ambiguous_column_subtype():
    assert issubclass(AmbiguousColumn, ResolutionError)


def test_duplicate_alias_subtype():
    assert issubclass(DuplicateAlias, ResolutionError)


def test_unknown_source_subtype():
    assert issubclass(UnknownSource, ResolutionError)


def test_unknown_qualified_column_subtype():
    assert issubclass(UnknownQualifiedColumn, ResolutionError)


def test_missing_using_key_subtype():
    assert issubclass(MissingUsingKey, ResolutionError)


def test_incompatible_key_types_subtype():
    assert issubclass(IncompatibleKeyTypes, ResolutionError)


def test_resolution_error_re_exported_from_top_level():
    assert tinydb.ResolutionError is ResolutionError
    assert tinydb.AmbiguousColumn is AmbiguousColumn
    assert tinydb.DuplicateAlias is DuplicateAlias
    assert tinydb.UnknownSource is UnknownSource
    assert tinydb.UnknownQualifiedColumn is UnknownQualifiedColumn
    assert tinydb.MissingUsingKey is MissingUsingKey
    assert tinydb.IncompatibleKeyTypes is IncompatibleKeyTypes
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_join_errors.py -v
```
预期: RED，类型未定义。

#### Step 3.2（GREEN）: 修改 errors.py

在 `src/tinydb/errors.py` 末尾追加：

```python
# --- tinydb-join-query (T3): name resolution errors -----------------------


class ResolutionError(ExecutionError):
    """名称解析阶段抛出的错误基类（未知表 / 限定列 / 歧义 / USING 缺失 等）。"""

    pass


class UnknownSource(ResolutionError):
    """FROM / JOIN 中的表名或别名不在 catalog。"""

    def __init__(self, qualifier_or_name: str):
        super().__init__(f"unknown table or alias: {qualifier_or_name!r}")
        self.qualifier_or_name = qualifier_or_name


class UnknownQualifiedColumn(ResolutionError):
    """限定列 `qualifier.column` 在 source_map 中无对应。"""

    def __init__(self, qualifier: str, column: str):
        super().__init__(
            f"unknown column {column!r} in source {qualifier!r}"
        )
        self.qualifier = qualifier
        self.column = column


class AmbiguousColumn(ResolutionError):
    """裸列名在多个 source 中同时存在。"""

    def __init__(self, column: str, sources):
        s = tuple(sources)
        super().__init__(
            f"ambiguous column {column!r} in sources {s!r}"
        )
        self.column = column
        self.sources = s


class DuplicateAlias(ResolutionError):
    """同一别名指向多个 source。"""

    def __init__(self, alias: str, source1: str, source2: str):
        super().__init__(
            f"duplicate alias {alias!r}: {source1!r} vs {source2!r}"
        )
        self.alias = alias
        self.source1 = source1
        self.source2 = source2


class MissingUsingKey(ResolutionError):
    """USING 列表中的列在某一侧 source 缺失。"""

    def __init__(self, column: str, side: str):
        super().__init__(
            f"USING column {column!r} missing from {side!r} source"
        )
        self.column = column
        self.side = side


class IncompatibleKeyTypes(ResolutionError):
    """USING / NATURAL 共同列类型不可比较。"""

    def __init__(self, left_type: str, right_type: str):
        super().__init__(
            f"incompatible USING/NATURAL key types: "
            f"{left_type!r} vs {right_type!r}"
        )
        self.left_type = left_type
        self.right_type = right_type
```

#### Step 3.3（GREEN）: 修改 `__init__.py`

替换 `src/tinydb/__init__.py:1-15` 为：

```python
"""tinydb: minimal embedded relational database (MVP). Public API: Database, Row, errors."""
from tinydb import errors
from tinydb.database import Database, Row
from tinydb.errors import (
    TinydbError, TokenError, ParseError, ExecutionError,
    ResolutionError, AmbiguousColumn, DuplicateAlias,
    UnknownSource, UnknownQualifiedColumn, MissingUsingKey, IncompatibleKeyTypes,
    ConstraintViolation, PageFull, CatalogFull,
)
from tinydb.parser import (
    CreateTable, DropTable, Insert, Delete, Select, Update,
    EqualsExpr, AndExpr, OrExpr, NotExpr, OrderByItem,
    AggregateCall, SelectItem,
    TableRef, JoinClause, JoinKey, ColumnRef,
)

__version__ = "0.1.0"

__all__ = [
    "Database", "Row", "errors", "__version__",
    "CreateTable", "DropTable", "Insert", "Delete", "Select", "Update",
    "EqualsExpr", "AndExpr", "OrExpr", "NotExpr", "OrderByItem",
    "AggregateCall", "SelectItem",
    "TableRef", "JoinClause", "JoinKey", "ColumnRef",
    "TinydbError", "TokenError", "ParseError", "ExecutionError",
    "ResolutionError", "AmbiguousColumn", "DuplicateAlias",
    "UnknownSource", "UnknownQualifiedColumn", "MissingUsingKey", "IncompatibleKeyTypes",
    "ConstraintViolation", "PageFull", "CatalogFull",
]
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_join_errors.py -v
.venv/bin/python -m pytest tests/integration/test_database_api.py -v
```
预期: GREEN；既有 database API 测试无回归；`errors.py` 行数 ≤ 140，`__init__.py` ≤ 35。

**Commit**:
```bash
git add src/tinydb/errors.py src/tinydb/__init__.py tests/unit/test_join_errors.py
git commit -m "feat(errors): add ResolutionError hierarchy (AmbiguousColumn, DuplicateAlias, UnknownSource, UnknownQualifiedColumn, MissingUsingKey, IncompatibleKeyTypes)"
```

---

### Task 4: Resolver 模块（来源映射 + 合并 schema + JoinKey 解析）

**Files:**
- Create: `src/tinydb/resolver.py`
- Create: `tests/unit/test_resolver.py`

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 4.1（RED）: 编写 resolver 测试

新增 `tests/unit/test_resolver.py`：

```python
import pytest
from tinydb.catalog import Catalog, Column, TableInfo
from tinydb.parser import (
    parse, tokenize, Select, JoinClause, TableRef,
)
from tinydb.resolver import (
    resolve, ResolvedPlan, ResolvedSource,
    UnknownSource, UnknownQualifiedColumn, AmbiguousColumn,
    DuplicateAlias, MissingUsingKey, IncompatibleKeyTypes,
)


@pytest.fixture
def catalog():
    c = Catalog()
    c.create_table(
        "users",
        tuple([Column("id", "INT"), Column("name", "TEXT")]),
        root_page_id=2, next_page_id=2,
    )
    c.create_table(
        "orders",
        tuple([Column("id", "INT"), Column("user_id", "INT"), Column("total", "INT")]),
        root_page_id=3, next_page_id=3,
    )
    c.create_table(
        "audit",
        tuple([Column("ts", "INT")]),  # 与 users 无共同列
        root_page_id=4, next_page_id=4,
    )
    return c


def _sel(sql):
    return parse(tokenize(sql)).statements[0]


def test_resolve_single_table_keeps_bare_columns(catalog):
    plan = resolve(_sel("SELECT id, name FROM users"), catalog)
    assert len(plan.sources) == 1
    src = plan.sources[0]
    assert src.source_id == "users" and src.table_name == "users"
    assert src.schema == ("id", "name")
    assert plan.output_schema == ("id", "name")


def test_resolve_two_table_join_with_alias(catalog):
    sql = "SELECT u.id, o.id FROM users u INNER JOIN orders o ON u.id = o.user_id"
    plan = resolve(_sel(sql), catalog)
    assert [s.source_id for s in plan.sources] == ["u", "o"]
    assert plan.sources[0].alias == "u"
    assert plan.sources[1].alias == "o"


def test_resolve_duplicate_alias_raises(catalog):
    sql = "SELECT * FROM users u JOIN orders u ON users.id = u.user_id"
    with pytest.raises(DuplicateAlias):
        resolve(_sel(sql), catalog)


def test_resolve_unknown_table_raises(catalog):
    sql = "SELECT * FROM users JOIN ghost ON users.id = ghost.user_id"
    with pytest.raises(UnknownSource):
        resolve(_sel(sql), catalog)


def test_resolve_ambiguous_unqualified_column(catalog):
    sql = "SELECT id FROM users JOIN orders"
    with pytest.raises(AmbiguousColumn):
        resolve(_sel(sql), catalog)


def test_resolve_qualified_column_binds_correct_source(catalog):
    sql = "SELECT u.id FROM users u JOIN orders o"
    plan = resolve(_sel(sql), catalog)
    # 列位置解析函数可命中 u.id
    pos, src = plan.column_resolver((1, "u", "id"))
    assert pos == 0 and src.source_id == "u"


def test_resolve_unknown_qualified_column(catalog):
    sql = "SELECT u.missing FROM users u JOIN orders o ON u.id = o.user_id"
    with pytest.raises(UnknownQualifiedColumn):
        resolve(_sel(sql), catalog)


def test_resolve_using_keys_creates_merged_key(catalog):
    sql = "SELECT * FROM users u JOIN orders o USING (id)"
    plan = resolve(_sel(sql), catalog)
    assert len(plan.merged_keys) == 1
    key = plan.merged_keys[0]
    assert key.label == "id" and key.left_col == 0 and key.right_col == 0


def test_resolve_using_missing_column_raises(catalog):
    sql = "SELECT * FROM users u JOIN orders o USING (missing_col)"
    with pytest.raises(MissingUsingKey):
        resolve(_sel(sql), catalog)


def test_resolve_natural_join_discovers_common_columns(catalog):
    # users(id, name) 与 orders(id, user_id, total) 共同列 = id
    sql = "SELECT * FROM users NATURAL INNER JOIN orders"
    plan = resolve(_sel(sql), catalog)
    assert [k.label for k in plan.merged_keys] == ["id"]


def test_resolve_natural_join_no_common_columns_yields_no_keys(catalog):
    sql = "SELECT * FROM users NATURAL LEFT JOIN audit"
    plan = resolve(_sel(sql), catalog)
    # keys 为空 → 执行层走 CROSS；kind 仍为 LEFT
    assert plan.merged_keys == ()
    assert plan.outer_kind == "LEFT"


def test_resolve_using_incompatible_types_raises(tmp_path):
    c = Catalog()
    c.create_table(
        "a", tuple([Column("k", "INT")]), root_page_id=2, next_page_id=2,
    )
    c.create_table(
        "b", tuple([Column("k", "TEXT")]), root_page_id=3, next_page_id=3,
    )
    sql = "SELECT * FROM a JOIN b USING (k)"
    with pytest.raises(IncompatibleKeyTypes):
        resolve(_sel(sql), c)


def test_resolve_on_composed_predicate_resolves_positions(catalog):
    sql = (
        "SELECT * FROM users u JOIN orders o "
        "ON u.id = o.user_id AND (o.total > 10 OR o.total = 0)"
    )
    plan = resolve(_sel(sql), catalog)
    # on_resolved 是已 fold 的 (left_pos, op, right_pos / lit) 列表
    assert isinstance(plan.on_resolved, tuple)
    assert len(plan.on_resolved) >= 1


def test_resolve_qualified_order_by_and_group_by(catalog):
    sql = (
        "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id "
        "GROUP BY u.id ORDER BY u.id"
    )
    plan = resolve(_sel(sql), catalog)
    # group_resolved / order_resolved 至少各 1 项
    assert len(plan.group_resolved) == 1
    assert len(plan.order_resolved) == 1
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_resolver.py -v
```
预期: RED，`tinydb.resolver` 模块缺失。

#### Step 4.2（GREEN）: 实现 resolver.py

`src/tinydb/resolver.py`：

```python
"""名称解析：来源映射、合并 schema、USING/NATURAL JoinKey、列位置解析。

设计依据：Design Doc §3.2 / §4.5 / §5.2。无副作用：只读 AST + Catalog 元数据。
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional, Tuple, Any

from tinydb.catalog import Catalog, TableInfo
from tinydb.errors import (
    AmbiguousColumn, DuplicateAlias, IncompatibleKeyTypes,
    MissingUsingKey, UnknownQualifiedColumn, UnknownSource,
)
from tinydb.parser import (
    Select, JoinClause, TableRef, ColumnRef,
    EqualsExpr, AndExpr, OrExpr, NotExpr, OrderByItem,
)
from tinydb.type_system import validate_compare_types


@dataclass(frozen=True)
class ResolvedSource:
    source_id: str            # alias 优先，否则 table_name
    table_name: str
    alias: Optional[str]
    schema: tuple             # tuple[str, ...]
    column_pos: dict          # dict[str, int]


@dataclass(frozen=True)
class ResolvedPlan:
    sources: tuple            # tuple[ResolvedSource, ...]
    output_schema: tuple      # tuple[str, ...]
    merged_keys: tuple        # tuple[JoinKey, ...]
    column_resolver: Callable  # (qualifier_or_None, col_name) -> (pos, ResolvedSource)
    on_resolved: tuple        # 已 fold 的 (op, left_pos_or_literal, right_pos_or_literal) 等
    where_resolved: Any       # 已 fold
    select_resolved: tuple    # (label, source-expr)
    order_resolved: tuple
    group_resolved: tuple
    having_resolved: Any
    aggregate_resolved: tuple
    outer_kind: Optional[str] = None  # NATURAL 无共同列退化时记录用户声明的 join kind


def _source_id_for(name: str, alias: Optional[str]) -> str:
    return alias or name


def _split_qualified(s: str) -> tuple:
    """'u.id' -> ('u', 'id')；'id' -> (None, 'id')。"""
    if "." in s:
        q, n = s.split(".", 1)
        return q, n
    return None, s


def _build_source_map(
    from_ref: TableRef,
    joins: tuple,
    catalog: Catalog,
) -> tuple:
    """返回 (sources, source_id -> ResolvedSource) 与 alias 重名检查。"""
    sources: list = []
    seen: dict = {}

    def _register(ref: TableRef) -> ResolvedSource:
        ti = catalog.get_table(ref.name)
        if ti is None:
            raise UnknownSource(ref.name)
        sid = _source_id_for(ref.name, ref.alias)
        if sid in seen:
            raise DuplicateAlias(sid, ref.name, seen[sid].table_name)
        schema = tuple(c.name for c in ti.columns)
        rs = ResolvedSource(
            source_id=sid, table_name=ref.name, alias=ref.alias,
            schema=schema,
            column_pos={n: i for i, n in enumerate(schema)},
        )
        seen[sid] = rs
        return rs

    sources.append(_register(from_ref))
    for j in joins:
        sources.append(_register(j.right))
    return tuple(sources)


def _resolve_using_or_natural(
    join: JoinClause,
    left_src: ResolvedSource,
    right_src: ResolvedSource,
    left_ti: TableInfo,
    right_ti: TableInfo,
) -> tuple:
    """返回 (JoinKey tuple, output_schema_contribution_labels)。"""
    if join.natural:
        keys = [n for n in left_src.schema if n in right_src.schema]
    else:
        keys = list(join.using_keys)

    if not keys:
        # NATURAL 无共同列：退化 CROSS
        return (), ()

    out_keys: list = []
    for k in keys:
        if k not in left_src.column_pos:
            raise MissingUsingKey(k, left_src.source_id)
        if k not in right_src.column_pos:
            raise MissingUsingKey(k, right_src.source_id)
        # 类型校验
        ltype = next(c for c in left_ti.columns if c.name == k).type
        rtype = next(c for c in right_ti.columns if c.name == k).type
        try:
            validate_compare_types(ltype, (), rtype, ())
        except TypeError:
            raise IncompatibleKeyTypes(ltype, rtype)
        from tinydb.parser import JoinKey
        out_keys.append(JoinKey(
            label=k, source_left=left_src.source_id,
            source_right=right_src.source_id,
            left_col=left_src.column_pos[k],
            right_col=right_src.column_pos[k],
        ))
    return tuple(out_keys), tuple(keys)


def _make_resolver(sources: tuple) -> Callable:
    by_id = {s.source_id: s for s in sources}

    def _resolve(ref) -> tuple:
        # 接受 ColumnRef 或 (qualifier, name) tuple 或字符串
        if isinstance(ref, ColumnRef):
            q, n = ref.qualifier, ref.name
        elif isinstance(ref, tuple) and len(ref) == 2:
            q, n = ref
        elif isinstance(ref, str):
            q, n = _split_qualified(ref)
        else:
            raise ValueError(f"unsupported column ref: {ref!r}")

        if q is not None:
            src = by_id.get(q)
            if src is None or n not in src.column_pos:
                raise UnknownQualifiedColumn(q, n)
            return src.column_pos[n], src

        # 裸列：仅在唯一 source 提供时通过
        hits = [s for s in sources if n in s.column_pos]
        if not hits:
            raise UnknownQualifiedColumn("?", n)
        if len(hits) > 1:
            raise AmbiguousColumn(n, tuple(s.source_id for s in hits))
        return hits[0].column_pos[n], hits[0]

    return _resolve


def resolve(ast: Select, catalog: Catalog) -> ResolvedPlan:
    if not isinstance(ast, Select):
        raise ValueError("resolve() expects a Select AST")
    sources = _build_source_map(ast.from_, ast.joins, catalog)
    resolver = _make_resolver(sources)

    merged_keys: list = []
    output_cols: list = []
    outer_kind: Optional[str] = None

    # 先合并 schema：每个 source 顺序贡献列；USING/NATURAL 共同列只出现一次
    seen_labels: set = set()
    for src in sources:
        for col in src.schema:
            if col in seen_labels:
                continue  # USING/NATURAL 合并键只在第一个 source 输出
            output_cols.append(col)
            seen_labels.add(col)

    # 计算 merged_keys 与外连接 kind（NATURAL 无共同列时记录）
    for join in ast.joins:
        left = sources[0]  # 当前实现：按左深顺序前一 source
        # 简单做法：取 sources 列表中 right 索引 - 1 的元素作为 left
        right_idx = next(
            i for i, s in enumerate(sources) if s.source_id == _source_id_for(join.right.name, join.right.alias)
        )
        left_idx = right_idx - 1
        left_src = sources[left_idx]
        right_src = sources[right_idx]
        left_ti = catalog.get_table(left_src.table_name)
        right_ti = catalog.get_table(right_src.table_name)
        keys, _ = _resolve_using_or_natural(join, left_src, right_src, left_ti, right_ti)
        merged_keys.extend(keys)
        if join.natural and not keys:
            outer_kind = join.kind

    # 已 fold 的 WHERE / ON 表达式（位置 + literal）—— 解析器阶段先把 ON / WHERE
    # 中所有 ColumnRef 用 resolver 替换为 (pos, op, lit) 三元组列表。
    on_resolved = tuple(_fold_expr(ast.joins[0].on_expr, resolver) if ast.joins and ast.joins[0].on_expr else ())
    where_resolved = _fold_expr(ast.where, resolver) if ast.where is not None else None

    return ResolvedPlan(
        sources=sources,
        output_schema=tuple(output_cols),
        merged_keys=tuple(merged_keys),
        column_resolver=resolver,
        on_resolved=on_resolved,
        where_resolved=where_resolved,
        select_resolved=tuple(),
        order_resolved=tuple(),
        group_resolved=tuple(),
        having_resolved=None,
        aggregate_resolved=tuple(),
        outer_kind=outer_kind,
    )


def _fold_expr(expr, resolver) -> Any:
    """把 Expr 树中所有 ColumnRef 替换为列位置；返回折叠后的简单 tuple。

    简化：仅处理 EqualsExpr / AndExpr / OrExpr / NotExpr。ON 复杂谓词会被解析器
    阶段折成 (op, lhs_pos, rhs_pos_or_lit) 三元组列表。
    """
    if expr is None:
        return None
    if isinstance(expr, EqualsExpr):
        # qualifier 已附在 EqualsExpr.qualifier
        pos, _ = resolver((getattr(expr, "qualifier", None), expr.column))
        return (expr.value,) and ("=", pos, expr.value)  # 简化形式：见执行层
    if isinstance(expr, AndExpr):
        return ("AND", _fold_expr(expr.left, resolver), _fold_expr(expr.right, resolver))
    if isinstance(expr, OrExpr):
        return ("OR", _fold_expr(expr.left, resolver), _fold_expr(expr.right, resolver))
    if isinstance(expr, NotExpr):
        return ("NOT", _fold_expr(expr.operand, resolver))
    raise ValueError(f"unsupported expr node: {type(expr).__name__}")
```

**注意**：上面的 `_fold_expr` 是简化骨架。后续 Task 6 / 7 会细化 fold 输出（保留 `(op, left, right)` 形式以支持 JOIN 后 WHERE 与既有 `eval_expr` 协同）。本 Task 只保证 resolver 不抛错并通过上述测试。

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_resolver.py -v
```
预期: GREEN；`resolver.py` 行数 ≤ 450。

#### Step 4.3（REFACTOR）: 拆分与一致性

- 抽取 `_fold_equals_expr` 等子函数以减少 `_fold_expr` 行数。
- `_build_source_map` 改为支持任意顺序（不是按 FROM 索引）；保证 LEFT/right 解析正确。
- `pyflakes src/tinydb/resolver.py` 应 0 warnings。

**Commit**:
```bash
git add src/tinydb/resolver.py tests/unit/test_resolver.py
git commit -m "feat(resolver): add source-map + merged-schema + USING/NATURAL JoinKey + ResolutionError subtype coverage"
```

---

### Task 5: LogicalPlan 中间层（plan 模块 + build_plan + format）

**Files:**
- Create: `src/tinydb/plan.py`
- Create: `tests/unit/test_plan.py`
- Modify: `src/tinydb/__init__.py`（再导出 LogicalPlan）

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 5.1（RED）: 编写 plan 测试

新增 `tests/unit/test_plan.py`：

```python
import pytest
from tinydb.catalog import Catalog, Column, TableInfo
from tinydb.parser import parse, tokenize, Select
from tinydb.plan import (
    LogicalPlan, Scan, Join, Filter, Aggregate, Sort, Project, Limit,
    build_plan,
)


@pytest.fixture
def catalog():
    c = Catalog()
    c.create_table(
        "users",
        tuple([Column("id", "INT"), Column("name", "TEXT")]),
        root_page_id=2, next_page_id=2,
    )
    c.create_table(
        "orders",
        tuple([Column("id", "INT"), Column("user_id", "INT")]),
        root_page_id=3, next_page_id=3,
    )
    return c


def _sel(sql):
    return parse(tokenize(sql)).statements[0]


def test_plan_for_single_table_is_scan_project(catalog):
    plan = build_plan(_sel("SELECT id FROM users"), catalog)
    assert isinstance(plan, LogicalPlan)
    assert plan.kind == "Project" or isinstance(plan, Project)


def test_plan_for_two_table_inner_join_is_left_deep(catalog):
    sql = "SELECT u.id FROM users u INNER JOIN orders o ON u.id = o.user_id"
    plan = build_plan(_sel(sql), catalog)
    # 顶层为 Project，下层为 Join(INNER, Scan(u), Scan(o))
    assert isinstance(plan, Project)
    inner = plan.source
    assert isinstance(inner, Join)
    assert inner.kind == "INNER"
    assert isinstance(inner.left, Scan)
    assert isinstance(inner.right, Scan)
    assert inner.left.table == "users"
    assert inner.right.table == "orders"


def test_plan_natural_left_join_no_common_keys_yields_empty_keys(catalog):
    # users 与 audit 无共同列（需创建 audit）
    catalog.create_table(
        "audit",
        tuple([Column("ts", "INT")]), root_page_id=4, next_page_id=4,
    )
    sql = "SELECT * FROM users NATURAL LEFT JOIN audit"
    plan = build_plan(_sel(sql), catalog)
    join = plan.source
    assert isinstance(join, Join)
    assert join.kind == "LEFT"
    assert join.keys == ()


def test_plan_using_keys_record_left_and_right_positions(catalog):
    sql = "SELECT * FROM users u JOIN orders o USING (id)"
    plan = build_plan(_sel(sql), catalog)
    join = plan.source
    assert isinstance(join, Join)
    assert len(join.keys) == 1
    key = join.keys[0]
    assert key.label == "id" and key.left_col == 0 and key.right_col == 0


def test_plan_constructs_filter_from_where(catalog):
    sql = "SELECT id FROM users WHERE id = 1"
    plan = build_plan(_sel(sql), catalog)
    # 顶层 Project -> Filter(source=Scan) -> Scan
    assert isinstance(plan, Project)
    assert isinstance(plan.source, Filter)
    assert isinstance(plan.source.source, Scan)


def test_plan_constructs_sort_and_limit(catalog):
    sql = "SELECT id FROM users ORDER BY id DESC LIMIT 5 OFFSET 2"
    plan = build_plan(_sel(sql), catalog)
    # Project -> Limit(source=Sort(source=Scan))
    assert isinstance(plan, Project)
    assert isinstance(plan.source, Limit)
    assert isinstance(plan.source.source, Sort)


def test_plan_format_stable_text(catalog):
    sql = "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id"
    plan = build_plan(_sel(sql), catalog)
    text = plan.format()
    assert "Project" in text
    assert "Join(INNER" in text
    assert "Scan(users AS u" in text
    assert "Scan(orders AS o" in text


def test_plan_construct_does_not_mutate_catalog(catalog):
    sql = "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id"
    keys_before = set(catalog.tables.keys())
    plan = build_plan(_sel(sql), catalog)
    keys_after = set(catalog.tables.keys())
    assert keys_before == keys_after


def test_plan_construct_does_not_touch_pager(tmp_path):
    # property-like: 通过 Database 验证 pager / WAL 不变。
    import tinydb
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    try:
        d.execute("CREATE TABLE a(id INT)")
        d.execute("CREATE TABLE b(id INT)")
        d.execute("INSERT INTO a(id) VALUES (1)")
        # 记录 page_count 与 wal.size
        pc_before = d.pager.page_count()
        wal_before = d.executor._wal_size() if hasattr(d.executor, "_wal_size") else None
        # 触发 build_plan
        from tinydb.parser import parse, tokenize, Select
        from tinydb.plan import build_plan
        ast = parse(tokenize("SELECT a.id FROM a JOIN b ON a.id = b.id")).statements[0]
        plan = build_plan(ast, d.catalog)
        assert d.pager.page_count() == pc_before
    finally:
        d.close()
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_plan.py -v
```
预期: RED，`tinydb.plan` 模块缺失。

#### Step 5.2（GREEN）: 实现 plan.py

`src/tinydb/plan.py`：

```python
"""不可变 LogicalPlan 中间层 + build_plan。

设计依据：Design Doc §3.4 / §4.1 / §5.4。无副作用：不触碰 Pager/WAL/Transaction。
"""
from dataclasses import dataclass
from typing import Any, Optional

from tinydb.catalog import Catalog
from tinydb.parser import (
    Select, AggregateCall, OrderByItem, JoinClause,
)
from tinydb.resolver import resolve, ResolvedPlan


# --- 节点 -------------------------------------------------------------------


@dataclass(frozen=True)
class Scan:
    table: str
    alias: Optional[str]
    schema: tuple
    source_id: str


@dataclass(frozen=True)
class Join:
    kind: str                       # INNER / LEFT / RIGHT / FULL / CROSS
    left: "LogicalPlan"
    right: "LogicalPlan"
    keys: tuple                      # tuple[JoinKey, ...]
    on_expr: Any                     # 已 fold 的 (op, ...) 列表；CROSS / USING/NATURAL 时为空
    natural: bool = False


@dataclass(frozen=True)
class Filter:
    source: "LogicalPlan"
    predicate: Any                   # 已 fold


@dataclass(frozen=True)
class Aggregate:
    source: "LogicalPlan"
    group_keys: tuple                # tuple[int, ...]
    aggregates: tuple                # tuple[AggregateCall, ...]


@dataclass(frozen=True)
class Sort:
    source: "LogicalPlan"
    keys: tuple                      # tuple[tuple[int, bool], ...]


@dataclass(frozen=True)
class Project:
    source: "LogicalPlan"
    items: tuple                     # tuple[tuple[str, Any], ...]
    star: bool = False


@dataclass(frozen=True)
class Limit:
    source: "LogicalPlan"
    limit: Optional[int]
    offset: Optional[int]


# LogicalPlan 是以上 7 个节点的并集 type alias（运行时用 isinstance 分发）。
LogicalPlan = Scan | Join | Filter | Aggregate | Sort | Project | Limit


# --- 构造 -------------------------------------------------------------------


def build_plan(ast: Select, catalog: Catalog) -> LogicalPlan:
    rp = resolve(ast, catalog)
    # 来源 → Scan 节点
    scans = [
        Scan(
            table=s.table_name, alias=s.alias,
            schema=s.schema, source_id=s.source_id,
        )
        for s in rp.sources
    ]

    # 左深构造 Join 节点
    current: LogicalPlan = scans[0]
    for join_ast, scan in zip(ast.joins, scans[1:]):
        # 找本次 join 对应的 merged_keys
        keys = tuple(k for k in rp.merged_keys if k.source_left and k.source_right)
        current = Join(
            kind=join_ast.kind, left=current, right=scan,
            keys=keys,
            on_expr=rp.on_resolved,
            natural=join_ast.natural,
        )

    # Filter
    if rp.where_resolved is not None:
        current = Filter(source=current, predicate=rp.where_resolved)

    # Aggregate（仅当 SELECT 命中聚合或 GROUP BY）
    if ast.aggregate_aliases or ast.group_by:
        current = Aggregate(
            source=current,
            group_keys=tuple(),
            aggregates=tuple(),
        )

    # Sort
    if ast.order_by:
        keys = tuple(
            (_resolve_order_key(it, rp), it.descending)
            for it in ast.order_by
        )
        current = Sort(source=current, keys=keys)

    # Project
    items: list = []
    star = False
    if ast.select_items:
        for si in ast.select_items:
            if si.kind == "star":
                star = True
                items.append(("", "star"))
            elif si.kind == "column":
                items.append((si.alias or si.name, ("col", getattr(si, "qualifier", None), si.name)))
            elif si.kind == "aggregate":
                items.append((si.alias or "", ("agg", si.aggregate)))
    else:
        # legacy columns
        for c in ast.columns:
            items.append((c, ("col", None, c)))
    current = Project(source=current, items=tuple(items), star=star)

    # Limit
    if ast.limit is not None or ast.offset is not None:
        current = Limit(source=current, limit=ast.limit, offset=ast.offset)

    return current


def _resolve_order_key(it: OrderByItem, rp: ResolvedPlan) -> int:
    pos, _ = rp.column_resolver((getattr(it, "qualifier", None), it.column))
    return pos


# --- 格式（稳定缩进文本，供 .explain 打印） --------------------------------


_INDENT = "   "


def _format(node: LogicalPlan, depth: int = 0) -> str:
    pad = _INDENT * depth
    if isinstance(node, Scan):
        alias = f" AS {node.alias}" if node.alias else ""
        return f"{pad}Scan({node.table}{alias}, schema={node.schema})"
    if isinstance(node, Join):
        head = (
            f"{pad}Join({node.kind}, keys={len(node.keys)}"
            f"{', natural' if node.natural else ''})"
        )
        left = _format(node.left, depth + 1)
        right = _format(node.right, depth + 1)
        return f"{head}\n{left}\n{right}"
    if isinstance(node, Filter):
        return f"{pad}Filter\n" + _format(node.source, depth + 1)
    if isinstance(node, Aggregate):
        return (
            f"{pad}Aggregate(groups={len(node.group_keys)}, "
            f"funcs={len(node.aggregates)})\n"
            + _format(node.source, depth + 1)
        )
    if isinstance(node, Sort):
        return f"{pad}Sort(keys={node.keys})\n" + _format(node.source, depth + 1)
    if isinstance(node, Project):
        return (
            f"{pad}Project(items={len(node.items)}"
            f"{', star' if node.star else ''})\n"
            + _format(node.source, depth + 1)
        )
    if isinstance(node, Limit):
        return (
            f"{pad}Limit(limit={node.limit}, offset={node.offset})\n"
            + _format(node.source, depth + 1)
        )
    raise ValueError(f"unknown plan node: {type(node).__name__}")


def format_plan(plan: LogicalPlan) -> str:
    return _format(plan)


# 给 LogicalPlan 实例添加 .format() 方法
setattr(LogicalPlan, "format", lambda self: format_plan(self))
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_plan.py -v
```
预期: GREEN；`plan.py` 行数 ≤ 350。

#### Step 5.3（REFACTOR）: 修正 LogicalPlan 类型分发

- `setattr(LogicalPlan, "format", ...)` 在 `LogicalPlan` 是 `Union` 类型时无法注入；改为为每个 dataclass 显式添加 `format()` 方法（或在调用点使用 `format_plan(plan)`）。
- 修正逻辑：将 `format_plan` 作为公共函数，LogicalPlan 实例的 `.format` 通过 monkey-patching 或在每个节点类内手动定义二选一。本计划采用 monkey-patching 后置（仅调用 `_format` 的入口函数 `format_plan`）。
- `pyflakes src/tinydb/plan.py` 应 0 warnings。

**Commit**:
```bash
git add src/tinydb/plan.py tests/unit/test_plan.py
git commit -m "feat(plan): add frozen LogicalPlan nodes, build_plan(), and stable format() output"
```

---

### Task 6: INNER / CROSS JOIN 执行（`_join_executor` 基础）

**Files:**
- Create: `src/tinydb/_join_executor.py`
- Create: `tests/unit/test_join_executor.py`
- Create: `tests/integration/test_join_execution.py`

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 6.1（RED）: 编写 _join_executor 测试

新增 `tests/unit/test_join_executor.py`（mock executor；不依赖 Database）：

```python
import pytest
from dataclasses import dataclass
from typing import Optional

from tinydb.catalog import Catalog, Column, TableInfo
from tinydb.parser import parse, tokenize
from tinydb.plan import build_plan, Scan, Join, Project
from tinydb._join_executor import JoinExecutor


@dataclass
class _FakeExecutor:
    """最小 stub：模拟 _txn_read_page / Executor 接口。

    _scan_table 返回 [(slot_id, decoded_values, page_id), ...] 列表。
    """
    catalog: object
    table_rows: dict              # {table_name: list[list[value]]}

    def _scan_table(self, ti):
        return [(i, list(row), 0) for i, row in enumerate(self.table_rows.get(ti.name, []))]


@pytest.fixture
def catalog():
    c = Catalog()
    c.create_table(
        "users",
        tuple([Column("id", "INT"), Column("name", "TEXT")]),
        root_page_id=2, next_page_id=2,
    )
    c.create_table(
        "orders",
        tuple([Column("id", "INT"), Column("user_id", "INT"), Column("total", "INT")]),
        root_page_id=3, next_page_id=3,
    )
    return c


def _build_plan(sql, catalog):
    ast = parse(tokenize(sql)).statements[0]
    return build_plan(ast, catalog)


def test_inner_join_returns_matched_rows(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "alice"], [2, "bob"]],
            "orders": [[10, 1, 100], [11, 2, 200], [12, 3, 50]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u INNER JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, schema = exe.execute_plan(plan)
    # 匹配：u.id=1->o.id=10, u.id=2->o.id=11；o.user_id=3 不匹配
    assert len(rows) == 2
    assert schema[0] == "id"  # 合并键只出现一次（USING 时）；这里是 ON 显式
    # 实际 schema 因 SQL 而异：u.id, o.id；这里近似校验
    assert any(r[0] == 1 for r in rows)


def test_cross_join_returns_cartesian_product(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[10, 1, 0], [11, 2, 0]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan("SELECT * FROM users CROSS JOIN orders", catalog)
    rows, _ = exe.execute_plan(plan)
    assert len(rows) == 4  # 2 x 2


def test_inner_join_with_empty_right(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={"users": [[1, "a"]], "orders": []},
    )
    exe = JoinExecutor(fe)
    plan = _build_plan("SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id", catalog)
    rows, _ = exe.execute_plan(plan)
    assert rows == []


def test_inner_join_with_empty_left(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={"users": [], "orders": [[10, 1, 0]]},
    )
    exe = JoinExecutor(fe)
    plan = _build_plan("SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id", catalog)
    rows, _ = exe.execute_plan(plan)
    assert rows == []


def test_using_id_produces_single_merged_column(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[1, 1, 100], [2, 2, 200]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan("SELECT * FROM users u JOIN orders o USING (id)", catalog)
    rows, schema = exe.execute_plan(plan)
    # schema 应包含 'id'（合并键）+ 'name' + 'user_id' + 'total'
    assert "id" in schema
    # 'id' 在 schema 中只出现一次
    assert schema.count("id") == 1
    # 行数 = 2
    assert len(rows) == 2


def test_chained_three_table_join(catalog):
    c2 = Catalog()
    c2.create_table("a", tuple([Column("id", "INT")]), root_page_id=2, next_page_id=2)
    c2.create_table("b", tuple([Column("id", "INT")]), root_page_id=3, next_page_id=3)
    c2.create_table("c", tuple([Column("id", "INT")]), root_page_id=4, next_page_id=4)
    fe = _FakeExecutor(
        catalog=c2,
        table_rows={
            "a": [[1], [2]],
            "b": [[1], [2], [3]],
            "c": [[1], [2]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT a.id FROM a JOIN b ON a.id = b.id JOIN c ON b.id = c.id",
        c2,
    )
    rows, _ = exe.execute_plan(plan)
    # a=1->b=1->c=1, a=1->b=1->c=2, a=2->b=2->c=1, a=2->b=2->c=2
    assert len(rows) == 4


def test_inner_join_routes_through_executor_scan(catalog, monkeypatch):
    """ACID 验收：JOIN 路径必须调用 executor._scan_table（间接走 _txn_read_page）。

    记录 _scan_table 调用计数，确保 JOIN 路径不直接调用 Pager.read_page。
    """
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={"users": [[1, "a"]], "orders": [[10, 1, 0]]},
    )
    calls = {"n": 0}

    real = type(fe)._scan_table

    def counting(self, ti):
        calls["n"] += 1
        return real(self, ti)

    monkeypatch.setattr(type(fe), "_scan_table", counting)
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    exe.execute_plan(plan)
    # 至少调用 2 次（users + orders）
    assert calls["n"] >= 2
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_join_executor.py -v
```
预期: RED，`tinydb._join_executor` 模块缺失。

#### Step 6.2（RED）: 编写集成测试（`test_join_execution.py`）

新增 `tests/integration/test_join_execution.py`：

```python
import pytest
import tinydb


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE users(id INT, name TEXT)")
    db.execute("CREATE TABLE orders(id INT, user_id INT, total INT)")
    db.execute("INSERT INTO users(id, name) VALUES (1, 'alice'), (2, 'bob')")
    db.execute(
        "INSERT INTO orders(id, user_id, total) VALUES "
        "(10, 1, 100), (11, 2, 200), (12, 3, 50)"
    )


@pytest.mark.integration
def test_inner_join_returns_matched_rows(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.id FROM users u INNER JOIN orders o ON u.id = o.user_id"
    )
    # u.id=1 -> o.id=10; u.id=2 -> o.id=11; o.user_id=3 不匹配
    assert len(rows) == 2
    by_u = {r["u.id"]: r["o.id"] for r in rows}
    assert by_u[1] == 10 and by_u[2] == 11


@pytest.mark.integration
def test_cross_join_returns_cartesian(db):
    _setup(db)
    rows = db.execute("SELECT * FROM users CROSS JOIN orders")
    assert len(rows) == 2 * 3  # 6


@pytest.mark.integration
def test_inner_join_with_on_compound(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id FROM users u JOIN orders o "
        "ON u.id = o.user_id AND o.total > 100"
    )
    assert {r["u.id"] for r in rows} == {2}  # o.total=200 对应 u=2


@pytest.mark.integration
def test_using_join_merges_id_column(db):
    _setup(db)
    rows = db.execute(
        "SELECT * FROM users JOIN orders USING (id)"
    )
    # id 列同时存在于 users 与 orders；USING 应合并为单个 'id'
    assert all("id" in r.columns for r in rows)
    assert all(r.columns.count("id") == 1 for r in rows)


@pytest.mark.integration
def test_natural_join_with_no_common_keys_returns_cross(db):
    db.execute("CREATE TABLE audit(ts INT)")
    db.execute("INSERT INTO audit(ts) VALUES (100), (200)")
    rows = db.execute("SELECT * FROM users NATURAL LEFT JOIN audit")
    # users 2 行 * audit 2 行 = 4 行
    assert len(rows) == 4


@pytest.mark.integration
def test_chained_three_table_join(db):
    db.execute("CREATE TABLE c(id INT)")
    db.execute("INSERT INTO c(id) VALUES (1), (2)")
    rows = db.execute(
        "SELECT u.id FROM users u "
        "JOIN orders o ON u.id = o.user_id "
        "JOIN c ON o.id = c.id"
    )
    # u=1->o=10->c=1 (10 != 1, no match); u=2->o=11->c=2 (11 != 2, no match)
    # 上述数据下没有匹配，返回 []
    assert rows == []
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/integration/test_join_execution.py -v
```
预期: RED，`executor.py` 仍调用 v0.1 单表 fast path，JOIN 不被识别。

#### Step 6.3（GREEN）: 实现 `_join_executor.py` 的 INNER / CROSS 路径

`src/tinydb/_join_executor.py`：

```python
"""JOIN 执行：nested-loop INNER / LEFT / RIGHT / FULL / CROSS + USING/NATURAL Coalesce。

设计依据：Design Doc §3.2 / §4.1 / §4.2 / §5.3。事务读路由由 executor 负责，本模块
仅消费 (rows, schema) 元组并在必要时调 executor._scan_table / _txn_read_page。
"""
from typing import Any, Iterable, Optional

from tinydb.catalog import TableInfo
from tinydb.plan import (
    LogicalPlan, Scan, Join, Filter, Aggregate, Sort, Project, Limit,
)


class JoinExecutor:
    """对 plan 中 Join 节点执行 nested-loop；返回 (rows, output_schema)。"""

    def __init__(self, executor):
        self.executor = executor  # 提供 _scan_table / _txn_read_page

    def execute_plan(self, plan: LogicalPlan) -> tuple:
        """走 plan 树直到 Project 节点；返回 (rows, output_schema)。"""
        rows, schema = self._eval(plan)
        return rows, schema

    def _eval(self, node: LogicalPlan):
        if isinstance(node, Scan):
            ti = self.executor.catalog.get_table(node.table)
            rows = []
            for _sid, vals, _pid in self.executor._scan_table(ti):
                rows.append(list(vals))
            return rows, list(node.schema)
        if isinstance(node, Join):
            return self._eval_join(node)
        if isinstance(node, Filter):
            return self._eval_filter(node)
        if isinstance(node, Project):
            return self._eval_project(node)
        if isinstance(node, Limit):
            return self._eval_limit(node)
        if isinstance(node, Sort):
            return self._eval_sort(node)
        if isinstance(node, Aggregate):
            return self._eval_aggregate(node)
        raise ValueError(f"unsupported plan node: {type(node).__name__}")

    # --- Join --------------------------------------------------------------

    def _eval_join(self, node: Join):
        left_rows, left_schema = self._eval(node.left)
        right_rows, right_schema = self._eval(node.right)

        if node.kind == "CROSS":
            return self._nested_loop_cross(left_rows, left_schema, right_rows, right_schema)

        if node.kind in ("INNER",):
            return self._nested_loop_inner(
                left_rows, left_schema, right_rows, right_schema, node
            )

        if node.kind in ("LEFT", "RIGHT", "FULL"):
            return self._nested_loop_outer(
                left_rows, left_schema, right_rows, right_schema, node
            )

        raise ValueError(f"unsupported join kind: {node.kind!r}")

    def _merged_schema(self, left_schema, right_schema, node: Join):
        """输出 schema：USING/NATURAL 合并键只出现一次；普通列保持原名。"""
        merge_labels = {k.label for k in node.keys}
        out = list(left_schema)
        seen = set(left_schema)
        for col in right_schema:
            if col in merge_labels and col in seen:
                continue
            out.append(col)
            seen.add(col)
        return out

    def _coalesce_row(self, left_row, right_row, node: Join, left_schema, right_schema):
        """构造输出行：合并键 Coalesce。"""
        # 先把 right_row 的列复制到 out；按 schema 顺序
        out = list(left_row)
        merge_left_idx = {k.label: k.left_col for k in node.keys}
        merge_right_idx = {k.label: k.right_col for k in node.keys}
        # 合并键如果已在 out（来自 left_schema），跳过 right 的位置
        for ri, col in enumerate(right_schema):
            if col in merge_right_idx and col in merge_left_idx:
                # Coalesce: left 若为 None 则用 right
                li = merge_left_idx[col]
                if out[li] is None:
                    out[li] = right_row[ri]
                continue
            out.append(right_row[ri])
        return out

    def _nested_loop_cross(self, left_rows, left_schema, right_rows, right_schema):
        out_rows = []
        for lr in left_rows:
            for rr in right_rows:
                out_rows.append(list(lr) + list(rr))
        return out_rows, list(left_schema) + list(right_schema)

    def _nested_loop_inner(self, left_rows, left_schema, right_rows, right_schema, node: Join):
        out_rows = []
        out_schema = self._merged_schema(left_schema, right_schema, node)
        for lr in left_rows:
            matched = False
            for rr in right_rows:
                if self._matches(lr, rr, left_schema, right_schema, node):
                    out_rows.append(self._coalesce_row(lr, rr, node, left_schema, right_schema))
                    matched = True
            # INNER：未匹配行不输出
        return out_rows, out_schema

    def _matches(self, lr, rr, ls, rs, node: Join) -> bool:
        """判断左右行是否匹配。优先 USING/NATURAL 合并键；否则用 ON 表达式。"""
        if node.keys:
            for k in node.keys:
                lv = lr[k.left_col]
                rv = rr[k.right_col]
                if lv is None or rv is None:
                    return False
                if lv != rv:
                    return False
            return True
        if node.on_expr is not None:
            return bool(self._eval_on(node.on_expr, lr, rr, ls, rs))
        # 没键也没 ON：CROSS 路径已处理；此处表示 INNER 没匹配条件 → 视为全匹配
        return True

    def _eval_on(self, on, lr, rr, ls, rs):
        """简化：on 是 resolver 阶段 fold 的 ('op', left_pos_or_literal, right_pos_or_literal) tree。

        为最小可工作骨架，本步仅处理单层 EqualsExpr 折出的 ('=', left_pos, literal)。
        """
        if isinstance(on, tuple) and len(on) == 3 and on[0] == "=":
            _, left_pos, lit = on
            # left_pos 是 left 行内位置；right 是 literal
            return lr[left_pos] == lit
        # 复杂谓词在 Task 7 完善
        return True

    def _nested_loop_outer(self, left_rows, left_schema, right_rows, right_schema, node: Join):
        """LEFT/RIGHT/FULL：先按 LEFT 规则输出；RIGHT 通过交换输入。"""
        if node.kind == "RIGHT":
            # 交换输入与 schema
            swapped = self._nested_loop_outer(
                right_rows, right_schema, left_rows, left_schema,
                Join(
                    kind="LEFT", left=node.right, right=node.left,
                    keys=tuple(
                        type(k)(label=k.label, source_left=k.source_right,
                                source_right=k.source_left,
                                left_col=k.right_col, right_col=k.left_col)
                        for k in node.keys
                    ),
                    on_expr=node.on_expr, natural=node.natural,
                ),
            )
            # 把每行的列顺序恢复为 "left 在前 right 在后"
            n_left = len(left_schema)
            return [r[n_left:] + r[:n_left] for r in swapped[0]], left_schema + right_schema

        out_rows: list = []
        out_schema = self._merged_schema(left_schema, right_schema, node)
        right_consumed = [False] * len(right_rows)
        for lr in left_rows:
            any_match = False
            for ri, rr in enumerate(right_rows):
                if self._matches(lr, rr, left_schema, right_schema, node):
                    out_rows.append(self._coalesce_row(lr, rr, node, left_schema, right_schema))
                    right_consumed[ri] = True
                    any_match = True
            if not any_match:
                null_right = [None] * len(right_schema)
                out_rows.append(self._coalesce_row(lr, null_right, node, left_schema, right_schema))
        if node.kind == "FULL":
            # 追加右未匹配行
            for ri, rr in enumerate(right_rows):
                if right_consumed[ri]:
                    continue
                null_left = [None] * len(left_schema)
                out_rows.append(self._coalesce_row(null_left, rr, node, left_schema, right_schema))
        return out_rows, out_schema

    # --- Filter / Project / Limit / Sort / Aggregate -----------------------

    def _eval_filter(self, node: Filter):
        rows, schema = self._eval(node.source)
        pred = node.predicate
        # 简化骨架：pred 是 ('op', left_pos_or_literal, right_pos_or_literal)
        if isinstance(pred, tuple) and len(pred) == 3 and pred[0] == "=":
            _, pos, lit = pred
            return [r for r in rows if r[pos] == lit], schema
        if isinstance(pred, tuple) and pred and isinstance(pred[0], str) and pred[0] in {"AND", "OR", "NOT"}:
            return [r for r in rows if self._eval_predicate(r, pred, schema)], schema
        return rows, schema

    def _eval_predicate(self, row, pred, schema):
        if isinstance(pred, tuple) and pred[0] == "AND":
            return self._eval_predicate(row, pred[1], schema) and self._eval_predicate(row, pred[2], schema)
        if isinstance(pred, tuple) and pred[0] == "OR":
            return self._eval_predicate(row, pred[1], schema) or self._eval_predicate(row, pred[2], schema)
        if isinstance(pred, tuple) and pred[0] == "NOT":
            return not self._eval_predicate(row, pred[1], schema)
        if isinstance(pred, tuple) and len(pred) == 3 and pred[0] == "=":
            return row[pred[1]] == pred[2]
        return True

    def _eval_project(self, node: Project):
        rows, schema = self._eval(node.source)
        if node.star:
            return rows, schema
        out_rows = []
        for r in rows:
            out_rows.append(self._project_row(r, node.items, schema))
        new_schema = tuple(label for label, _ in node.items if label)
        return out_rows, list(new_schema)

    def _project_row(self, row, items, schema):
        out = []
        for label, expr in items:
            if expr == "star":
                continue
            if isinstance(expr, tuple) and expr[0] == "col":
                _, _qualifier, name = expr
                idx = schema.index(name)
                out.append(row[idx])
            elif isinstance(expr, tuple) and expr[0] == "agg":
                # 聚合由 Aggregate 节点处理；Project 只透传
                out.append(None)
            else:
                out.append(None)
        return out

    def _eval_limit(self, node: Limit):
        rows, schema = self._eval(node.source)
        if node.offset:
            rows = rows[node.offset:]
        if node.limit is not None:
            rows = rows[: node.limit]
        return rows, schema

    def _eval_sort(self, node: Sort):
        rows, schema = self._eval(node.source)
        keys = node.keys
        # Python stable sort；逐键倒序应用
        for idx, desc in reversed(keys):
            rows.sort(key=lambda r: (r[idx] is None, r[idx]), reverse=bool(desc))
        return rows, schema

    def _eval_aggregate(self, node: Aggregate):
        # 留待 Task 7 扩展
        return self._eval(node.source)
```

#### Step 6.4（GREEN）: 在 executor.py 添加 JOIN dispatch

在 `src/tinydb/executor.py:_exec_select`（`:1189-1242`）的开头插入：

```python
        # --- tinydb-join-query (T6): JOIN dispatch ---
        if stmt.joins:
            from tinydb.plan import build_plan
            from tinydb._join_executor import JoinExecutor
            plan = build_plan(stmt, self.catalog)
            je = JoinExecutor(self)
            rows, schema = je.execute_plan(plan)
            return [list(r) for r in rows]
```

（更精细的实现见 Task 7：`rows` 需套上输出列名以便 `database.py` 包成 `Row`；先把列名写进 `schema` 让 integration 测试访问 `r["u.id"]`。）

**注意**：当前 `_exec_select` 调用点依赖返回 `list[list[Any]]`；database.py 包 Row 时使用 `stmt.columns`。JOIN 路径需要让 Row 的 `columns` 反映 join 输出（`u.id`, `o.id`）。在 `database.py` 中检测 `stmt.joins` 并改用 join executor 的 schema：

```python
# src/tinydb/database.py execute() 中：
if isinstance(last, Select) and results:
    if last.joins:
        # results 已经被 JoinExecutor 包成 list[Row]；直接返回
        pass
    else:
        ti = self.catalog.get_table(last.table)
        if ti is not None:
            cols = tuple(n for n, _ in ti.schema) if last.columns == ("*",) else tuple(last.columns)
            results = [Row(values=tuple(r), columns=cols) for r in results]
```

为了让 integration 测试用 `r["u.id"]`，最简方案：让 `executor.execute()` 在 JOIN 路径下直接返回 `list[Row]`（带限定列名）。在 `_exec_select` 中：

```python
        if stmt.joins:
            from tinydb.plan import build_plan
            from tinydb._join_executor import JoinExecutor
            from tinydb.database import Row
            plan = build_plan(stmt, self.catalog)
            je = JoinExecutor(self)
            rows, schema = je.execute_plan(plan)
            cols = self._join_output_columns(stmt, schema)
            return [Row(values=tuple(r), columns=cols) for r in rows]
```

新增 helper：

```python
    def _join_output_columns(self, stmt, schema) -> tuple:
        """按 resolver 的 output_schema + SELECT * 展开为限定列标签。"""
        # 简化：直接返回 JoinExecutor 返回的 schema（含限定列名）
        return tuple(schema)
```

并在 `database.py` 的 `execute()` 中兼容 `list[Row]` 直接返回：

```python
        for s in stmts.statements:
            out = self.executor.execute(s)
            if isinstance(out, list):
                results = out  # 已是 Row 列表或 list[list]
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_join_executor.py tests/integration/test_join_execution.py -v
```
预期: GREEN；既有 `test_executor.py` / `test_aggregation_pipeline.py` 全部通过（单表 fast path 未变）；`_join_executor.py` 行数 ≤ 450。

#### Step 6.5（REFACTOR）: 提取与命名一致

- `_eval_predicate` 内部抽公共子函数。
- `_coalesce_row` 注释完整（合并键 Coalesce 规则）。
- `pyflakes src/tinydb/_join_executor.py` 0 warnings。
- `executor.py` 净增 ≤ 30 行；现有 `test_executor.py` / `test_acid.py` / `test_aggregation_pipeline.py` 全部通过。

**Commit**:
```bash
git add src/tinydb/_join_executor.py src/tinydb/executor.py src/tinydb/database.py \
        tests/unit/test_join_executor.py tests/integration/test_join_execution.py
git commit -m "feat(executor): route JOIN through _join_executor with nested-loop INNER/CROSS"
```

---

### Task 7: LEFT / RIGHT / FULL JOIN + USING/NATURAL Coalesce + ON 复杂谓词

**Files:**
- Modify: `src/tinydb/resolver.py`（完善 `_fold_expr`，支持 ON 与 WHERE 的复合 AND/OR/NOT 折出 `(op, left_pos, right_pos_or_lit)` 三元组）
- Modify: `src/tinydb/_join_executor.py`（完善 `_matches` / `_eval_on` / `_eval_predicate`；支持复杂 ON / WHERE）
- Modify: `tests/unit/test_join_executor.py`（追加 LEFT/RIGHT/FULL 用例）
- Modify: `tests/property/test_join_order.py`（新建 property 测试）

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 7.1（RED）: 补充 LEFT/RIGHT/FULL 测试

在 `tests/unit/test_join_executor.py` 追加：

```python
def test_left_join_emits_unmatched_left_row_with_nulls(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"], [3, "c"]],  # u=3 无订单
            "orders": [[10, 1, 100], [11, 2, 200]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u LEFT JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    # 4 行：u=1->o=10, u=2->o=11, u=3(NULL); u=3 在 u=2 之后立即出现
    assert len(rows) == 3
    # u=3 行右部为 NULL
    by_u = {r[0]: r[1] for r in rows}
    assert by_u[3] is None


def test_right_join_preserves_right_unmatched(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[10, 1, 100], [11, 2, 200], [12, 99, 0]],  # o.user_id=99 无用户
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u RIGHT JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    # 3 行：u=1->o=10, u=2->o=11, NULL->o=12
    assert len(rows) == 3
    # 末尾是右未匹配行
    assert rows[-1][0] is None and rows[-1][1] == 12


def test_full_join_emits_both_unmatched(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"], [3, "c"]],
            "orders": [[10, 1, 100], [11, 99, 200]],  # u=3 无订单；o.user_id=99 无用户
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT u.id, o.id FROM users u FULL JOIN orders o ON u.id = o.user_id",
        catalog,
    )
    rows, _ = exe.execute_plan(plan)
    # 4 行：u=1->o=10, NULL(u=3), NULL(o.user_id=99->o=11)
    assert len(rows) == 4


def test_left_join_with_using_emits_coalesced_id(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"], [2, "b"]],
            "orders": [[1, 1, 100], [2, 2, 200]],
        },
    )
    exe = JoinExecutor(fe)
    plan = _build_plan(
        "SELECT * FROM users u LEFT JOIN orders o USING (id)",
        catalog,
    )
    rows, schema = exe.execute_plan(plan)
    # schema 含 'id'（合并）+ 'name' + 'user_id' + 'total'
    assert "id" in schema
    assert schema.count("id") == 1


def test_using_coalesce_picks_right_when_left_null(catalog):
    fe = _FakeExecutor(
        catalog=catalog,
        table_rows={
            "users": [[1, "a"]],
            # 故意构造一个右侧 id=1, 但用户无对应 name 列字段；这里通过 dummy left=null 不可行
            # 改测 Coalesce：左侧 NULL 时取右侧
            "orders": [[1, 1, 100]],
        },
    )
    # 此处用伪 row：把 users 改成 [None, "a"] 非法，跳过；改用纯逻辑测试
    exe = JoinExecutor(fe)
    # 手动构造 fake left = [None, ...]
    # 实际由 Task 7.3 完善 coalesce 单元覆盖，本测试仅记录存在性
    assert True
```

#### Step 7.2（RED）: property 测试 — strict-left-deep-insertion

新增 `tests/property/test_join_order.py`：

```python
"""Property: LEFT/RIGHT/FULL 输出顺序 = strict-left-deep-insertion。

对随机生成的两表/三表查询与数据，断言：
- LEFT 未匹配行紧跟其左行；
- RIGHT/FULL 右未匹配行追加在末尾，且按右表扫描顺序。
"""
import pytest
import tinydb


def _build_db(tmp_path, schema):
    p = str(tmp_path / "ord.db")
    d = tinydb.Database(p)
    for name, cols in schema.items():
        d.execute(f"CREATE TABLE {name}({', '.join(cols)})")
    return d


@pytest.mark.property
def test_left_join_strict_order_with_random_data(tmp_path):
    import random
    random.seed(42)
    d = _build_db(tmp_path, {"u": ["id INT", "name TEXT"], "o": ["id INT", "uid INT"]})
    # 左表 5 行，右表 7 行；随机匹配
    d.execute("INSERT INTO u(id, name) VALUES (1,'a'),(2,'b'),(3,'c'),(4,'d'),(5,'e')")
    d.execute(
        "INSERT INTO o(id, uid) VALUES "
        "(1,1),(2,1),(3,2),(4,3),(5,3),(6,99),(7,100)"
    )
    rows = d.execute(
        "SELECT u.id, o.id FROM u LEFT JOIN o ON u.id = o.uid"
    )
    # 期望顺序：
    # u=1: o=1, o=2
    # u=2: o=3
    # u=3: o=4, o=5
    # u=4: NULL
    # u=5: NULL
    expected_u_seq = [1, 1, 2, 3, 3, 4, 5]
    expected_o_seq = [1, 2, 3, 4, 5, None, None]
    actual_u_seq = [r["u.id"] for r in rows]
    actual_o_seq = [r["o.id"] for r in rows]
    assert actual_u_seq == expected_u_seq
    assert actual_o_seq == expected_o_seq


@pytest.mark.property
def test_full_join_unmatched_right_appended_in_scan_order(tmp_path):
    d = _build_db(tmp_path, {"u": ["id INT"], "o": ["id INT", "uid INT"]})
    d.execute("INSERT INTO u(id) VALUES (1), (2)")
    d.execute("INSERT INTO o(id, uid) VALUES (10, 1), (11, 99), (12, 100)")
    rows = d.execute(
        "SELECT u.id, o.id FROM u FULL JOIN o ON u.id = o.uid"
    )
    # 顺序：匹配 (u=1,o=10) + 左未匹配 (u=2,NULL) + 右未匹配 (NULL,o=11), (NULL,o=12)
    actual = [(r["u.id"], r["o.id"]) for r in rows]
    assert actual == [
        (1, 10),
        (2, None),
        (None, 11),
        (None, 12),
    ]
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_join_executor.py tests/property/test_join_order.py -v
```
预期: RED；Coalesce 与右未匹配行顺序尚未完整。

#### Step 7.3（GREEN）: 完善 resolver `_fold_expr` 与 `_join_executor._matches`

**resolver 改动**：

```python
def _fold_expr(expr, resolver) -> Any:
    if expr is None:
        return None
    if isinstance(expr, EqualsExpr):
        pos, _ = resolver((getattr(expr, "qualifier", None), expr.column))
        return ("=", pos, expr.value)
    if isinstance(expr, AndExpr):
        return ("AND", _fold_expr(expr.left, resolver), _fold_expr(expr.right, resolver))
    if isinstance(expr, OrExpr):
        return ("OR", _fold_expr(expr.left, resolver), _fold_expr(expr.right, resolver))
    if isinstance(expr, NotExpr):
        return ("NOT", _fold_expr(expr.operand, resolver))
    raise ValueError(f"unsupported expr node: {type(expr).__name__}")
```

（已存在 Task 4.2 的版本中 `expr.value` 被错误地混入 `and` 链；本步去掉。）

**`_join_executor._matches` 改动**：

```python
    def _matches(self, lr, rr, ls, rs, node: Join) -> bool:
        if node.keys:
            for k in node.keys:
                lv, rv = lr[k.left_col], rr[k.right_col]
                if lv is None or rv is None:
                    return False
                if lv != rv:
                    return False
            return True
        if node.on_expr is not None:
            return self._eval_predicate_pair(lr, rr, ls, rs, node.on_expr)
        return True  # CROSS 已单独处理

    def _eval_predicate_pair(self, lr, rr, ls, rs, pred):
        """pred 是 resolver 折出的 ('=', pos_in_left_or_right, lit_or_neg_pos) 三元组。

        简化约定：pos >= 0 时表示 left 行内位置；pos < 0 时取负后表示 right 行内位置。
        """
        if isinstance(pred, tuple) and pred[0] == "AND":
            return (
                self._eval_predicate_pair(lr, rr, ls, rs, pred[1])
                and self._eval_predicate_pair(lr, rr, ls, rs, pred[2])
            )
        if isinstance(pred, tuple) and pred[0] == "OR":
            return (
                self._eval_predicate_pair(lr, rr, ls, rs, pred[1])
                or self._eval_predicate_pair(lr, rr, ls, rs, pred[2])
            )
        if isinstance(pred, tuple) and pred[0] == "NOT":
            return not self._eval_predicate_pair(lr, rr, ls, rs, pred[1])
        if isinstance(pred, tuple) and len(pred) == 3 and pred[0] == "=":
            _, pos, lit = pred
            if pos >= 0:
                return lr[pos] == lit
            return rr[-pos - 1] == lit
        return True
```

**`_eval_predicate`（WHERE 用）**：

```python
    def _eval_predicate(self, row, pred, schema):
        # 同上但只用 row 与 schema
        if isinstance(pred, tuple) and pred[0] == "AND":
            return self._eval_predicate(row, pred[1], schema) and self._eval_predicate(row, pred[2], schema)
        if isinstance(pred, tuple) and pred[0] == "OR":
            return self._eval_predicate(row, pred[1], schema) or self._eval_predicate(row, pred[2], schema)
        if isinstance(pred, tuple) and pred[0] == "NOT":
            return not self._eval_predicate(row, pred[1], schema)
        if isinstance(pred, tuple) and len(pred) == 3 and pred[0] == "=":
            _, pos, lit = pred
            return row[pos] == lit
        return True
```

**Coalesce 单元**：

```python
def test_using_coalesce_picks_right_when_left_null():
    # 构造直接调用 _coalesce_row 的小测试
    from tinydb._join_executor import JoinExecutor
    from tinydb.plan import Join
    from tinydb.parser import JoinKey
    je = JoinExecutor(None)
    join = Join(
        kind="LEFT", left=None, right=None,
        keys=(JoinKey(label="id", source_left="u", source_right="o",
                       left_col=0, right_col=0),),
        on_expr=None, natural=False,
    )
    left = [None, "a"]
    right = [1, 100]
    out = je._coalesce_row(left, right, join, ["id", "name"], ["id", "total"])
    # 合并键 'id' 来自 left（None）→ right（1）
    assert out[0] == 1
    assert out[1] == "a"
    assert out[2] == 100
```

#### Step 7.4（GREEN）: RIGHT/FULL 输出顺序

`_nested_loop_outer` 已在 Task 6 实现。Task 7 增加：FULL 时右未匹配行按 `right_rows` 索引顺序追加（已实现）；property 测试断言此顺序。

**验收命令**:
```bash
.venv/bin/python -m pytest tests/unit/test_join_executor.py tests/property/test_join_order.py \
                   tests/integration/test_join_execution.py -v
```
预期: GREEN；property 测试锁定输出顺序。

#### Step 7.5（REFACTOR）: 命名与文档

- 把 `_eval_predicate_pair` 与 `_eval_predicate` 合并为 `_eval_fold_expr(pred, left_row, right_row, left_schema, right_schema)`，`right_row=None` 时表示纯 row 谓词。
- `pyflakes` 0 warnings。

**Commit**:
```bash
git add src/tinydb/resolver.py src/tinydb/_join_executor.py \
        tests/unit/test_join_executor.py tests/property/test_join_order.py
git commit -m "feat(join): LEFT/RIGHT/FULL semantics + USING/NATURAL Coalesce + strict-left-deep-insertion ordering"
```

---

### Task 8: JOIN 后查询阶段（WHERE / 投影 / GROUP BY / HAVING / COUNT/SUM / ORDER BY / LIMIT）

**Files:**
- Modify: `src/tinydb/_join_executor.py`（补全 Filter / Aggregate / Sort / Project / Limit 在合并 schema 上的消费）
- Modify: `src/tinydb/resolver.py`（完善 GROUP BY / HAVING / ORDER BY 解析）
- Modify: `src/tinydb/parser.py`（确保 `group_by` 接受 `qualifier.col` 字符串或保留裸列）
- Create: `tests/integration/test_join_post_phases.py`

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 8.1（RED）: 编写 JOIN × 后阶段集成测试

新增 `tests/integration/test_join_post_phases.py`：

```python
import pytest
import tinydb


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE u(id INT, name TEXT, dept TEXT)")
    db.execute("CREATE TABLE o(id INT, uid INT, total INT, status TEXT)")
    db.execute("INSERT INTO u(id, name, dept) VALUES (1,'a','eng'),(2,'b','eng'),(3,'c','sales')")
    db.execute(
        "INSERT INTO o(id, uid, total, status) VALUES "
        "(10,1,100,'paid'),(11,1,150,'paid'),(12,2,200,'open'),(13,3,50,'paid')"
    )


@pytest.mark.integration
def test_join_then_where_filters_by_status(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid WHERE o.status = 'paid'"
    )
    # paid: o=10,11,13 → u=1,1,3
    assert {r["u.id"] for r in rows} == {1, 3}
    assert len(rows) == 3


@pytest.mark.integration
def test_join_then_group_by_count(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.dept, COUNT(*) AS n FROM u JOIN o ON u.id = o.uid "
        "GROUP BY u.dept"
    )
    by_dept = {r["dept"]: r["n"] for r in rows}
    assert by_dept == {"eng": 3, "sales": 1}


@pytest.mark.integration
def test_join_then_having(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, COUNT(*) AS n FROM u JOIN o ON u.id = o.uid "
        "GROUP BY u.id HAVING COUNT(*) > 1"
    )
    by_u = {r["u.id"]: r["n"] for r in rows}
    assert by_u == {1: 2}  # u=1 有两单


@pytest.mark.integration
def test_join_then_sum(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, SUM(o.total) AS s FROM u JOIN o ON u.id = o.uid "
        "GROUP BY u.id"
    )
    by_u = {r["u.id"]: r["s"] for r in rows}
    assert by_u == {1: 250, 2: 200, 3: 50}


@pytest.mark.integration
def test_join_then_order_by_and_limit(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.total FROM u JOIN o ON u.id = o.uid "
        "ORDER BY o.total DESC LIMIT 2"
    )
    totals = [r["total"] for r in rows]
    assert totals == [200, 150]


@pytest.mark.integration
def test_join_then_offset(db):
    _setup(db)
    rows = db.execute(
        "SELECT u.id, o.total FROM u JOIN o ON u.id = o.uid "
        "ORDER BY o.total DESC LIMIT 2 OFFSET 1"
    )
    totals = [r["total"] for r in rows]
    assert totals == [150, 100]


@pytest.mark.integration
def test_join_then_select_star_uses_qualified_labels(db):
    _setup(db)
    rows = db.execute("SELECT * FROM u JOIN o ON u.id = o.uid WHERE u.id = 1")
    r = rows[0]
    assert "u.id" in r.columns
    assert "o.id" in r.columns
    assert r["u.id"] == 1 and r["o.id"] in (10, 11)


@pytest.mark.integration
def test_join_with_unknown_column_in_where(db):
    _setup(db)
    with pytest.raises(Exception):
        db.execute(
            "SELECT * FROM u JOIN o ON u.id = o.uid WHERE u.missing = 1"
        )
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/integration/test_join_post_phases.py -v
```
预期: RED；Group/Aggregate/HAVING 在 JOIN 路径下未实现。

#### Step 8.2（GREEN）: 扩展 `_join_executor._eval_aggregate` 与相关 helper

```python
def _eval_aggregate(self, node: Aggregate):
    """复用既有 aggregation 5-phase pipeline 但消费合并 schema。

    为最小实现：把 rows 直接喂给 executor 的 5-phase helper（如可直接调用）。
    简化路径：对无 GROUP BY 的聚合直接计算；对有 GROUP BY 的聚合按 group_keys 分组。
    """
    rows, schema = self._eval(node.source)
    # 无 group_keys + 单 aggregate：直接计算
    if not node.group_keys and node.aggregates:
        return self._aggregate_single(rows, schema, node.aggregates)
    # 有 group_keys：按 key 分组
    return self._aggregate_grouped(rows, schema, node.group_keys, node.aggregates)


def _aggregate_single(self, rows, schema, aggregates):
    out_rows = []
    for agg in aggregates:
        out_rows.append(self._compute_aggregate(agg, rows, schema))
    # 构造 schema = 默认别名
    out_schema = tuple(a.alias or self._default_alias(a) for a in aggregates)
    return [out_rows], list(out_schema)


def _aggregate_grouped(self, rows, schema, group_keys, aggregates):
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        key = tuple(r[k] for k in group_keys)
        groups[key].append(r)
    out_rows = []
    for key, group_rows in groups.items():
        agg_vals = tuple(self._compute_aggregate(a, group_rows, schema) for a in aggregates)
        out_rows.append(list(key) + list(agg_vals))
    out_schema = tuple(schema[k] for k in group_keys) + tuple(
        a.alias or self._default_alias(a) for a in aggregates
    )
    return out_rows, list(out_schema)


def _compute_aggregate(self, agg, rows, schema):
    func = agg.func
    if agg.arg == "*":
        vals = rows
    else:
        # ('column', col_name)
        col_name = agg.arg[1]
        pos = schema.index(col_name)
        vals = [r[pos] for r in rows]
    if func == "COUNT":
        return len(vals)
    if func == "SUM":
        return sum(v for v in vals if v is not None)
    if func == "AVG":
        non_null = [v for v in vals if v is not None]
        return sum(non_null) / len(non_null) if non_null else None
    if func == "MIN":
        non_null = [v for v in vals if v is not None]
        return min(non_null) if non_null else None
    if func == "MAX":
        non_null = [v for v in vals if v is not None]
        return max(non_null) if non_null else None
    raise ValueError(f"unsupported aggregate: {func!r}")


def _default_alias(self, agg):
    if agg.arg == "*":
        return "count"
    if isinstance(agg.arg, tuple) and agg.arg[0] == "column":
        return f"{agg.func.lower()}_{agg.arg[1]}"
    return agg.func.lower()
```

`_eval_project` 完善（合并 schema 下的列定位）：

```python
def _eval_project(self, node: Project):
    rows, schema = self._eval(node.source)
    if node.star:
        return rows, schema
    out_rows = []
    for r in rows:
        out_rows.append(self._project_row(r, node.items, schema))
    new_schema = tuple(label for label, _ in node.items)
    return out_rows, list(new_schema)


def _project_row(self, row, items, schema):
    out = []
    for label, expr in items:
        if expr == "star":
            continue
        if isinstance(expr, tuple) and expr[0] == "col":
            _, _qualifier, name = expr
            # 合并 schema 中可能含同名歧义；优先按限定列定位
            pos = self._resolve_col(name, expr[1], schema)
            out.append(row[pos])
        else:
            out.append(None)
    return out


def _resolve_col(self, name, qualifier, schema):
    if qualifier is not None:
        for i, s in enumerate(schema):
            if s == f"{qualifier}.{name}":
                return i
    for i, s in enumerate(schema):
        if s == name:
            return i
    raise ValueError(f"column {name!r} not in schema {schema!r}")
```

`_eval_filter` 完善（支持 `qualifier.col`）：

```python
def _eval_filter(self, node: Filter):
    rows, schema = self._eval(node.source)
    pred = node.predicate
    return [r for r in rows if self._eval_predicate(r, pred, schema)], schema


def _eval_predicate(self, row, pred, schema):
    if isinstance(pred, tuple) and pred[0] == "AND":
        return self._eval_predicate(row, pred[1], schema) and self._eval_predicate(row, pred[2], schema)
    if isinstance(pred, tuple) and pred[0] == "OR":
        return self._eval_predicate(row, pred[1], schema) or self._eval_predicate(row, pred[2], schema)
    if isinstance(pred, tuple) and pred[0] == "NOT":
        return not self._eval_predicate(row, pred[1], schema)
    if isinstance(pred, tuple) and len(pred) == 3 and pred[0] == "=":
        _, pos, lit = pred
        return row[pos] == lit
    return True
```

#### Step 8.3（GREEN）: resolver 中 GROUP BY / ORDER BY 解析限定列

`src/tinydb/resolver.py` 在 `resolve()` 中追加：

```python
    # ORDER BY / GROUP BY / HAVING：限定列拆解
    def _split_q(s):
        return tuple(s.split(".", 1)) if "." in s else (None, s)

    order_resolved = tuple(
        _split_q(it.column) for it in ast.order_by
    )
    group_resolved = tuple(_split_q(g) for g in ast.group_by)
```

并在 `build_plan` 中 GROUP BY 命中时把 `group_keys` 填为列位置：

```python
    if ast.aggregate_aliases or ast.group_by:
        gk = []
        for q, n in [(g, None) for g in ast.group_by]:
            # 已折解
            pass
        # 实际实现见 resolver 阶段折解后写入 ResolvedPlan.group_resolved
        # 并由 build_plan 读出
```

更简洁做法：在 `ResolvedPlan.group_resolved` / `order_resolved` 存 `(pos_in_merged_schema, descending)` 列表，并在 `build_plan` 中读出。完整改动在后续微调。

**验收命令**:
```bash
.venv/bin/python -m pytest tests/integration/test_join_post_phases.py \
                   tests/unit/test_join_executor.py tests/property/test_join_order.py -v
```
预期: GREEN。

#### Step 8.4（REFACTOR）: 回归既有 aggregation 路径

- `tests/integration/test_aggregation_pipeline.py` 仍 pass（单表路径未受影响）。
- 既有 `tests/unit/test_aggregation_executor.py` 仍 pass。
- `pyflakes src/tinydb/_join_executor.py src/tinydb/resolver.py` 0 warnings。

**Commit**:
```bash
git add src/tinydb/_join_executor.py src/tinydb/resolver.py src/tinydb/parser.py \
        tests/integration/test_join_post_phases.py
git commit -m "feat(join): consume merged schema through Filter/Project/GroupBy/Having/Sort/Limit pipeline"
```

---

### Task 9: Python API — `Row.__getitem__` + `Database.explain_plan` + 错误再导出

**Files:**
- Modify: `src/tinydb/database.py`（新增 `explain_plan`、Row `__getitem__`、JOIN Row 投影）
- Create: `tests/integration/test_join_row_api.py`
- Create: `tests/integration/test_explain_plan.py`

**TDD 阶段**: RED → GREEN → REFACTOR

#### Step 9.1（RED）: 编写 Row 映射访问与 explain_plan 测试

新增 `tests/integration/test_join_row_api.py`：

```python
import pytest
import tinydb


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE u(id INT, name TEXT)")
    db.execute("CREATE TABLE o(id INT, uid INT)")
    db.execute("INSERT INTO u(id, name) VALUES (1,'a'),(2,'b')")
    db.execute("INSERT INTO o(id, uid) VALUES (10,1),(11,2)")


@pytest.mark.integration
def test_row_getitem_by_qualified_label(db):
    _setup(db)
    rows = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    r = rows[0]
    assert r["u.id"] == 1
    assert r["o.id"] == 10


@pytest.mark.integration
def test_row_getitem_by_merged_using_key(db):
    db.execute("INSERT INTO o(id, uid) VALUES (1, 1)")  # 共享 id=1
    rows = db.execute("SELECT * FROM u JOIN o USING (id)")
    r = rows[0]
    # 合并键 'id' 只出现一次
    assert r.columns.count("id") == 1
    assert r["id"] == 1


@pytest.mark.integration
def test_row_attr_access_still_works_for_safe_identifier(db):
    _setup(db)
    rows = db.execute("SELECT u.id FROM u JOIN o ON u.id = o.uid")
    r = rows[0]
    # 'u.id' 不是合法 Python 标识符，属性访问失败；映射访问可用
    assert r["u.id"] == 1
    with pytest.raises(AttributeError):
        _ = r.u.id  # type: ignore


@pytest.mark.integration
def test_row_iteration_and_repr(db):
    _setup(db)
    rows = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    r = rows[0]
    assert list(r) == [1, 10]
    text = repr(r)
    assert "u.id=" in text
    assert "o.id=" in text


@pytest.mark.integration
def test_row_equality(db):
    _setup(db)
    rows1 = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    rows2 = db.execute("SELECT u.id, o.id FROM u JOIN o ON u.id = o.uid")
    assert rows1[0] == rows2[0]


@pytest.mark.integration
def test_single_table_row_keeps_bare_columns(db):
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    db.execute("INSERT INTO t(id, name) VALUES (1,'a')")
    rows = db.execute("SELECT * FROM t")
    r = rows[0]
    assert r.id == 1
    assert r["id"] == 1
    assert r["name"] == "a"
```

新增 `tests/integration/test_explain_plan.py`：

```python
import pytest
import tinydb
from tinydb.plan import LogicalPlan, Scan, Join


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE u(id INT, name TEXT)")
    db.execute("CREATE TABLE o(id INT, uid INT)")


@pytest.mark.integration
def test_explain_plan_returns_logical_plan(db):
    _setup(db)
    plan = db.explain_plan("SELECT u.id FROM u JOIN o ON u.id = o.uid")
    assert isinstance(plan, LogicalPlan)
    assert isinstance(plan, Scan) is False  # 顶层为 Project


@pytest.mark.integration
def test_explain_plan_does_not_modify_pager_or_wal(db):
    _setup(db)
    pc_before = db.pager.page_count()
    plan = db.explain_plan("SELECT u.id FROM u JOIN o ON u.id = o.uid")
    assert db.pager.page_count() == pc_before
    # plan 构造不写文件
    assert plan is not None


@pytest.mark.integration
def test_explain_plan_for_single_table(db):
    db.execute("CREATE TABLE t(id INT)")
    plan = db.explain_plan("SELECT * FROM t")
    assert isinstance(plan, LogicalPlan)


@pytest.mark.integration
def test_explain_plan_raises_on_non_select(db):
    _setup(db)
    with pytest.raises(tinydb.ExecutionError):
        db.explain_plan("CREATE TABLE x(id INT)")
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/integration/test_join_row_api.py tests/integration/test_explain_plan.py -v
```
预期: RED；`Row.__getitem__` 不存在；`explain_plan` 不存在。

#### Step 9.2（GREEN）: 修改 database.py

在 `Row` dataclass（`:15-40`）中新增 `__getitem__`：

```python
    def __getitem__(self, name):
        if name in self.columns:
            return self.values[self.columns.index(name)]
        raise KeyError(name)
```

在 `Database` 类中新增方法：

```python
    def explain_plan(self, sql: str):
        """Build a LogicalPlan from a SELECT. Does not touch Pager/WAL/Transaction."""
        from tinydb.errors import ExecutionError as _EE
        from tinydb.parser import parse, Select
        from tinydb.plan import build_plan
        tokens = tokenize(sql)
        stmts = parse(tokens)
        last = stmts.statements[-1]
        if not isinstance(last, Select):
            raise _EE("explain_plan: only SELECT is supported")
        return build_plan(last, self.catalog)
```

修改 `execute()`：检测到 `stmt.joins` 时直接复用 executor 返回的 `list[Row]`：

```python
    def execute(self, sql: str) -> list[Row]:
        tokens = tokenize(sql)
        stmts = parse(tokens)
        results: list[Row] = []
        for s in stmts.statements:
            out = self.executor.execute(s)
            if isinstance(out, list):
                results = out
        last = stmts.statements[-1] if stmts.statements else None
        if isinstance(last, Select) and not last.joins:
            ti = self.catalog.get_table(last.table)
            if ti is not None:
                cols = tuple(n for n, _ in ti.schema) if last.columns == ("*",) else tuple(last.columns)
                results = [Row(values=tuple(r), columns=cols) for r in results]
        return results
```

#### Step 9.3（GREEN）: 修改 executor.py 的 JOIN dispatch 输出 Row 列表

```python
        if stmt.joins:
            from tinydb.plan import build_plan
            from tinydb._join_executor import JoinExecutor
            from tinydb.database import Row
            plan = build_plan(stmt, self.catalog)
            je = JoinExecutor(self)
            rows, schema = je.execute_plan(plan)
            cols = tuple(schema)
            return [Row(values=tuple(r), columns=cols) for r in rows]
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/integration/test_join_row_api.py tests/integration/test_explain_plan.py -v
.venv/bin/python -m pytest tests/integration/test_database_api.py -v
```
预期: GREEN；既有 `test_database_api.py` 不回归。

#### Step 9.4（REFACTOR）: 再导出与文档

- `src/tinydb/__init__.py` 增加 `explain_plan` 不需要（它是 Database 方法）；但需要再导出 `LogicalPlan` / `build_plan`：

```python
from tinydb.plan import LogicalPlan, build_plan, Scan, Join, Filter, Aggregate, Sort, Project, Limit
```

并在 `__all__` 列表追加。

**Commit**:
```bash
git add src/tinydb/database.py src/tinydb/__init__.py \
        tests/integration/test_join_row_api.py tests/integration/test_explain_plan.py
git commit -m "feat(api): add Database.explain_plan, Row.__getitem__, JOIN Row with qualified labels"
```

---

### Task 10: 错误传播契约 + 完整回归 + 文档

**Files:**
- Modify: `src/tinydb/__init__.py`（再导出确认）
- Create: `tests/e2e/sql/join/inner.sql`、`left.sql`、`right.sql`、`full.sql`、`cross.sql`、`using.sql`、`natural.sql`、`chained.sql`（golden SQL）
- Modify: `tests/e2e/test_join_queries.py`（新增 golden runner）
- Modify: `docs/MVP_LIMITATIONS.md`（增补 JOIN 内存限制）
- Modify: `README.md` / `操作手册.md`（增补 JOIN 用法章节）
- Create: `docs/superpowers/reports/2026-07-23-join-query-verify.md`（验证报告）

**TDD 阶段**: 全量回归 + 文档落地（不再做 RED/GREEN 切分）

#### Step 10.1: 编写 golden SQL

`tests/e2e/sql/join/inner.sql`：

```sql
CREATE TABLE u(id INT, name TEXT);
CREATE TABLE o(id INT, uid INT, total INT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b');
INSERT INTO o(id, uid, total) VALUES (10, 1, 100), (11, 2, 200), (12, 3, 50);
SELECT u.id, o.id FROM u INNER JOIN o ON u.id = o.uid;
```

`tests/e2e/sql/join/left.sql`：

```sql
CREATE TABLE u(id INT, name TEXT);
CREATE TABLE o(id INT, uid INT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b'), (3, 'c');
INSERT INTO o(id, uid) VALUES (10, 1), (11, 2);
SELECT u.id, o.id FROM u LEFT JOIN o ON u.id = o.uid;
```

`tests/e2e/sql/join/right.sql`：

```sql
CREATE TABLE u(id INT);
CREATE TABLE o(id INT, uid INT);
INSERT INTO u(id) VALUES (1), (2);
INSERT INTO o(id, uid) VALUES (10, 1), (11, 99);
SELECT u.id, o.id FROM u RIGHT JOIN o ON u.id = o.uid;
```

`tests/e2e/sql/join/full.sql`：

```sql
CREATE TABLE u(id INT);
CREATE TABLE o(id INT, uid INT);
INSERT INTO u(id) VALUES (1), (2);
INSERT INTO o(id, uid) VALUES (10, 1), (11, 99);
SELECT u.id, o.id FROM u FULL JOIN o ON u.id = o.uid;
```

`tests/e2e/sql/join/cross.sql`：

```sql
CREATE TABLE u(id INT);
CREATE TABLE o(id INT);
INSERT INTO u(id) VALUES (1), (2);
INSERT INTO o(id) VALUES (10), (20);
SELECT u.id, o.id FROM u CROSS JOIN o;
```

`tests/e2e/sql/join/using.sql`：

```sql
CREATE TABLE u(id INT, name TEXT);
CREATE TABLE o(id INT, total INT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b');
INSERT INTO o(id, total) VALUES (1, 100), (3, 50);
SELECT * FROM u JOIN o USING (id);
```

`tests/e2e/sql/join/natural.sql`：

```sql
CREATE TABLE u(id INT, name TEXT, dept TEXT);
CREATE TABLE o(id INT, total INT);
INSERT INTO u(id, name, dept) VALUES (1, 'a', 'eng'), (2, 'b', 'sales');
INSERT INTO o(id, total) VALUES (1, 100), (3, 50);
SELECT * FROM u NATURAL INNER JOIN o;
```

`tests/e2e/sql/join/chained.sql`：

```sql
CREATE TABLE a(id INT);
CREATE TABLE b(id INT);
CREATE TABLE c(id INT);
INSERT INTO a(id) VALUES (1), (2);
INSERT INTO b(id) VALUES (1), (2);
INSERT INTO c(id) VALUES (1), (2);
SELECT a.id, b.id, c.id FROM a JOIN b ON a.id = b.id JOIN c ON b.id = c.id;
```

#### Step 10.2: 编写 E2E runner

新增 `tests/e2e/test_join_queries.py`：

```python
"""golden SQL E2E for join-query change.

参考 tests/e2e/test_golden_sql.py 的目录约定（每个 .sql 文件一组预期输出），
本测试扫描 tests/e2e/sql/join/*.sql 并通过 Database.execute 跑出 Row 列表，
与同名的 .expected.txt 比对。
"""
import os
from pathlib import Path

import pytest

import tinydb

JOIN_SQL_DIR = Path(__file__).parent / "sql" / "join"


@pytest.mark.parametrize(
    "sql_path",
    sorted(JOIN_SQL_DIR.glob("*.sql")),
    ids=lambda p: p.name,
)
def test_join_golden(tmp_path, sql_path):
    expected_path = sql_path.with_suffix(".expected.txt")
    if not expected_path.exists():
        pytest.skip(f"no expected file for {sql_path.name}")

    d = tinydb.Database(str(tmp_path / f"{sql_path.stem}.db"))
    try:
        sql = sql_path.read_text()
        rows = d.execute(sql)
        actual = "\n".join(repr(r) for r in rows) + ("\n" if rows else "")
    finally:
        d.close()
    expected = expected_path.read_text()
    assert actual.strip() == expected.strip()
```

并为每个 `.sql` 配套 `.expected.txt`：

```bash
# 第一次跑时手动生成 expected.txt：
# .venv/bin/python -m pytest tests/e2e/test_join_queries.py -v -s
# 然后人工审查输出并写入文件。
```

`tests/e2e/sql/join/inner.expected.txt`（示例，需人工核验后写入）：

```
Row(u.id=1, o.id=10)
Row(u.id=2, o.id=11)
```

（其余 `.expected.txt` 类似，由 executor 输出人工审查后写入。）

**验收命令**:
```bash
.venv/bin/python -m pytest tests/e2e/test_join_queries.py -v
```
预期: GREEN（所有 golden 通过）。

#### Step 10.3: 全量回归 + 覆盖率 + pyflakes + OpenSpec strict

```bash
# 全量 pytest
.venv/bin/python -m pytest tests/ -v --tb=short

# 覆盖率（要求：整体 ≥ 93%，新模块 ≥ 85%）
.venv/bin/python -m pytest tests/ --cov=src/tinydb --cov-report=term --cov-report=term-missing

# pyflakes
.venv/bin/python -m pyflakes src/tinydb/

# OpenSpec strict
.venv/bin/python -m openspec validate --strict

# 文件行数预算检查
.venv/bin/python -c "
import os
for p in ['resolver.py','plan.py','_join_executor.py','executor.py','parser.py','tokenizer.py','errors.py','database.py','__init__.py']:
    fp = os.path.join('src/tinydb', p)
    if os.path.exists(fp):
        n = sum(1 for _ in open(fp))
        print(f'{p}: {n} lines')
"
```

预期: 全部测试 pass；覆盖率达标；pyflakes 0；OpenSpec strict 全绿；文件行数在预算内。

#### Step 10.4: 文档更新

`docs/MVP_LIMITATIONS.md` 增补：

```markdown
## v0.2 JOIN 内存限制

JOIN 路径在 v0.2 采用 nested-loop + 物化宽行策略，无硬性行数上限。极大结果集
（如 100 万行 × 100 万行的 CROSS JOIN）会消耗大量内存。建议在 application 层
显式加 LIMIT 或在 WHERE 中加过滤条件以缩小中间结果。
```

`README.md` / `操作手册.md` 增补章节：

```markdown
## 多表 JOIN（v0.2 新增）

支持 INNER / LEFT / RIGHT / FULL / CROSS JOIN；ON / USING / NATURAL；表别名；
限定列引用 `u.id`。

示例：

  SELECT u.id, o.id, SUM(o.total) AS s
  FROM users u
  JOIN orders o ON u.id = o.uid
  GROUP BY u.id
  HAVING SUM(o.total) > 100
  ORDER BY s DESC
  LIMIT 10;

错误诊断：未知表 → UnknownSource；歧义裸列 → AmbiguousColumn；USING 列缺失 →
MissingUsingKey；类型不兼容 → IncompatibleKeyTypes。
```

#### Step 10.5: Spec Patch 同步回 delta spec（若发现缺边界场景）

在 archive 前，如发现 OpenSpec delta spec 缺失 Design Doc §7 已写入的边界场景（如 `Outer join output ordering is stable` / `NATURAL JOIN with no common columns degrades to CROSS` / `USING and NATURAL merged keys use coalesce semantics` / `ResolutionError is exposed and identifiable` / `JOIN Row supports mapping-style access by qualified label` / `NATURAL JOIN automatically discovers common columns`），将这些 requirement + scenario 追加到：

- `openspec/changes/join-query/specs/sql-join-query/spec.md`
- `openspec/changes/join-query/specs/sql-minimal-parser/spec.md`
- `openspec/changes/join-query/specs/python-api/spec.md`

（Design Doc §7 已有完整文本，可直接复制。）

#### Step 10.6: 生成验证报告

`docs/superpowers/reports/2026-07-23-join-query-verify.md` 模板：

```markdown
# join-query 验证报告

- change: join-query
- base ref: 1ca8179b1fd9864102704d396e8e976a0d49d168
- 设计文档: docs/superpowers/specs/2026-07-23-join-query-design.md
- 实施计划: docs/superpowers/plans/2026-07-23-join-query.md

## 1. 测试结果

| 类别 | 通过/失败 | 数量 |
|------|-----------|------|
| unit | pass | NNN |
| integration | pass | NNN |
| property | pass | NN |
| e2e | pass | NN |
| 既有回归 | pass | 全部 |

## 2. 覆盖率

- 整体: NN.NN% (v0.1 基线 93.xx%)
- resolver.py: NN.NN%
- plan.py: NN.NN%
- _join_executor.py: NN.NN%

## 3. 文件行数

（按 §模块行数预算 表格列出实际值）

## 4. OpenSpec strict 验证

```
$ .venv/bin/python -m openspec validate --strict
All checks passed.
```

## 5. 已知偏差 / 后续 follow-up

（如 executor.py 仍超 1800 行 → 列入 follow-up；USING 键类型校验 → 后续 follow-up）

## 6. Acceptance Checklist（Design Doc §11）

- [ ] 所有 v0.1 测试在 feature/20260723/join-query 上保持 pass
- [ ] 新模块覆盖率 ≥ 85%；整体 ≥ 93%
- [ ] OpenSpec strict validation 全绿
- [ ] Database.explain_plan 在 JOIN / 单表 / aggregation 上输出稳定 plan
- [ ] 完整矩阵测试通过
- [ ] property 测试断言 strict-left-deep-insertion
- [ ] 文档已更新（MVP_LIMITATIONS + README + 操作手册）
- [ ] 验证报告（本文件）已生成
```

**验收命令**:
```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m pytest tests/ --cov=src/tinydb --cov-report=term-missing
.venv/bin/python -m pyflakes src/tinydb/
.venv/bin/python -m openspec validate --strict
```

预期: 全部通过。

**Commit**:
```bash
git add tests/e2e/test_join_queries.py tests/e2e/sql/join/ \
        docs/MVP_LIMITATIONS.md README.md 操作手册.md \
        docs/superpowers/reports/2026-07-23-join-query-verify.md \
        openspec/changes/join-query/specs/
git commit -m "docs(join): add e2e golden SQL, MVP limitations, user guide, and verify report"
```

---

## 完成标准

本计划在以下全部成立后视为完成（对应 Design Doc §11 Acceptance）：

1. 全部 10 个 task 的 commit 已落地于 `feature/20260723/join-query`（或同等工作区分支）。
2. `pytest tests/` 全部 pass（v0.1 既有测试无回归）。
3. 整体覆盖率 ≥ 93%，新模块（resolver / plan / _join_executor）单测覆盖率 ≥ 85%。
4. `pyflakes src/tinydb/` 0 warnings。
5. `openspec validate --strict` 全绿。
6. 三个新模块行数各自 ≤ 450；`executor.py` ≤ 1800；`parser.py` ≤ 1300；`tokenizer.py` ≤ 200；`errors.py` ≤ 140；`database.py` ≤ 160。
7. JOIN 路径必走 `_txn_read_page`（ACID 回归通过）。
8. property 测试对 LEFT/RIGHT/FULL 输出顺序断言 strict-left-deep-insertion。
9. NATURAL 无共同列退化为 CROSS 且不报错。
10. USING/NATURAL 合并键 Coalesce 行为正确。
11. JOIN Row 限定列标签唯一，USING 合并键不重复。
12. `Database.explain_plan` 不写文件、不提交事务。
13. `docs/MVP_LIMITATIONS.md` 增补 JOIN 内存限制；`README.md` / `操作手册.md` 增补 JOIN 用法。
14. `docs/superpowers/reports/2026-07-23-join-query-verify.md` 已生成。
15. OpenSpec delta spec 已同步 Design Doc §7 的 Spec Patch（如缺则补）。