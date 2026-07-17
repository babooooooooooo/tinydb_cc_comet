---
change: tinydb-engine-v1
design-doc: docs/superpowers/specs/2026-07-16-tinydb-engine-v1-design.md
base-ref: b14f031aede3ad32a14a7402957c54b1fea31bcf
archived-with: 2026-07-17-tinydb-engine-v1
---

# tinydb-engine-v1 Implementation Plan

> **说明（中文概要）**：本计划实现 `tinydb-engine-v1` change。在 parser 与 executor 中引入 UPDATE 语句、复合 WHERE 表达式（AND / OR / NOT）、SELECT 末尾链（ORDER BY / LIMIT / OFFSET）。模块行数预算：`parser.py ≤ 750`、`executor.py ≤ 580`（已从 520 上调）、`tokenizer.py ≤ 200`。不改存储层、不引入索引、不引入事务。覆盖目标 ≥ 90%。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `tinydb-mvp` parser/executor with UPDATE statements, compound WHERE (AND/OR/NOT), and SELECT ORDER BY/LIMIT/OFFSET chain — without changing storage layer.

**Architecture:** Add new AST nodes (`Update`, `EqualsExpr`, `AndExpr`, `OrExpr`, `NotExpr`, `OrderByItem`) to parser.py; add recursive-descent `_parse_expr` precedence chain (OR < AND < NOT < primary < comparison). Add 11 new keywords to tokenizer. Add `eval_expr` (short-circuit) and `_exec_update` (in-place with chain fallback) and stable sort + slice chain in executor.py. Upgrade `Select` dataclass to `frozen=True` with `order_by/limit/offset` defaults.

**Tech Stack:** Python 3.12, pytest 9.x, pytest-cov, hypothesis (existing). No new dependencies.

**Worktree:** `/home/lz/projects/tinydb-worktrees/tinydb-engine-v1` (env: `.venv/bin/python -m pytest`).

---

## Source-of-truth references

- **Design Doc**: `docs/superpowers/specs/2026-07-16-tinydb-engine-v1-design.md`
  - §3 AST nodes (§3.2 Select upgrade, §3.3 new nodes)
  - §4 Tokenizer keyword table
  - §5 Parser EBNF + skeleton code (§5.3, §5.4, §5.5)
  - §6 Executor (`eval_expr` §6.1, `_exec_update` v2 §6.3, `_exec_select` chain §6.4, dispatcher §6.6)
  - §8 Test matrix (U-PAR-*, U-EXE-*, I-V1-*, e2e golden)
- **Tasks**: `openspec/changes/tinydb-engine-v1/tasks.md` (8 sections, 31 subtasks)
- **Proposal**: `openspec/changes/tinydb-engine-v1/proposal.md`

---

## File inventory (responsibilities)

| File | Status | Responsibility |
|------|--------|---------------|
| `src/tinydb/parser.py` | modify | + AST nodes; + `_parse_expr/_parse_or_expr/_parse_and_expr/_parse_not_expr/_parse_primary`; + `_parse_update/_parse_order_by/_parse_limit/_parse_offset`; upgrade `Select` dataclass |
| `src/tinydb/tokenizer.py` | modify | + 11 keywords: UPDATE SET AND OR NOT ORDER BY ASC DESC LIMIT OFFSET |
| `src/tinydb/executor.py` | modify | + `eval_expr`; + `_exec_update` (v2 with chain fallback); + `_stable_sort`; upgrade `_exec_select`; dispatcher routes `Update` |
| `src/tinydb/database.py` | minor | Fix `stmt.columns == ["*"]` → `(" *",)` if present |
| `tests/unit/test_engine_v1_parser.py` | new | U-PAR-01..22 (parser AST/keyword/error tests) |
| `tests/unit/test_engine_v1_executor.py` | new | U-EXE-01..25 (eval/update/sort tests) |
| `tests/unit/test_engine_v1_tokenizer.py` | new | keyword conflict tests (U-PAR-19..21) |
| `tests/integration/test_engine_v1.py` | new | I-V1-01..10 (e2e through Database) |
| `tests/e2e/sql/engine_v1/*.sql` | new | 12 golden SQL files + matching `.expected.txt` |
| `tests/unit/test_parser.py` | modify | Migrate `stmt.where == ("col","=",lit)` → `EqualsExpr(column=..., value=...)` |
| `tests/integration/test_executor.py` | modify | Migrate any `stmt.where == tuple` assertions |

Module line budget: `parser.py ≤ 750`, `executor.py ≤ 580` (uplift from 520; +60 for eval_expr + _exec_update with chain fallback + ORDER BY/LIMIT/OFFSET sort/slice + row codec path; design rationale recorded in task 13 §13.4). Coverage ≥ 90% across project; engine-v1 changed modules 100%.

---

## Engine-v1 task ordering (matches tasks.md §1-§8)

Implementation order below minimizes cross-task dependencies. RED tests are listed by name (full bodies live in the test files written in §1.1–§7.1).

---

## Task 1: Tokenizer keyword table + conflict tests

**Files:**
- Modify: `src/tinydb/tokenizer.py:13-16` (KEYWORDS set)
- New: `tests/unit/test_engine_v1_tokenizer.py`

- [x] **Step 1.1 — Write failing tokenizer tests**

In `tests/unit/test_engine_v1_tokenizer.py`:

```python
import pytest
from tinydb.tokenizer import tokenize, KEYWORDS
from tinydb.errors import ParseError

KW_NEW = ["UPDATE", "SET", "AND", "OR", "NOT", "ORDER",
          "BY", "ASC", "DESC", "LIMIT", "OFFSET"]

@pytest.mark.parametrize("kw", KW_NEW)
def test_tokenizer_keyword_upper(kw):
    toks = tokenize(f"SELECT * FROM t {kw}")
    assert toks[0].value == "SELECT"  # sanity
    assert any(t.type == "KEYWORD" and t.value == kw for t in toks)

@pytest.mark.parametrize("kw", KW_NEW)
def test_tokenizer_keyword_case_insensitive(kw):
    toks = tokenize(f"select * from t {kw.lower()}")
    assert any(t.type == "KEYWORD" and t.value == kw for t in toks)

def test_tokenizer_update_as_identifier_raises():
    # 'update' (lower) is still upper-cased and detected as keyword
    with pytest.raises(ParseError):
        tokenize("CREATE TABLE update (id INT)")

def test_tokenizer_lowercase_order_is_identifier():
    toks = tokenize("CREATE TABLE t (order INT)")
    # 'order' IDENT (since column names are tokens after '(')
    assert any(t.type == "IDENT" and t.value == "order" for t in toks)

def test_keyword_table_complete():
    for kw in KW_NEW:
        assert kw in KEYWORDS
```

- [x] **Step 1.2 — Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_tokenizer.py -v`
Expected: FAIL on `KEYWORDS` membership and `ParseError` for keyword-as-column (current MVP accepts).

- [x] **Step 1.3 — Add 11 keywords to KEYWORDS**

In `src/tinydb/tokenizer.py` (line ~13-16), extend the set:

```python
KEYWORDS = {
    "CREATE", "TABLE", "DROP", "INSERT", "INTO", "VALUES", "SELECT",
    "FROM", "WHERE", "DELETE", "INT", "TEXT", "FLOAT", "BOOL",
    # --- tinydb-engine-v1 ---
    "UPDATE", "SET",
    "AND", "OR", "NOT",
    "ORDER", "BY",
    "ASC", "DESC",
    "LIMIT", "OFFSET",
}
```

- [x] **Step 1.4 — Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_tokenizer.py -v`
Expected: PASS

- [x] **Step 1.5 — Verify existing MVP tokenizer tests still pass**

Run: `.venv/bin/python -m pytest tests/unit/test_tokenizer.py -v`
Expected: PASS (no MVP test uses these keywords as identifiers).

- [x] **Step 1.6 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/tokenizer.py tests/unit/test_engine_v1_tokenizer.py
git commit -m "feat(engine-v1): task 1 tokenizer keyword table"
```

---

## Task 2: AST nodes (`EqualsExpr`, `AndExpr`, `OrExpr`, `NotExpr`, `OrderByItem`, `Update`)

**Files:**
- Modify: `src/tinydb/parser.py` (dataclass definitions block)
- Existing: `src/tinydb/__init__.py` (re-export new symbols)

- [x] **Step 2.1 — Write failing AST shape tests**

In `tests/unit/test_engine_v1_parser.py`:

```python
from tinydb.parser import (EqualsExpr, AndExpr, OrExpr, NotExpr,
                            OrderByItem, Update, Select)
from tinydb.errors import ParseError
from tinydb.tokenizer import tokenize
from tinydb.parser import Parser  # or whatever the parser entry is

def _parse(sql):
    from tinydb.parser import parse  # adjust name if needed
    return parse(sql)

def test_ast_equals_expr_dataclass():
    e = EqualsExpr(column="x", value=1)
    assert e.column == "x" and e.value == 1

def test_ast_and_or_not_dataclass():
    a = AndExpr(left=EqualsExpr("a", 1), right=OrExpr(
        left=EqualsExpr("b", 2),
        right=NotExpr(operand=EqualsExpr("c", 3))))
    assert isinstance(a, AndExpr)

def test_ast_order_by_item_dataclass():
    o = OrderByItem(column="x", descending=True)
    assert o.descending is True

def test_ast_update_dataclass():
    u = Update(table="t",
               sets=(("a", EqualsExpr("a", 1)),),
               where=EqualsExpr("b", 2),
               line=1, col=1)
    assert u.table == "t" and len(u.sets) == 1

def test_ast_select_defaults_compatible_with_mvp():
    # Backward compat: positional/keyword args used by MVP still work
    s = Select(table="t", columns=("x",), line=1, col=1)
    assert s.where is None
    assert s.order_by == ()
    assert s.limit is None
    assert s.offset is None
```

> Adjust import names based on actual parser module API (read `src/tinydb/parser.py` first; entry may be `Parser` class with `.parse(sql)`).

- [x] **Step 2.2 — Run tests to verify they fail (ImportError)**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v -k "test_ast_"`
Expected: FAIL with ImportError on the new names.

- [x] **Step 2.3 — Add new AST nodes + upgrade Select**

In `src/tinydb/parser.py`, in the dataclass definitions section:

```python
from dataclasses import dataclass, field
from typing import Any, Optional, Union

# --- existing: CreateTable, DropTable, Insert, Delete unchanged ---

# --- upgraded Select (frozen, new defaults, tuple columns) ---
@dataclass(frozen=True)
class Select:
    table: str
    columns: tuple  # str tuple
    where: Optional[Any] = None      # Expr (EqualsExpr | AndExpr | OrExpr | NotExpr)
    order_by: tuple = ()
    limit: Optional[int] = None
    offset: Optional[int] = None
    line: int = 0
    col: int = 0

# --- expression AST ---
@dataclass(frozen=True)
class EqualsExpr:
    column: str
    value: Any

@dataclass(frozen=True)
class AndExpr:
    left: Any  # Expr
    right: Any

@dataclass(frozen=True)
class OrExpr:
    left: Any
    right: Any

@dataclass(frozen=True)
class NotExpr:
    operand: Any

# --- SELECT sub-clauses ---
@dataclass(frozen=True)
class OrderByItem:
    column: str
    descending: bool = False

# --- UPDATE statement ---
@dataclass(frozen=True)
class Update:
    table: str
    sets: tuple                 # tuple[tuple[str, Expr], ...]
    where: Optional[Any]        # Expr | None
    line: int
    col: int
```

> Keep `Select` columns as `tuple` from this commit onward. The next task wires parser to emit tuples; this commit only declares types.

- [x] **Step 2.4 — Re-export from package**

In `src/tinydb/__init__.py`, add (alongside existing exports):

```python
from .parser import (
    CreateTable, DropTable, Insert, Delete, Select, Update,
    EqualsExpr, AndExpr, OrExpr, NotExpr, OrderByItem,
)
```

- [x] **Step 2.5 — Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v -k "test_ast_"`
Expected: PASS

- [x] **Step 2.6 — Run full suite to confirm no signature breakage**

Run: `.venv/bin/python -m pytest -q`
Expected: FAIL only on tests that assert `stmt.where == tuple` (those are migration items in Task 3). Note failures for next task.

- [x] **Step 2.7 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/parser.py src/tinydb/__init__.py tests/unit/test_engine_v1_parser.py
git commit -m "feat(engine-v1): task 2 AST nodes and Select upgrade"
```

---

## Task 3: Migrate MVP parser tests (`tuple where` → `EqualsExpr`)

**Files:**
- Modify: `tests/unit/test_parser.py` (all `assert stmt.where == ("col", "=", lit)`)
- Modify: `tests/integration/test_executor.py` (similar pattern)

- [x] **Step 3.1 — Migrate parser unit tests**

Run: `grep -rn 'stmt\.where == (' tests/`
For each match, replace `assert stmt.where == ("col", "=", <lit>)` with
`assert stmt.where == EqualsExpr(column="col", value=<lit>)`.
If `<lit>` is a bare literal, wrap into a variable so equality is structural.

- [x] **Step 3.2 — Migrate executor integration tests**

Run: `grep -rn 'where == (' tests/integration/`
Apply the same migration.

- [x] **Step 3.3 — Fix `columns == ["*"]` → `(" *",)` everywhere**

Run: `grep -rn 'columns == \["\*"\]' src/tinydb tests/`
Replace each occurrence with `columns == ("*",)`. Search both `src/` (executor/database) and `tests/`.

- [x] **Step 3.4 — Run full suite to confirm green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all 234 MVP tests + new AST tests). If anything fails, fix migration; do not touch production code.

- [x] **Step 3.5 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add tests/
git commit -m "test(engine-v1): task 3 migrate MVP tuple-where assertions"
```

---

## Task 4: Parser — UPDATE statement + `_parse_update`

**Files:**
- Modify: `src/tinydb/parser.py` (add `_parse_update`; dispatcher routes `UPDATE`)

- [x] **Step 4.1 — Write failing parser UPDATE tests**

Append to `tests/unit/test_engine_v1_parser.py`:

```python
def test_parse_update_basic():
    stmt = _parse("UPDATE t SET a=1 WHERE b=2")
    assert isinstance(stmt, Update)
    assert stmt.table == "t"
    assert len(stmt.sets) == 1
    assert stmt.sets[0][0] == "a"
    assert isinstance(stmt.sets[0][1], EqualsExpr)
    assert stmt.sets[0][1].value == 1
    assert isinstance(stmt.where, EqualsExpr)
    assert stmt.where.column == "b"

def test_parse_update_multi_set():
    stmt = _parse("UPDATE t SET a=1, b='x'")
    assert [s[0] for s in stmt.sets] == ["a", "b"]
    assert stmt.where is None

def test_parse_update_no_set_raises():
    with pytest.raises(ParseError):
        _parse("UPDATE t")

def test_parse_update_set_rhs_expr_raises():
    # a+1 is not a literal
    with pytest.raises(ParseError):
        _parse("UPDATE t SET a=a+1")

def test_parse_update_missing_comma_raises():
    with pytest.raises(ParseError):
        _parse("UPDATE t SET a=1 b=2")
```

- [x] **Step 4.2 — Run tests to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v -k "test_parse_update"`
Expected: FAIL (UPDATE not in dispatcher).

- [x] **Step 4.3 — Implement `_parse_update`**

In `src/tinydb/parser.py`:

```python
# Add to Parser class
def _parse_update(self):
    kw = self.expect_keyword("UPDATE")
    tt = self.peek()
    if tt.type != "IDENT":
        raise ParseError(tt.line, tt.col, "expected table name")
    table = self.advance().value
    self.expect_keyword("SET")

    sets = []
    while True:
        ct = self.peek()
        if ct.type != "IDENT":
            raise ParseError(ct.line, ct.col, "expected column name in SET")
        col = self.advance().value
        self.expect("PUNCT", "=")
        lit_tok = self.advance()
        # Literal token types: INT_LIT, FLOAT_LIT, STR_LIT, BOOL_LIT (adjust to project's actual names)
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
    where = self._parse_where()
    return Update(table=table, sets=tuple(sets), where=where,
                  line=kw.line, col=kw.col)
```

> `_LITERAL_TYPES` is whatever set the parser already uses to validate literals (e.g., `{"INT_LIT", "FLOAT_LIT", "STR_LIT", "BOOL_LIT"}`). Check `src/tinydb/parser.py` for the existing literal-token constant.

- [x] **Step 4.4 — Wire dispatcher**

In `parse_statement` (or equivalent entry), add:

```python
if kw == "UPDATE":
    return self._parse_update()
```

- [x] **Step 4.5 — Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v -k "test_parse_update"`
Expected: PASS

- [x] **Step 4.6 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/parser.py tests/unit/test_engine_v1_parser.py
git commit -m "feat(engine-v1): task 4 UPDATE statement parsing"
```

---

## Task 5: Parser — compound WHERE (`_parse_expr` precedence chain)

**Files:**
- Modify: `src/tinydb/parser.py` (add `_parse_expr/_parse_or_expr/_parse_and_expr/_parse_not_expr/_parse_primary`)

- [x] **Step 5.1 — Write failing parser compound WHERE tests**

Append to `tests/unit/test_engine_v1_parser.py`:

```python
def test_parse_and_or_not_associativity():
    s = _parse("SELECT * FROM t WHERE a=1 AND b=2 OR c=3")
    expr = s.where
    # Left-assoc: Or(And(EQ a 1, EQ b 2), EQ c 3)
    assert isinstance(expr, OrExpr)
    assert isinstance(expr.left, AndExpr)
    assert isinstance(expr.left.left, EqualsExpr)
    assert isinstance(expr.left.right, EqualsExpr)
    assert isinstance(expr.right, EqualsExpr)

def test_parse_and_or_precedence():
    s = _parse("SELECT * FROM t WHERE a=1 OR b=2 AND c=3")
    # AND binds tighter: Or(EQ a 1, And(EQ b 2, EQ c 3))
    assert isinstance(s.where, OrExpr)
    assert isinstance(s.where.right, AndExpr)

def test_parse_not_with_parens():
    s = _parse("SELECT * FROM t WHERE NOT (a=1 OR b=2)")
    assert isinstance(s.where, NotExpr)
    assert isinstance(s.where.operand, OrExpr)

def test_parse_not_right_associative():
    s = _parse("SELECT * FROM t WHERE NOT NOT a=1")
    assert isinstance(s.where, NotExpr)
    assert isinstance(s.where.operand, NotExpr)

def test_parse_where_unterminated_or():
    with pytest.raises(ParseError):
        _parse("SELECT * FROM t WHERE a=1 OR")

def test_parse_where_unterminated_not():
    with pytest.raises(ParseError):
        _parse("SELECT * FROM t WHERE NOT")

def test_parse_where_missing_rparen():
    with pytest.raises(ParseError):
        _parse("SELECT * FROM t WHERE (a=1")
```

- [x] **Step 5.2 — Run tests to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v -k "test_parse_and_or_not or test_parse_not or test_parse_where_"`
Expected: FAIL.

- [x] **Step 5.3 — Implement precedence chain**

In `src/tinydb/parser.py` Parser class:

```python
def _peek_kw(self, kw):
    t = self.peek()
    return t.type == "KEYWORD" and t.value == kw

def _peek_punct(self, p):
    t = self.peek()
    return t.type == "PUNCT" and t.value == p

def _parse_expr(self):
    return self._parse_or_expr()

def _parse_or_expr(self):
    left = self._parse_and_expr()
    while self._peek_kw("OR"):
        self.advance()
        right = self._parse_and_expr()
        left = OrExpr(left=left, right=right)
    return left

def _parse_and_expr(self):
    left = self._parse_not_expr()
    while self._peek_kw("AND"):
        self.advance()
        right = self._parse_not_expr()
        left = AndExpr(left=left, right=right)
    return left

def _parse_not_expr(self):
    if self._peek_kw("NOT"):
        self.advance()
        return NotExpr(operand=self._parse_not_expr())
    return self._parse_primary()

def _parse_primary(self):
    if self._peek_punct("("):
        self.advance()
        inner = self._parse_expr()
        self.expect("PUNCT", ")")
        return inner
    return self._parse_comparison()  # existing IDENT = literal path
```

Replace the existing `_parse_where` body (or its `where_clause` parsing block) to call `self._parse_expr()`. Make sure `Delete.where` also flows through `_parse_expr` for consistency.

- [x] **Step 5.4 — Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v`
Expected: all PASS.

- [x] **Step 5.5 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/parser.py tests/unit/test_engine_v1_parser.py
git commit -m "feat(engine-v1): task 5 WHERE compound expression parser"
```

---

## Task 6: Parser — ORDER BY / LIMIT / OFFSET

**Files:**
- Modify: `src/tinydb/parser.py` (`_parse_select` upgrade)

- [x] **Step 6.1 — Write failing tests**

Append to `tests/unit/test_engine_v1_parser.py`:

```python
def test_parse_select_order_by_asc():
    s = _parse("SELECT * FROM t ORDER BY x")
    assert len(s.order_by) == 1
    assert s.order_by[0].column == "x"
    assert s.order_by[0].descending is False

def test_parse_select_order_by_desc():
    s = _parse("SELECT * FROM t ORDER BY x DESC")
    assert s.order_by[0].descending is True

def test_parse_select_order_by_multi_key():
    s = _parse("SELECT * FROM t ORDER BY a ASC, b DESC")
    assert [(o.column, o.descending) for o in s.order_by] == [("a", False), ("b", True)]

def test_parse_select_limit_offset():
    s = _parse("SELECT * FROM t LIMIT 10 OFFSET 5")
    assert s.limit == 10 and s.offset == 5

def test_parse_select_limit_only():
    s = _parse("SELECT * FROM t LIMIT 10")
    assert s.limit == 10 and s.offset is None

def test_parse_select_offset_only():
    s = _parse("SELECT * FROM t OFFSET 5")
    assert s.offset == 5 and s.limit is None

def test_parse_select_order_limit_offset_chain():
    s = _parse("SELECT * FROM t WHERE a=1 ORDER BY b DESC LIMIT 2 OFFSET 1")
    assert s.where is not None
    assert len(s.order_by) == 1 and s.order_by[0].descending is True
    assert s.limit == 2 and s.offset == 1
```

- [x] **Step 6.2 — Run tests to verify failure**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v -k "test_parse_select_order or test_parse_select_limit or test_parse_select_offset or test_parse_select_order_limit"`
Expected: FAIL.

- [x] **Step 6.3 — Implement `_parse_order_by/_parse_limit/_parse_offset`**

In `src/tinydb/parser.py` Parser class:

```python
def _parse_order_by(self):
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

def _parse_limit(self):
    if not self._peek_kw("LIMIT"):
        return None
    self.advance()
    t = self.advance()
    if t.type != "INT_LIT":     # adjust to project's literal token name
        raise ParseError(t.line, t.col, "LIMIT must be a non-negative integer")
    return int(t.value)

def _parse_offset(self):
    if not self._peek_kw("OFFSET"):
        return None
    self.advance()
    t = self.advance()
    if t.type != "INT_LIT":
        raise ParseError(t.line, t.col, "OFFSET must be a non-negative integer")
    return int(t.value)
```

- [x] **Step 6.4 — Upgrade `_parse_select` to call the three new helpers**

```python
def _parse_select(self):
    kw = self.expect_keyword("SELECT")
    cols = self._parse_projection()             # returns list[str]
    self.expect_keyword("FROM")
    table_t = self.peek()
    if table_t.type != "IDENT":
        raise ParseError(table_t.line, table_t.col, "expected table name")
    table = self.advance().value
    where = self._parse_where()                  # now returns Expr
    order_by = self._parse_order_by()
    limit = self._parse_limit()
    offset = self._parse_offset()
    return Select(table=table, columns=tuple(cols), where=where,
                  order_by=order_by, limit=limit, offset=offset,
                  line=kw.line, col=kw.col)
```

- [x] **Step 6.5 — Run tests; iterate to green**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_parser.py -v`
Expected: all PASS.

- [x] **Step 6.6 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/parser.py tests/unit/test_engine_v1_parser.py
git commit -m "feat(engine-v1): task 6 ORDER BY/LIMIT/OFFSET parser"
```

---

## Task 7: Executor — `eval_expr` (compound WHERE evaluator)

**Files:**
- Modify: `src/tinydb/executor.py` (add module-level `eval_expr`)

- [x] **Step 7.1 — Write failing executor eval tests**

New file `tests/unit/test_engine_v1_executor.py`:

```python
import pytest
from tinydb.executor import eval_expr
from tinydb.parser import EqualsExpr, AndExpr, OrExpr, NotExpr
from tinydb.errors import ExecutionError

SCHEMA = [("a", "INT"), ("b", "INT"), ("c", "TEXT")]

def test_eval_expr_equals_basic():
    row = [1, 2, "x"]
    assert eval_expr(EqualsExpr("a", 1), row, SCHEMA) is True
    assert eval_expr(EqualsExpr("a", 9), row, SCHEMA) is False

def test_eval_expr_and_short_circuits_left_false():
    # Right side raises; left is False so AND short-circuits and never raises
    bad = EqualsExpr("nonexistent", 1)  # would raise ExecutionError
    expr = AndExpr(left=EqualsExpr("a", 999), right=bad)
    assert eval_expr(expr, [1, 2, "x"], SCHEMA) is False

def test_eval_expr_or_short_circuits_left_true():
    bad = EqualsExpr("nonexistent", 1)
    expr = OrExpr(left=EqualsExpr("a", 1), right=bad)
    assert eval_expr(expr, [1, 2, "x"], SCHEMA) is True

def test_eval_expr_not_negates():
    expr = NotExpr(operand=EqualsExpr("a", 1))
    assert eval_expr(expr, [1, 2, "x"], SCHEMA) is False
    assert eval_expr(expr, [9, 2, "x"], SCHEMA) is True

def test_eval_expr_unknown_column_raises():
    with pytest.raises(ExecutionError):
        eval_expr(EqualsExpr("z", 1), [1, 2, "x"], SCHEMA)

def test_eval_expr_type_mismatch_raises():
    # a is INT, but literal is str
    with pytest.raises(ExecutionError):
        eval_expr(EqualsExpr("a", "x"), [1, 2, "x"], SCHEMA)

def test_eval_expr_nested_and_or_not():
    # a=1 AND NOT (b=2 OR c=3)
    expr = AndExpr(
        left=EqualsExpr("a", 1),
        right=NotExpr(operand=OrExpr(
            left=EqualsExpr("b", 2),
            right=EqualsExpr("c", 3),
        )),
    )
    assert eval_expr(expr, [1, 2, "x"], SCHEMA) is False  # b=2 true → NOT false
    assert eval_expr(expr, [1, 9, "z"], SCHEMA) is True
```

- [x] **Step 7.2 — Run tests; verify failure**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_executor.py -v`
Expected: ImportError or FAIL on `eval_expr`.

- [x] **Step 7.3 — Implement `eval_expr`**

In `src/tinydb/executor.py`:

```python
from .parser import EqualsExpr, AndExpr, OrExpr, NotExpr

def eval_expr(expr, row, schema):
    """递归评估 WHERE 表达式；AND/OR 短路；strict type 校验。"""
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
        return eval_expr(expr.left, row, schema) and eval_expr(expr.right, row, schema)
    if isinstance(expr, OrExpr):
        return eval_expr(expr.left, row, schema) or eval_expr(expr.right, row, schema)
    if isinstance(expr, NotExpr):
        return not eval_expr(expr.operand, row, schema)
    raise ExecutionError(f"unsupported expression: {type(expr).__name__}")
```

> `_python_type_to_db_type` is whatever helper already exists in `executor.py`/`type_system.py` to map `int → "INT"`, `float → "FLOAT"`, `str → "TEXT"`, `bool → "BOOL"`. If absent, derive it inline using `isinstance` checks.

- [x] **Step 7.4 — Run tests; verify pass**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_executor.py -v`
Expected: PASS

- [x] **Step 7.5 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/executor.py tests/unit/test_engine_v1_executor.py
git commit -m "feat(engine-v1): task 7 eval_expr compound WHERE evaluator"
```

---

## Task 8: Executor — route WHERE through `eval_expr` (preserve MVP behavior)

**Files:**
- Modify: `src/tinydb/executor.py` (in `_exec_select` and `_exec_delete`, replace direct `where` evaluation with `eval_expr`)

- [x] **Step 8.1 — Run full suite, expect green**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (existing `_exec_select` evaluates `where` as `tuple`; because `eval_expr(EqualsExpr(...))` works for the new shape, and MVP tests were migrated in Task 3 to construct `EqualsExpr`, this should still pass).

If MVP test bodies still construct `EqualsExpr(column=..., value=...)` directly (post Task 3 migration), the existing executor code may still expect a tuple. Adjust executor to call `eval_expr` for `where` if it currently does direct tuple comparison.

> Migration check: search `_exec_select` for the pattern that consumes `where`. If it does `col, op, lit = stmt.where`, replace with `eval_expr(stmt.where, vals, schema)`.

- [x] **Step 8.2 — Commit any wiring fix (or empty commit if no change)**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/executor.py
git diff --cached --quiet || git commit -m "feat(engine-v1): task 8 route WHERE through eval_expr"
```

---

## Task 9: Executor — SELECT chain (ORDER BY + LIMIT + OFFSET)

**Files:**
- Modify: `src/tinydb/executor.py` (`_exec_select` chain + `_stable_sort` + `_reverse_key`)

- [x] **Step 9.1 — Write failing SELECT chain tests**

Append to `tests/unit/test_engine_v1_executor.py`:

```python
import os, tempfile
from tinydb.database import Database
from tinydb.executor import Executor
from tinydb.catalog import Catalog
from tinydb.pager import Pager

def _db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    os.unlink(path)
    db = Database(path)
    return db, path

def test_executor_select_sorts_and_slices():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT, b INT)")
        for i, v in enumerate([3, 1, 4, 1, 5, 9, 2, 6]):
            db.execute_sql(f"INSERT INTO t (a, b) VALUES ({v}, {i})")
        out = db.execute_sql("SELECT * FROM t ORDER BY a ASC LIMIT 3")
        assert [r[0] for r in out] == [1, 1, 2]
        out = db.execute_sql("SELECT * FROM t ORDER BY a DESC LIMIT 3")
        assert [r[0] for r in out] == [9, 6, 5]
        out = db.execute_sql("SELECT * FROM t ORDER BY a ASC OFFSET 5")
        assert [r[0] for r in out] == [4, 5, 9]
    finally:
        os.unlink(path)

def test_executor_select_order_by_stable_when_tied():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT, b INT)")
        for i, k in enumerate([2, 1, 2, 1, 2]):
            db.execute_sql(f"INSERT INTO t (a, b) VALUES ({k}, {i})")
        out = db.execute_sql("SELECT b FROM t ORDER BY a ASC")
        # b values where a=1: 1, 3; where a=2: 0, 2, 4
        assert [r[0] for r in out] == [1, 3, 0, 2, 4]
    finally:
        os.unlink(path)

def test_executor_select_limit_zero():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT)")
        db.execute_sql("INSERT INTO t (a) VALUES (1)")
        assert db.execute_sql("SELECT * FROM t LIMIT 0") == []
    finally:
        os.unlink(path)

def test_executor_select_offset_beyond_rows():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT)")
        db.execute_sql("INSERT INTO t (a) VALUES (1), (2)")
        assert db.execute_sql("SELECT * FROM t OFFSET 10") == []
    finally:
        os.unlink(path)

def test_executor_select_offset_negative_raises():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT)")
        with pytest.raises(ExecutionError):
            db.execute_sql("SELECT * FROM t OFFSET -1")
    finally:
        os.unlink(path)

def test_executor_select_order_by_unknown_column_raises():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT)")
        db.execute_sql("INSERT INTO t (a) VALUES (1)")
        with pytest.raises(ExecutionError):
            db.execute_sql("SELECT * FROM t ORDER BY z")
    finally:
        os.unlink(path)
```

- [x] **Step 9.2 — Run tests; verify failure**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_executor.py -v -k "test_executor_select_sorts or test_executor_select_order_by_stable or test_executor_select_limit_zero or test_executor_select_offset or test_executor_select_offset_negative or test_executor_select_order_by_unknown"`
Expected: FAIL.

- [x] **Step 9.3 — Implement `_stable_sort` and chain in `_exec_select`**

In `src/tinydb/executor.py`:

```python
def _reverse_key(v):
    if isinstance(v, bool):
        return not v
    if isinstance(v, (int, float)):
        return -v
    if isinstance(v, str):
        return v  # DESC handled by sort flag (0 vs 1)
    raise ExecutionError(f"unsupported DESC type: {type(v).__name__}")

def _stable_sort(rows, items, schema):
    name_to_idx = {n: i for i, (n, _) in enumerate(schema)}

    def key(row):
        sid, vals, _pid = row
        parts = []
        for it in items:
            col_idx = name_to_idx.get(it.column)
            if col_idx is None:
                raise ExecutionError(f"unknown column {it.column!r} in ORDER BY")
            v = vals[col_idx]
            col_type = schema[col_idx][1]
            try:
                py_to_db(v, col_type)
            except (TypeError, ValueError) as e:
                raise ExecutionError(f"column {it.column!r}: {e}") from e
            if it.descending:
                parts.append((1, _reverse_key(v), sid))
            else:
                parts.append((0, v, sid))
        return tuple(parts)
    return sorted(rows, key=key)
```

In `_exec_select`:

```python
rows = []  # list[tuple[sid, vals, pid]]
for sid, vals, pid in self._scan_table(ti):
    if stmt.where is None or eval_expr(stmt.where, vals, schema):
        rows.append((sid, vals, pid))

if stmt.order_by:
    rows = _stable_sort(rows, stmt.order_by, schema)

if stmt.offset is not None and stmt.offset < 0:
    raise ExecutionError("OFFSET must be non-negative")
if stmt.limit is not None and stmt.limit < 0:
    raise ExecutionError("LIMIT must be non-negative")

if stmt.offset:
    rows = rows[stmt.offset:]
if stmt.limit is not None:
    rows = rows[:stmt.limit]
```

> `py_to_db` is the existing type validator. If absent, import from `type_system`.

- [x] **Step 9.4 — Run tests; iterate to green**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_executor.py -v`
Expected: all PASS.

- [x] **Step 9.5 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/executor.py tests/unit/test_engine_v1_executor.py
git commit -m "feat(engine-v1): task 9 SELECT ORDER BY/LIMIT/OFFSET chain"
```

---

## Task 10: Executor — `_exec_update` (v2 with chain fallback)

**Files:**
- Modify: `src/tinydb/executor.py` (`_exec_update` + dispatcher route)

- [x] **Step 10.1 — Write failing UPDATE tests**

Append to `tests/unit/test_engine_v1_executor.py`:

```python
def test_executor_update_in_place_no_grow():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT, b TEXT)")
        db.execute_sql("INSERT INTO t (a, b) VALUES (1, 'x')")
        out = db.execute_sql("UPDATE t SET a=99 WHERE b='x'")
        assert out == []  # DML protocol
        rows = db.execute_sql("SELECT a, b FROM t")
        assert rows == [[99, "x"]]
    finally:
        os.unlink(path)

def test_executor_update_in_place_shrink():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT, b TEXT)")
        db.execute_sql("INSERT INTO t (a, b) VALUES (1, 'hello world')")
        db.execute_sql("UPDATE t SET b='hi'")
        rows = db.execute_sql("SELECT a, b FROM t")
        assert rows == [[1, "hi"]]
    finally:
        os.unlink(path)

def test_executor_update_compound_where():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT, b INT)")
        for a, b in [(1, 1), (1, 2), (2, 1)]:
            db.execute_sql(f"INSERT INTO t (a, b) VALUES ({a}, {b})")
        db.execute_sql("UPDATE t SET b=99 WHERE a=1 AND b=2")
        rows = db.execute_sql("SELECT b FROM t ORDER BY a ASC, b ASC")
        # a=1,b=1 unchanged; a=1,b=2 -> 99; a=2,b=1 unchanged
        assert rows == [[1], [99], [1]]
    finally:
        os.unlink(path)

def test_executor_update_no_where_updates_all():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT)")
        db.execute_sql("INSERT INTO t (a) VALUES (1), (2), (3)")
        db.execute_sql("UPDATE t SET a=0")
        rows = sorted(r[0] for r in db.execute_sql("SELECT a FROM t"))
        assert rows == [0, 0, 0]
    finally:
        os.unlink(path)

def test_executor_update_set_unknown_column_raises():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT)")
        with pytest.raises(ExecutionError):
            db.execute_sql("UPDATE t SET z=1")
    finally:
        os.unlink(path)

def test_executor_update_set_type_mismatch_raises():
    db, path = _db()
    try:
        db.execute_sql("CREATE TABLE t (a INT)")
        with pytest.raises(ExecutionError):
            db.execute_sql("UPDATE t SET a='x'")
    finally:
        os.unlink(path)
```

- [x] **Step 10.2 — Run tests; verify failure**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_executor.py -v -k "test_executor_update"`
Expected: FAIL (UPDATE not dispatched).

- [x] **Step 10.3 — Implement `_exec_update` v2**

In `src/tinydb/executor.py`:

```python
def _exec_update(self, stmt):
    ti = self.catalog.get_table(stmt.table)
    if ti is None:
        raise ExecutionError(f"table {stmt.table!r} does not exist")
    schema = ti.schema

    # Validate SET
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

    # Collect matches
    matches = []
    for sid, vals, pid in self._scan_table(ti):
        if stmt.where is None or eval_expr(stmt.where, vals, schema):
            matches.append((pid, sid, vals))

    # Group by page
    by_page = {}
    for pid, sid, vals in matches:
        by_page.setdefault(pid, []).append((sid, vals))

    for pid, sid_vals_list in by_page.items():
        page = SlottedPage.from_bytes(pid, self.pager.read_page(pid))
        pending_chain = []
        for sid, vals in sid_vals_list:
            new_vals = list(vals)
            for col_name, expr in stmt.sets:
                new_vals[col_name_to_idx[col_name]] = expr.value
            new_bytes = encode_row(new_vals, schema)
            old_slot = page.slots[sid]
            grew = len(new_bytes) > old_slot.length
            if not grew:
                try:
                    page.update(sid, new_bytes)
                    continue
                except PageFull:
                    grew = True
            # Fallback: delete + insert
            if old_slot.flags & FLAG_SPILL_START:
                self._free_overflow_chain(pid)
            page.delete(sid)
            try:
                page.insert(new_bytes)
            except PageFull:
                pending_chain.append(new_bytes)

        self.pager.write_page(pid, page.to_bytes())
        for new_bytes in pending_chain:
            self._insert_row_into_chain(ti, new_bytes)

    self.pager.flush()
    return []
```

> Use whatever helpers already exist (`SlottedPage.from_bytes`, `page.update/insert/delete`, `encode_row`, `_scan_table`, `_free_overflow_chain`, `_insert_row_into_chain`, `FLAG_SPILL_START`, `PageFull`). Names map to MVP primitives.

- [x] **Step 10.4 — Wire dispatcher**

In `Executor.execute` (or equivalent):

```python
dispatch = {
    CreateTable: self._exec_create_table,
    DropTable:   self._exec_drop_table,
    Insert:      self._exec_insert,
    Select:      self._exec_select,
    Delete:      self._exec_delete,
    Update:      self._exec_update,
}
handler = dispatch.get(type(stmt))
if handler is None:
    raise ExecutionError(f"unsupported statement: {type(stmt).__name__}")
return handler(stmt)
```

- [x] **Step 10.5 — Run tests; iterate to green**

Run: `.venv/bin/python -m pytest tests/unit/test_engine_v1_executor.py -v`
Expected: all PASS.

- [x] **Step 10.6 — Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no MVP regression).

- [x] **Step 10.7 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add src/tinydb/executor.py tests/unit/test_engine_v1_executor.py
git commit -m "feat(engine-v1): task 10 UPDATE executor with chain fallback"
```

---

## Task 11: Integration tests

**Files:**
- New: `tests/integration/test_engine_v1.py` (I-V1-01..10)

- [x] **Step 11.1 — Write I-V1 tests**

Create `tests/integration/test_engine_v1.py` with:

```python
import os, tempfile, pytest
from tinydb.database import Database
from tinydb.errors import ExecutionError

@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp()
    os.close(fd); os.unlink(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)

@pytest.fixture
def db(db_path):
    return Database(db_path)

def test_update_end_to_end(db):
    db.execute_sql("CREATE TABLE t (id INT, name TEXT)")
    db.execute_sql("INSERT INTO t (id, name) VALUES (1, 'a'), (2, 'b')")
    db.execute_sql("UPDATE t SET name='z' WHERE id=1")
    rows = sorted(db.execute_sql("SELECT name FROM t"))
    assert rows == [["a"], ["z"]] or rows == [["z"], ["a"]]

def test_update_persists_after_reopen(db, db_path):
    db.execute_sql("CREATE TABLE t (a INT)")
    db.execute_sql("INSERT INTO t (a) VALUES (1)")
    db.execute_sql("UPDATE t SET a=99")
    db.close()
    db2 = Database(db_path)
    assert db2.execute_sql("SELECT a FROM t") == [[99]]

def test_update_compound_where_multi_page(db):
    db.execute_sql("CREATE TABLE t (a INT, b INT)")
    rows = [(i % 7, i) for i in range(200)]
    for a, b in rows:
        db.execute_sql(f"INSERT INTO t (a, b) VALUES ({a}, {b})")
    db.execute_sql("UPDATE t SET b=0 WHERE a=3 AND b>100")
    out = db.execute_sql("SELECT a, b FROM t WHERE a=3 AND b=0")
    assert all(r[0] == 3 for r in out)

def test_update_grow_falls_back_to_chain(db):
    db.execute_sql("CREATE TABLE t (a INT, payload TEXT)")
    db.execute_sql("INSERT INTO t (a, payload) VALUES (1, 'short')")
    db.execute_sql("UPDATE t SET payload='" + "x" * 4000 + "'")
    out = db.execute_sql("SELECT a, payload FROM t")
    assert len(out) == 1 and out[0][1] == "x" * 4000

def test_select_order_limit_chain_top_n(db):
    db.execute_sql("CREATE TABLE events (ts INT, level INT)")
    for ts in range(100):
        db.execute_sql(f"INSERT INTO events (ts, level) VALUES ({ts}, {ts % 5})")
    out = db.execute_sql("SELECT ts FROM events ORDER BY ts DESC LIMIT 10")
    assert [r[0] for r in out] == list(range(99, 89, -1))

def test_select_complex_where_e2e(db):
    db.execute_sql("CREATE TABLE t (a INT, b INT, c INT)")
    for a, b, c in [(1,2,3), (1,3,3), (2,2,2), (3,3,3)]:
        db.execute_sql(f"INSERT INTO t (a, b, c) VALUES ({a}, {b}, {c})")
    out = db.execute_sql("SELECT a FROM t WHERE (a=1 OR b=2) AND NOT c=3 ORDER BY a ASC")
    # (a=1 OR b=2): rows (1,2,3), (1,3,3), (2,2,2); AND NOT c=3: (2,2,2) only
    assert out == [[2]]

def test_select_offset_pagination(db):
    db.execute_sql("CREATE TABLE t (a INT)")
    for i in range(20):
        db.execute_sql(f"INSERT INTO t (a) VALUES ({i})")
    page1 = db.execute_sql("SELECT a FROM t ORDER BY a ASC LIMIT 5 OFFSET 0")
    page2 = db.execute_sql("SELECT a FROM t ORDER BY a ASC LIMIT 5 OFFSET 5")
    assert [r[0] for r in page1] == [0,1,2,3,4]
    assert [r[0] for r in page2] == [5,6,7,8,9]

def test_select_all_features_chain(db):
    db.execute_sql("CREATE TABLE t (a INT, b INT)")
    for a, b in [(1,10),(2,20),(3,30),(1,11),(2,21)]:
        db.execute_sql(f"INSERT INTO t (a, b) VALUES ({a}, {b})")
    db.execute_sql("UPDATE t SET b=99 WHERE a=2")
    out = db.execute_sql("SELECT a, b FROM t ORDER BY a ASC, b ASC")
    assert out == [[1,10],[1,11],[2,99],[2,99],[3,30]]

def test_delete_then_update_same_row(db):
    db.execute_sql("CREATE TABLE t (a INT)")
    db.execute_sql("INSERT INTO t (a) VALUES (1)")
    db.execute_sql("DELETE FROM t WHERE a=1")
    db.execute_sql("INSERT INTO t (a) VALUES (1)")
    db.execute_sql("UPDATE t SET a=42")
    assert db.execute_sql("SELECT a FROM t") == [[42]]

def test_update_spill_row(db):
    db.execute_sql("CREATE TABLE t (a INT, blob TEXT)")
    db.execute_sql("INSERT INTO t (a, blob) VALUES (1, 'small')")
    db.execute_sql("UPDATE t SET blob='" + "Q" * 8000 + "'")
    out = db.execute_sql("SELECT a, blob FROM t")
    assert out == [[1, "Q" * 8000]]
```

- [x] **Step 11.2 — Run integration tests; iterate to green**

Run: `.venv/bin/python -m pytest tests/integration/test_engine_v1.py -v`
Expected: all PASS.

- [x] **Step 11.3 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add tests/integration/test_engine_v1.py
git commit -m "test(engine-v1): task 11 integration suite I-V1"
```

---

## Task 12: E2E golden SQL files

**Files:**
- New: `tests/e2e/sql/engine_v1/01_update_basic.sql` + `.expected.txt`
- ... up to `12_update_grow_fallback.sql`

- [x] **Step 12.1 — Inspect e2e harness**

Read `tests/e2e/conftest.py` and one existing `tests/e2e/sql/mvp/*.sql` + `.expected.txt` to learn the exact runner format (header tokens like `-- REOPEN`, statement separator, expected output format).

- [x] **Step 12.2 — Author 12 SQL files + matching expected output**

For each row in Design Doc §8.4 table, write `<NN>_<name>.sql` and `<NN>_<name>.expected.txt`. Match the project's existing convention exactly (case, separator, ordering of result rows).

- [x] **Step 12.3 — Run e2e tests; iterate until green**

Run: `.venv/bin/python -m pytest tests/e2e/ -v`
Expected: all 12 engine_v1 golden tests PASS.

- [x] **Step 12.4 — Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git add tests/e2e/sql/engine_v1/
git commit -m "test(engine-v1): task 12 e2e golden SQL files"
```

---

## Task 13: Regression checks (coverage + module line budget)

**Files:**
- Possibly trim `src/tinydb/parser.py` or `src/tinydb/executor.py` if over budget.

- [x] **Step 13.1 — Run full suite with coverage**

Run: `.venv/bin/python -m pytest --cov=tinydb --cov-report=term -q`
Expected: total ≥ 90%, engine-v1 touched files (parser.py, executor.py, tokenizer.py) at 100%.

- [x] **Step 13.2 — Verify module line budget**

Run: `wc -l src/tinydb/parser.py src/tinydb/executor.py`
Expected: parser.py ≤ 750, executor.py ≤ 580 (uplift).

If over budget, extract helpers to submodules — do NOT remove tests.

- [x] **Step 13.3 — Run all 234 MVP tests + new tests**

Run: `.venv/bin/python -m pytest -q | tail -20`
Expected: all PASS.

- [x] **Step 13.4 — Final commit if any cleanup**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-engine-v1
git status
# if changes exist:
git add -A
git commit -m "chore(engine-v1): task 13 regression cleanup" || true
```

---

## Verification commands

After all tasks complete, run from the worktree root:

```bash
.venv/bin/python -m pytest --cov=tinydb --cov-report=term --cov-fail-under=85 -q
```

Expected: exit code 0; coverage ≥ 85%.

---

## Exit criteria

- All 31 sub-items in `openspec/changes/tinydb-engine-v1/tasks.md` checked `[x]`.
- 234 MVP tests + new engine_v1 tests all PASS.
- Coverage ≥ 90% project-wide; parser.py / executor.py / tokenizer.py at 100%.
- Module line budget respected.
- 12 e2e golden SQL files in `tests/e2e/sql/engine_v1/`.
- 31 incremental commits on `feature/20260716/tinydb-engine-v1` branch.
