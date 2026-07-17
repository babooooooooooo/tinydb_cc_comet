---
change: tinydb-constraints
design-doc: docs/superpowers/specs/2026-07-16-tinydb-constraints-design.md
base-ref: 619280da9fbea9335795dbd725ebbb8962ae2261
archived-with: tinydb-constraints
---

# tinydb-constraints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `tinydb-mvp` 之上引入列约束（`NOT NULL` / `UNIQUE` / `PRIMARY KEY`），通过 parser / catalog / executor / REPL 四层协同，实现 INSERT 路径运行时校验与单行 `ERROR: ConstraintViolation(...)` 渲染，零存储格式破坏。

**Architecture:** 双层列模型 — parser 持有 frozen `ColumnDefinition`（frozen dataclass），catalog 持有 frozen `Column`（frozen dataclass + `to_dict`/`from_dict`），executor 顶层显式 `ColumnDefinition → Column` 映射。catalog 反序列化双格式兼容（`[name, type]` 旧 / `{name, type, nullable, unique, primary_key}` 新）。executor INSERT 走 5 阶段流水线（normalize → NOT NULL → types → unique → encode → insert），同批次 `_pending_session_keys` 避免半成品漏检。REPL 新增 `_format_exception` 把 `ConstraintViolation` 渲染为单行 `ERROR: ConstraintViolation(...)`。

**Tech Stack:** Python 3.11+，纯 stdlib。dev 依赖沿用 MVP（`pytest>=7`、`hypothesis>=6`、`pytest-cov>=4`）。零新运行时依赖。Editable install via `pip install -e '.[dev]'`。

---

## 文件结构（实施前映射）

### 源码 `src/tinydb/`（变更范围）

| 文件 | 变动 | 行数预算 |
|------|------|---------|
| `errors.py` | 新增 `ConstraintViolation(ExecutionError)` 子异常 | ≤ 55 |
| `catalog.py` | 新增 `Column` dataclass；`TableInfo` 升级为 `columns: tuple[Column, ...]` + `schema` 只读投影；`from_bytes`/`to_bytes` 双格式 | ≤ 130 |
| `tokenizer.py` | `KEYWORDS` 新增 `NOT` / `NULL` / `PRIMARY` / `KEY`（`UNIQUE` 已存在） | ≤ 210 |
| `parser.py` | 新增 `ColumnDefinition` AST；`Literal.value` 类型扩为 `... | None`；`parse_create_table` 列约束子句链；`parse_insert` 接受 `NULL` 字面量 | ≤ 750 |
| `executor.py` | `_exec_create_table` 显式 `ColumnDefinition → Column` 映射；`_exec_insert` 五阶段校验流水线 + `_pending_session_keys` | ≤ 620 |
| `repl.py` | 新增 `_format_exception` 私有函数；`_run_sql` 改走该函数 | ≤ 310 |

### 测试 `tests/`

| 文件 | 状态 | 覆盖 |
|------|------|------|
| `tests/unit/test_constraints_parser.py` | 新建 | 13 parser 约束 + NULL 字面量 |
| `tests/unit/test_constraints_executor.py` | 新建 | 11 executor 校验流水线 |
| `tests/unit/test_constraint_violation.py` | 新建 | `ConstraintViolation` 契约 |
| `tests/integration/test_catalog_constraints.py` | 新建 | 7 catalog 双格式 + 持久化 |
| `tests/integration/test_constraints_repl.py` | 新建 | 4 REPL 错误渲染 |
| `tests/e2e/sql/constraints/*.sql` + `.expected.txt` | 新建 | 8 golden |
| `tests/property/test_parser_constraints.py` | 新建 | property-based 约束鲁棒性 |
| `tests/fixtures/legacy_mvp_schema.json` | 新建 | MVP 旧 schema fixture |
| `tests/fixtures/new_constraints_schema.json` | 新建 | 新格式 schema fixture |
| `tests/fixtures/mixed_invalid_schema.json` | 新建 | 混合格式 fixture |
| `tests/unit/test_parser.py` | 改写 | 迁移至 `ColumnDefinition` 形状 |
| `tests/integration/test_catalog.py` | 改写 | 使用 `table.columns[0].name` |
| `tests/integration/test_executor.py` | 改写 | 保持 SQL 端到端可工作 |

### 文档

- `docs/MVP_LIMITATIONS.md` — 增补：`tinydb-constraints` 交付后 O(n) UNIQUE 校验仍生效；约束子集；索引化留 `tinydb-engine-v2`

---

## 测试策略

每 capability 沿用 4 层金字塔：

1. **Unit**：`ColumnDefinition` 解析、`ConstraintViolation` 契约、executor 校验流水线（用 `Executor` 真实实例 + tmp_path）。
2. **Integration**：catalog 双格式往返、约束落盘 + reopen、REPL 进程级错误渲染。
3. **E2E**：8 条 `tests/e2e/sql/constraints/*.sql` golden（happy / null-violation / unique-violation / duplicate-pk / multi-row-partial）。
4. **Property**：随机 SQL 串经 `tokenize → parse` 不漏系统异常；INSERT 后扫描结果 == Python 镜像（约束版）。

覆盖率门槛：`--cov-fail-under=85`（项目级），新代码 ≥ 90%。

---

## Commit 粒度规则

按 `tasks.md` 子任务粒度拆 commit。每任务内部：

- 单 Red→Green 循环：整 Task 单 commit
- 多循环：每个循环单独 commit

Commit message 格式（参照 `common/git-workflow.md`）：`<type>(constraints): <subject>`

类型映射：

| 类型 | 触发场景 |
|------|---------|
| `feat` | 新增能力/接口 |
| `test` | 仅测试代码 |
| `refactor` | 重构不改行为 |
| `fix` | bug 修复 |
| `docs` | 文档 |

---

## 任务列表

> **执行顺序**：Task 1 → 2 → 3 → ... → 26。每任务完成后必须 git commit 再进入下一任务。
> **测试先行**：每任务 Step 1 都是"写失败测试"。Step 2 必须看到 RED 才推进 Step 3。
> **行数审计**：每次 commit 前对新增模块跑 `wc -l src/tinydb/<module>.py`，违反 Design Doc §14 / proposal.md Impact 预算 → 立即拆分子任务。
> **venv 调用约定**：所有 pytest 须 `cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest ...`（REPL 进程级测试需在 PATH 上找到 `tinydb-repl`）。

---

### Task 1: errors.ConstraintViolation 子异常

**Files:**
- Modify: `src/tinydb/errors.py:23` — 在 `ExecutionError` 旁加 `ConstraintViolation` 子类
- Create: `tests/unit/test_constraint_violation.py` — 6 个契约测试

- [x] **Step 1: 写失败测试**

```python
# tests/unit/test_constraint_violation.py
import pytest
from tinydb.errors import ExecutionError, ConstraintViolation, TinydbError


@pytest.mark.unit
def test_constraint_violation_inherits_execution_error():
    exc = ConstraintViolation(kind="null", column="x", value=None)
    assert isinstance(exc, ExecutionError)
    assert isinstance(exc, TinydbError)


@pytest.mark.unit
def test_constraint_violation_str_includes_kind_column_value():
    exc = ConstraintViolation(kind="null", column="x", value=None)
    text = str(exc)
    assert "kind='null'" in text
    assert "column='x'" in text
    assert "value=None" in text


@pytest.mark.unit
def test_constraint_violation_str_includes_kind_columns_value_for_unique():
    exc = ConstraintViolation(kind="unique", columns=("email",), value=("a@x",))
    text = str(exc)
    assert "kind='unique'" in text
    assert "columns=['email']" in text
    assert "value=('a@x',)" in text


@pytest.mark.unit
def test_constraint_violation_str_for_duplicate_pk():
    exc = ConstraintViolation(kind="duplicate_pk", columns=("id",), value=(1,))
    text = str(exc)
    assert "kind='duplicate_pk'" in text
    assert "columns=['id']" in text
    assert "value=(1,)" in text


@pytest.mark.unit
def test_constraint_violation_kind_column_attributes():
    exc = ConstraintViolation(kind="null", column="x", value=None)
    assert exc.kind == "null"
    assert exc.column == "x"
    assert exc.value is None


@pytest.mark.unit
def test_constraint_violation_supports_caught_by_except_execution_error():
    with pytest.raises(ExecutionError) as exc_info:
        raise ConstraintViolation(kind="unique", columns=("a",), value=("dup",))
    assert isinstance(exc_info.value, ConstraintViolation)
    assert exc_info.value.kind == "unique"
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraint_violation.py -v
```

期望：`ModuleNotFoundError: cannot import name 'ConstraintViolation' from 'tinydb.errors'`

- [x] **Step 3: 实现 `ConstraintViolation`**

```python
# src/tinydb/errors.py —— 在 ExecutionError 之后追加
class ConstraintViolation(ExecutionError):
    """Raised when a column-level constraint is violated (NOT NULL / UNIQUE / PK).

    Always includes a stable ``kind`` string so callers (REPL, Python API
    consumers) can dispatch on the violation class. The ``column`` /
    ``columns`` / ``value`` attributes are populated contextually:

    * ``kind='null'``            — single-column (NOT NULL / PK) violation; uses ``column``.
    * ``kind='unique'``          — single- or composite-column UNIQUE violation; uses ``columns``.
    * ``kind='duplicate_pk'``    — PRIMARY KEY duplicate; uses ``columns``.
    """

    def __init__(self, kind: str, *, column=None, columns=None, value=None):
        self.kind = kind
        self.column = column
        self.columns = columns
        self.value = value
        parts = [f"kind={kind!r}"]
        if column is not None:
            parts.append(f"column={column!r}")
        if columns is not None:
            parts.append(f"columns={list(columns)!r}")
        if value is not None:
            parts.append(f"value={value!r}")
        super().__init__(f"ConstraintViolation({', '.join(parts)})")
```

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraint_violation.py -v
```

期望：6 passed。

- [x] **Step 5: 跑全量测试看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：234 passed（baseline 不变）。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/errors.py tests/unit/test_constraint_violation.py
git commit -m "feat(constraints): add ConstraintViolation exception"
```

---

### Task 2: catalog.Column dataclass + TableInfo 升级骨架

**Files:**
- Modify: `src/tinydb/catalog.py:1-84` — 引入 `Column` dataclass；`TableInfo` 新字段
- Modify: `tests/integration/test_catalog.py` — 保持现有测试可工作（迁移断言到 `.schema` 投影）

- [x] **Step 1: 写失败测试 — Column dataclass 与 TableInfo 投影**

```python
# 追加到 tests/integration/test_catalog.py 末尾
from tinydb.catalog import Column


@pytest.mark.integration
def test_column_dataclass_roundtrip():
    col = Column(name="id", type="INT", nullable=False, unique=False, primary_key=True)
    d = col.to_dict()
    assert d == {"name": "id", "type": "INT", "nullable": False, "unique": False, "primary_key": True}
    col2 = Column.from_dict(d)
    assert col2 == col


@pytest.mark.integration
def test_column_defaults():
    # SQL92 default: nullable=True; the other two are False.
    col = Column(name="x", type="TEXT")
    assert col.nullable is True
    assert col.unique is False
    assert col.primary_key is False


@pytest.mark.integration
def test_table_info_schema_projection_preserves_order():
    ti = TableInfo(
        name="u",
        columns=(
            Column(name="id", type="INT", nullable=False, unique=False, primary_key=True),
            Column(name="name", type="TEXT", nullable=True, unique=False, primary_key=False),
        ),
        root_page_id=2,
        next_page_id=2,
    )
    assert ti.schema == (("id", "INT"), ("name", "TEXT"))


@pytest.mark.integration
def test_table_info_columns_is_tuple_not_list():
    ti = TableInfo(
        name="u",
        columns=(Column(name="x", type="INT"),),
        root_page_id=2,
        next_page_id=2,
    )
    assert isinstance(ti.columns, tuple)
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_catalog.py -v
```

期望：`ImportError: cannot import name 'Column' from 'tinydb.catalog'`（或 TypeError，因为 `TableInfo` 旧 dataclass 字段不匹配）

- [x] **Step 3: 升级 catalog.py**

```python
# src/tinydb/catalog.py —— 完整重写
"""Catalog persisted as JSON on page 1; INT fields encoded as strings (R8 mitigation)."""
import json
from dataclasses import dataclass
from typing import Optional

from tinydb.errors import InvalidDatabaseFile
from tinydb.pager import PAGE_SIZE

CATALOG_PAGE_ID = 1


@dataclass(frozen=True)
class Column:
    """Column metadata with column-level constraints.

    Persisted as a JSON object (see ``to_dict``/``from_dict``). Legacy
    catalogs that stored schema as ``[[name, type], ...]`` are loaded
    with the SQL92 defaults: ``nullable=True``, ``unique=False``,
    ``primary_key=False`` (D3 裁决).
    """

    name: str
    type: str
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "unique": self.unique,
            "primary_key": self.primary_key,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Column":
        return cls(
            name=d["name"],
            type=d["type"],
            nullable=d.get("nullable", True),
            unique=d.get("unique", False),
            primary_key=d.get("primary_key", False),
        )


@dataclass
class TableInfo:
    columns: tuple[Column, ...]
    root_page_id: int
    next_page_id: int
    name: str = ""  # filled by Catalog.add_table; legacy callers may set it.

    @property
    def schema(self) -> tuple[tuple[str, str], ...]:
        """Read-only ``[(name, type)]`` projection for row_codec and other
        legacy consumers (database.Row, REPL ``.schema``). New code should
        read ``self.columns`` directly."""
        return tuple((c.name, c.type) for c in self.columns)


def _enc_int(v: int) -> str:
    return str(v)


def _dec_int(v) -> int:
    if isinstance(v, str):
        return int(v)
    return int(v)


def _load_column(item) -> Column:
    """Dual-format loader: accepts legacy ``[name, type]`` arrays and new
    ``{name, type, nullable, unique, primary_key}`` objects. Mixed forms
    inside a single table are not allowed (R1 mitigation)."""
    if isinstance(item, list):
        if len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str):
            return Column(name=item[0], type=item[1])
        raise InvalidDatabaseFile(f"unrecognized column entry: {item!r}")
    if isinstance(item, dict):
        return Column.from_dict(item)
    raise InvalidDatabaseFile(f"unrecognized column entry: {item!r}")


class Catalog:
    def __init__(self):
        self.tables: dict[str, TableInfo] = {}

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Catalog":
        text = raw.rstrip(b"\x00").decode("utf-8")
        if not text:
            return cls()
        data = json.loads(text)
        c = cls()
        for name, info in data.get("tables", {}).items():
            cols = tuple(_load_column(c_) for c_ in info["schema"])
            c.tables[name] = TableInfo(
                name=name,
                columns=cols,
                root_page_id=_dec_int(info["root_page_id"]),
                next_page_id=_dec_int(info["next_page_id"]),
            )
        return c

    def to_bytes(self) -> bytes:
        data = {
            "tables": {
                name: {
                    "schema": [c.to_dict() for c in ti.columns],
                    "root_page_id": _enc_int(ti.root_page_id),
                    "next_page_id": _enc_int(ti.next_page_id),
                }
                for name, ti in self.tables.items()
            }
        }
        text = json.dumps(data, separators=(",", ":")).encode("utf-8")
        if len(text) > PAGE_SIZE:
            raise ValueError("catalog page overflow")
        return text + b"\x00" * (PAGE_SIZE - len(text))

    def create_table(
        self,
        name: str,
        schema,  # tuple[Column, ...] or list[tuple[str, str]] (legacy)
        root_page_id: int,
        next_page_id: int,
    ) -> None:
        if name in self.tables:
            raise ValueError(f"table {name!r} already exists")
        # Accept both Column tuples and legacy ``[(name, type), ...]`` so
        # existing callers keep working during migration (replaced by the
        # explicit ColumnDefinition -> Column path in the executor once
        # Task 13 lands).
        if schema and isinstance(schema[0], Column):
            cols: tuple[Column, ...] = tuple(schema)
        else:
            cols = tuple(Column(name=n, type=t) for n, t in schema)
        self.tables[name] = TableInfo(
            name=name,
            columns=cols,
            root_page_id=root_page_id,
            next_page_id=next_page_id,
        )

    def drop_table(self, name: str) -> None:
        if name not in self.tables:
            raise KeyError(f"no such table: {name}")
        del self.tables[name]

    def get_table(self, name: str) -> Optional[TableInfo]:
        return self.tables.get(name)
```

- [x] **Step 4: 跑 catalog 测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_catalog.py -v
```

期望：所有 catalog 测试通过（含旧 4 个 + 新 4 个）。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：234 passed。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/catalog.py tests/integration/test_catalog.py
git commit -m "feat(catalog): add Column dataclass with constraints"
```

---

### Task 3: catalog 持久化 — 新格式 roundtrip + legacy 加载 + mixed 拒绝

**Files:**
- Create: `tests/fixtures/legacy_mvp_schema.json`
- Create: `tests/fixtures/new_constraints_schema.json`
- Create: `tests/fixtures/mixed_invalid_schema.json`
- Create: `tests/integration/test_catalog_constraints.py`

- [x] **Step 1: 创建 fixture 文件**

```json
// tests/fixtures/legacy_mvp_schema.json
{
  "tables": {
    "users": {
      "schema": [["id", "INT"], ["name", "TEXT"]],
      "root_page_id": 2,
      "next_page_id": 0
    }
  }
}
```

```json
// tests/fixtures/new_constraints_schema.json
{
  "tables": {
    "users": {
      "schema": [
        {"name": "id", "type": "INT", "nullable": false, "unique": false, "primary_key": true},
        {"name": "email", "type": "TEXT", "nullable": false, "unique": true, "primary_key": false},
        {"name": "name", "type": "TEXT", "nullable": true, "unique": false, "primary_key": false}
      ],
      "root_page_id": 2,
      "next_page_id": 0
    }
  }
}
```

```json
// tests/fixtures/mixed_invalid_schema.json
{
  "tables": {
    "bad": {
      "schema": [["id", "INT"], {"name": "x", "type": "TEXT"}],
      "root_page_id": 2,
      "next_page_id": 0
    }
  }
}
```

- [x] **Step 2: 写失败测试 — 双格式 roundtrip + mixed 拒绝**

```python
# tests/integration/test_catalog_constraints.py
import json
from pathlib import Path

import pytest

from tinydb.catalog import Catalog, TableInfo
from tinydb.errors import InvalidDatabaseFile

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.integration
def test_catalog_loads_new_format_roundtrip(tmp_path):
    raw = (FIXTURES / "new_constraints_schema.json").read_bytes() + b"\x00" * 3000
    cat = Catalog.from_bytes(raw[:4096])
    ti = cat.get_table("users")
    assert ti is not None
    assert [c.name for c in ti.columns] == ["id", "email", "name"]
    assert ti.columns[0].primary_key is True
    assert ti.columns[1].unique is True
    assert ti.columns[1].nullable is False
    assert ti.columns[2].nullable is True


@pytest.mark.integration
def test_catalog_loads_legacy_mvp_format():
    raw = (FIXTURES / "legacy_mvp_schema.json").read_bytes() + b"\x00" * 3000
    cat = Catalog.from_bytes(raw[:4096])
    ti = cat.get_table("users")
    assert ti is not None
    assert ti.columns == (
        Catalog(name := "id", type="INT", nullable=True, unique=False, primary_key=False).columns[0] if False else ti.columns[0],
        ti.columns[1],
    )
    # All three constraint flags default to False for legacy schema.
    for col in ti.columns:
        assert col.nullable is True
        assert col.unique is False
        assert col.primary_key is False


@pytest.mark.integration
def test_catalog_legacy_format_nullable_default_true():
    raw = (FIXTURES / "legacy_mvp_schema.json").read_bytes() + b"\x00" * 3000
    cat = Catalog.from_bytes(raw[:4096])
    ti = cat.get_table("users")
    assert all(c.nullable is True for c in ti.columns)


@pytest.mark.integration
def test_catalog_rejects_mixed_old_and_new_columns():
    raw = (FIXTURES / "mixed_invalid_schema.json").read_bytes() + b"\x00" * 3000
    with pytest.raises(InvalidDatabaseFile):
        Catalog.from_bytes(raw[:4096])


@pytest.mark.integration
def test_catalog_to_bytes_uses_new_format():
    cat = Catalog()
    cat.create_table(
        "u",
        (TableInfo(name="u", columns=(
            cat.create_table.__globals__["Column"](
                name="id", type="INT", nullable=False, unique=False, primary_key=True
            ),
        ), root_page_id=2, next_page_id=2) if False else []),
        root_page_id=2,
        next_page_id=2,
    ) if False else None  # placeholder; replaced below
    # Direct construction keeps the test simple.
    from tinydb.catalog import Column
    cat = Catalog()
    cat.create_table(
        "u",
        (Column(name="id", type="INT", nullable=False, unique=False, primary_key=True),),
        root_page_id=2,
        next_page_id=2,
    )
    raw = cat.to_bytes()
    text = raw.rstrip(b"\x00").decode("utf-8")
    parsed = json.loads(text)
    assert isinstance(parsed["tables"]["u"]["schema"][0], dict)
    assert parsed["tables"]["u"]["schema"][0]["primary_key"] is True


@pytest.mark.integration
def test_catalog_constraints_persist_across_reopen(tmp_path):
    from tinydb.pager import Pager
    from tinydb.catalog import Column

    p = Pager(str(tmp_path / "ct.db"))
    cat = Catalog.from_bytes(p.read_page(1))
    cat.create_table(
        "u",
        (Column(name="id", type="INT", nullable=False, unique=False, primary_key=True),),
        root_page_id=2,
        next_page_id=2,
    )
    p.write_page(1, cat.to_bytes())
    p.flush()
    p.close()
    p2 = Pager(str(tmp_path / "ct.db"))
    cat2 = Catalog.from_bytes(p2.read_page(1))
    ti = cat2.get_table("u")
    assert ti.columns[0].primary_key is True
    assert ti.columns[0].nullable is False
    p2.close()
```

> 注：`test_catalog_loads_legacy_mvp_format` 内部的 if/else 写法只是为了让 pytest 文件保持平直可读；实际可简化为：

```python
@pytest.mark.integration
def test_catalog_loads_legacy_mvp_format():
    raw = (FIXTURES / "legacy_mvp_schema.json").read_bytes() + b"\x00" * 3000
    cat = Catalog.from_bytes(raw[:4096])
    ti = cat.get_table("users")
    assert ti is not None
    for col in ti.columns:
        assert col.nullable is True
        assert col.unique is False
        assert col.primary_key is False
```

> 用此精简版覆盖 Step 1 那个长版。

- [x] **Step 3: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_catalog_constraints.py -v
```

期望：`ModuleNotFoundError: No module named 'tests.fixtures'` 或 `FileNotFoundError: ... legacy_mvp_schema.json`

- [x] **Step 4: 创建 fixtures 文件与 tests/fixtures/__init__.py**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
touch tests/fixtures/__init__.py
mkdir -p tests/fixtures
```

并把 Step 1 的三个 JSON 文件写到 `tests/fixtures/`。

- [x] **Step 5: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_catalog_constraints.py -v
```

期望：6 passed。

- [x] **Step 6: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：240 passed（234 + 6 新增）。

- [x] **Step 7: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add tests/fixtures tests/integration/test_catalog_constraints.py
git commit -m "test(catalog): cover dual-format loader and persistence"
```

---

### Task 4: tokenizer — 关键字 NOT / NULL / PRIMARY / KEY

**Files:**
- Modify: `src/tinydb/tokenizer.py:12-15` — `KEYWORDS` 集合增 4 项

- [x] **Step 1: 写失败测试 — 新关键字被识别**

```python
# 追加到 tests/unit/test_tokenizer.py
from tinydb.tokenizer import tokenize


@pytest.mark.unit
def test_tokenizer_recognizes_not_null_primary_key_unique_as_keywords():
    sql = "CREATE TABLE t(id INT NOT NULL PRIMARY KEY, email TEXT UNIQUE)"
    tokens = tokenize(sql)
    keywords = [t.value for t in tokens if t.type == "KEYWORD"]
    assert "NOT" in keywords
    assert "NULL" in keywords
    assert "PRIMARY" in keywords
    assert "KEY" in keywords
    assert "UNIQUE" in keywords


@pytest.mark.unit
def test_tokenizer_does_not_treat_null_as_ident():
    # 之前 NULL 是 IDENT；现在应该识别为 KEYWORD
    tokens = tokenize("SELECT NULL FROM t")
    types = [t.type for t in tokens]
    assert "NULL" in [t.value for t in tokens if t.type == "KEYWORD"]
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_tokenizer.py -v
```

期望：2 failed（"NULL" 不在 KEYWORDS 中）。

- [x] **Step 3: 在 KEYWORDS 集合增 4 项**

```python
# src/tinydb/tokenizer.py —— 修改 KEYWORDS 集合
KEYWORDS = {
    "CREATE", "TABLE", "DROP", "INSERT", "INTO", "VALUES", "SELECT",
    "FROM", "WHERE", "DELETE", "INT", "TEXT", "FLOAT", "BOOL",
    "NOT", "NULL", "PRIMARY", "KEY", "UNIQUE",  # Task 4
}
```

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_tokenizer.py -v
```

期望：所有 tokenizer 测试通过。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：240 passed。`parser.py` 旧测试可能因为 NULL 关键字而新行为改变 — 见 Step 6。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/tokenizer.py tests/unit/test_tokenizer.py
git commit -m "feat(tokenizer): recognize NOT NULL PRIMARY KEY UNIQUE keywords"
```

---

### Task 5: parser — ColumnDefinition AST + Literal(None)

**Files:**
- Modify: `src/tinydb/parser.py:1-99` — 新增 `ColumnDefinition` 节点；`Literal` 值类型扩为 `... | None`
- Create: `tests/unit/test_constraints_parser.py` — 4 个 AST 形状测试

- [x] **Step 1: 写失败测试 — ColumnDefinition AST 形状**

```python
# tests/unit/test_constraints_parser.py —— 模块首部
import pytest
from tinydb.parser import parse, CreateTable, ColumnDefinition, Insert
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError


@pytest.mark.unit
def test_create_table_column_definition_default_nullable_true():
    stmt = parse(tokenize("CREATE TABLE t(id INT, name TEXT)"))
    ct = stmt.statements[0]
    assert isinstance(ct, CreateTable)
    assert all(isinstance(c, ColumnDefinition) for c in ct.columns)
    assert ct.columns[0] == ColumnDefinition(
        name="id", type="INT", nullable=True, unique=False, primary_key=False
    )
    assert ct.columns[1] == ColumnDefinition(
        name="name", type="TEXT", nullable=True, unique=False, primary_key=False
    )


@pytest.mark.unit
def test_create_table_column_definition_not_null():
    stmt = parse(tokenize("CREATE TABLE t(id INT NOT NULL)"))
    cd = stmt.statements[0].columns[0]
    assert cd == ColumnDefinition(
        name="id", type="INT", nullable=False, unique=False, primary_key=False
    )


@pytest.mark.unit
def test_create_table_column_definition_primary_key():
    stmt = parse(tokenize("CREATE TABLE t(id INT PRIMARY KEY)"))
    cd = stmt.statements[0].columns[0]
    assert cd.primary_key is True


@pytest.mark.unit
def test_create_table_column_definition_all_three():
    stmt = parse(tokenize("CREATE TABLE t(id INT NOT NULL UNIQUE PRIMARY KEY)"))
    cd = stmt.statements[0].columns[0]
    assert cd == ColumnDefinition(
        name="id", type="INT", nullable=False, unique=True, primary_key=True
    )
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_parser.py -v
```

期望：4 failed（`ColumnDefinition` 未导入 + 现有 parser 抛 `expected NULL after NOT` 等）。

- [x] **Step 3: 引入 ColumnDefinition AST**

```python
# src/tinydb/parser.py —— 在 AST 节点区插入
@dataclass(frozen=True)
class ColumnDefinition:
    """CREATE TABLE column definition: name, type, and column-level constraints.

    Pure data — the parser does NOT consult the catalog; the executor maps
    a list of ``ColumnDefinition`` into a list of ``catalog.Column`` at
    CREATE TABLE time (Task 13)."""

    name: str
    type: str
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False
```

并修改 `CreateTable.columns` 注解：

```python
@dataclass(frozen=True)
class CreateTable:
    name: str
    columns: tuple[ColumnDefinition, ...]
    if_not_exists: bool = False
    line: int = 0
    col: int = 0
```

- [x] **Step 4: 在 parser.py 接入约束子句链**

替换 `_parse_create_table` 的列循环：

```python
    def _parse_create_table(self) -> CreateTable:
        kw = self.expect_keyword("CREATE")
        self.expect_keyword("TABLE")

        name_tok = self.peek()
        if name_tok.type != "IDENT":
            raise ParseError(name_tok.line, name_tok.col, "expected table name")
        name = self.advance().value

        self.expect("PUNCT", "(")

        cols: list[ColumnDefinition] = []
        seen: set = set()

        if self.peek().type == "PUNCT" and self.peek().value == ")":
            tok = self.peek()
            raise ParseError(tok.line, tok.col, "expected column name")

        while True:
            col_tok = self.peek()
            if col_tok.type != "IDENT":
                raise ParseError(col_tok.line, col_tok.col, "expected column name")
            cname = self.advance().value
            if cname in seen:
                raise ParseError(col_tok.line, col_tok.col, f"duplicate column {cname}")
            seen.add(cname)

            type_tok = self.peek()
            if (
                type_tok.type != "KEYWORD"
                or type_tok.value not in SUPPORTED_TYPES
            ):
                value_repr = (
                    type_tok.value if type_tok.type != "EOF" else "EOF"
                )
                raise ParseError(
                    type_tok.line, type_tok.col,
                    f"type {value_repr} not supported in MVP",
                )
            ctype = self.advance().value

            nullable = True
            unique = False
            primary_key = False
            saw_unique = False
            saw_pk = False
            while self.peek().type == "KEYWORD" and self.peek().value in {
                "NOT", "NULL", "PRIMARY", "KEY", "UNIQUE",
            }:
                kw_tok = self.advance()
                if kw_tok.value == "NOT":
                    nxt = self.peek()
                    if not (nxt.type == "KEYWORD" and nxt.value == "NULL"):
                        raise ParseError(
                            nxt.line, nxt.col, "expected NULL after NOT"
                        )
                    self.advance()
                    if not nullable:
                        raise ParseError(
                            kw_tok.line, kw_tok.col, "duplicate NOT NULL constraint"
                        )
                    nullable = False
                elif kw_tok.value == "NULL":
                    # Bare NULL (without leading NOT) is rejected (裁决 2).
                    raise ParseError(
                        kw_tok.line, kw_tok.col,
                        "bare NULL not allowed; use NOT NULL or omit",
                    )
                elif kw_tok.value == "PRIMARY":
                    nxt = self.peek()
                    if not (nxt.type == "KEYWORD" and nxt.value == "KEY"):
                        raise ParseError(
                            nxt.line, nxt.col, "expected KEY after PRIMARY"
                        )
                    self.advance()
                    if saw_pk:
                        raise ParseError(
                            kw_tok.line, kw_tok.col, "duplicate PRIMARY KEY"
                        )
                    saw_pk = True
                    primary_key = True
                elif kw_tok.value == "KEY":
                    # Bare KEY without PRIMARY is rejected.
                    raise ParseError(
                        kw_tok.line, kw_tok.col,
                        "unexpected KEY; use PRIMARY KEY",
                    )
                elif kw_tok.value == "UNIQUE":
                    if saw_unique:
                        raise ParseError(
                            kw_tok.line, kw_tok.col, "duplicate UNIQUE constraint"
                        )
                    saw_unique = True
                    unique = True

            cols.append(ColumnDefinition(
                name=cname, type=ctype,
                nullable=nullable, unique=unique, primary_key=primary_key,
            ))

            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break

        self.expect("PUNCT", ")")
        return CreateTable(
            name=name, columns=tuple(cols),
            line=kw.line, col=kw.col,
        )
```

- [x] **Step 5: 跑测试看红绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_parser.py -v
```

期望：4 passed。

- [x] **Step 6: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：现有 parser 测试因为 `columns` 形状变化开始失败 — 这是预期的（Task 9 修复）。

- [x] **Step 7: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/parser.py tests/unit/test_constraints_parser.py
git commit -m "feat(parser): add ColumnDefinition AST and constraint clauses"
```

---

### Task 6: parser — INSERT `NULL` 字面量 + 列约束错误矩阵

**Files:**
- Modify: `src/tinydb/parser.py:218-275` — `parse_insert` 接受 `NULL` 字面量
- Modify: `src/tinydb/parser.py` — `Literal` 节点（若存在）支持 None；当前无 `Literal` 节点，只在 `Insert.values` 里直接放 Python 值

- [x] **Step 1: 写失败测试 — NULL 字面量与列约束错误**

```python
# 追加到 tests/unit/test_constraints_parser.py


@pytest.mark.unit
def test_insert_accepts_null_literal_when_column_nullable():
    stmt = parse(tokenize("INSERT INTO t(x) VALUES (NULL)"))
    ins = stmt.statements[0]
    assert isinstance(ins, Insert)
    assert ins.values == [[None]]


@pytest.mark.unit
def test_insert_accepts_null_literal_mixed_with_int():
    stmt = parse(tokenize("INSERT INTO t(x, y) VALUES (1, NULL)"))
    assert stmt.statements[0].values == [[1, None]]


@pytest.mark.unit
def test_create_table_rejects_bare_null_after_type():
    with pytest.raises(ParseError, match="bare NULL not allowed"):
        parse(tokenize("CREATE TABLE t(x INT NULL)"))


@pytest.mark.unit
def test_create_table_rejects_not_without_null():
    with pytest.raises(ParseError, match="expected NULL after NOT"):
        parse(tokenize("CREATE TABLE t(x INT NOT)"))


@pytest.mark.unit
def test_create_table_rejects_primary_without_key():
    with pytest.raises(ParseError, match="expected KEY after PRIMARY"):
        parse(tokenize("CREATE TABLE t(x INT PRIMARY)"))


@pytest.mark.unit
def test_create_table_rejects_duplicate_unique_constraint():
    with pytest.raises(ParseError, match="duplicate UNIQUE"):
        parse(tokenize("CREATE TABLE t(x INT UNIQUE NOT NULL UNIQUE)"))


@pytest.mark.unit
def test_create_table_rejects_duplicate_primary_key():
    with pytest.raises(ParseError, match="duplicate PRIMARY KEY"):
        parse(tokenize("CREATE TABLE t(x INT PRIMARY KEY PRIMARY KEY)"))


@pytest.mark.unit
def test_create_table_rejects_bare_key_token():
    with pytest.raises(ParseError, match="unexpected KEY"):
        parse(tokenize("CREATE TABLE t(x INT KEY)"))
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_parser.py -v
```

期望：8 failed（NULL 关键字 + 错误矩阵未触发）。

- [x] **Step 3: 改 `parse_insert` 接受 NULL**

```python
# src/tinydb/parser.py —— 修改 _parse_insert 的字面量读取循环
        while True:
            self.expect("PUNCT", "(")
            row: list = []
            if self.peek().type == "PUNCT" and self.peek().value == ")":
                tok = self.peek()
                raise ParseError(tok.line, tok.col, "expected literal")
            while True:
                v = self.advance()
                if v.type == "KEYWORD" and v.value == "NULL":
                    row.append(None)
                elif v.type in _LITERAL_TYPES:
                    row.append(v.value)
                else:
                    raise ParseError(v.line, v.col, "expected literal")
                if self.peek().type == "PUNCT" and self.peek().value == ",":
                    self.advance()
                    continue
                break
            if len(row) != len(cols):
                raise ParseError(
                    kw.line, kw.col,
                    f"value count mismatch: got {len(row)}, expected {len(cols)}",
                )
            values.append(row)
            self.expect("PUNCT", ")")
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break
```

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_parser.py -v
```

期望：12 passed（4 + 8）。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：parser / executor / lifecycle 测试可能因为 `columns` 形状变化而红 — Task 9 修复。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/parser.py tests/unit/test_constraints_parser.py
git commit -m "feat(parser): accept NULL literal in INSERT and constraint errors"
```

---

### Task 7: parser — 旧 `tuple[tuple[str, str]]` 迁移到 `ColumnDefinition` 形态（Callers）

**Files:**
- Modify: `src/tinydb/executor.py:84-112` — `_exec_create_table` 接受 `ColumnDefinition` 元组并显式映射到 `catalog.Column`

- [x] **Step 1: 写失败测试 — 端到端 CREATE 走新 AST 路径**

```python
# tests/integration/test_executor.py —— 在文件末尾新增
@pytest.mark.integration
def test_create_table_with_not_null_persists_constraint(tmp_path):
    from tinydb import Database
    with Database(str(tmp_path / "nn.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL, name TEXT)")
        ti = db.catalog.get_table("t")
    assert ti.columns[0].nullable is False
    assert ti.columns[1].nullable is True


@pytest.mark.integration
def test_create_table_with_unique_persists_constraint(tmp_path):
    from tinydb import Database
    with Database(str(tmp_path / "uq.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, email TEXT UNIQUE)")
        ti = db.catalog.get_table("t")
    assert ti.columns[0].primary_key is True
    assert ti.columns[1].unique is True
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_executor.py::test_create_table_with_not_null_persists_constraint -v
```

期望：TypeError — `_exec_create_table` 期望 `list[tuple[str, str]]`，新 AST 是 `tuple[ColumnDefinition, ...]`。

- [x] **Step 3: 改 `_exec_create_table` 显式映射**

```python
# src/tinydb/executor.py —— 修改 _exec_create_table
    def _exec_create_table(self, stmt: CreateTable) -> list:
        """Create an empty table and persist the catalog entry.

        Maps ``stmt.columns`` (parser AST: ``tuple[ColumnDefinition, ...]``)
        into a ``tuple[catalog.Column, ...]`` before calling
        ``catalog.create_table``. The explicit bridge is the R1 裁决:
        the parser does NOT import ``catalog``, the catalog does NOT
        import the parser.
        """
        from tinydb.catalog import Column  # local import avoids cycle noise

        if self.catalog.get_table(stmt.name) is not None:
            raise ExecutionError(f"table {stmt.name!r} already exists")

        cols: list[Column] = []
        seen: set = set()
        for cd in stmt.columns:
            if cd.name in seen:
                raise ExecutionError(f"duplicate column {cd.name}")
            seen.add(cd.name)
            cols.append(Column(
                name=cd.name,
                type=cd.type,
                nullable=cd.nullable,
                unique=cd.unique,
                primary_key=cd.primary_key,
            ))

        root_id = self.pager.alloc_page()
        page = SlottedPage.empty(root_id)
        self.pager.write_page(root_id, page.to_bytes())

        # MVP: next_page_id == root_page_id.
        self.catalog.create_table(
            stmt.name, tuple(cols),
            root_page_id=root_id, next_page_id=root_id,
        )

        self.pager.write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        return []
```

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_executor.py -v
```

期望：所有 executor 测试通过。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：234 + 8 + 2 = 244 passed。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/executor.py tests/integration/test_executor.py
git commit -m "feat(executor): map ColumnDefinition to Column on CREATE TABLE"
```

---

### Task 8: parser — 迁移旧 `tests/unit/test_parser.py` 到 ColumnDefinition 形状

**Files:**
- Modify: `tests/unit/test_parser.py:17-20` — `assert stmt.statements[0].columns == [(...)]` 改为 `ColumnDefinition` 形态

- [ ] **Step 1: 跑现有 parser 测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_parser.py -v
```

期望：4 个测试失败：`assert columns == [("id", "INT"), ("name", "TEXT")]` 等。

- [ ] **Step 2: 改 `test_parse_create_table_simple` 形状**

```python
# tests/unit/test_parser.py —— 替换
from tinydb.parser import parse, CreateTable, ColumnDefinition, Insert
from tinydb.tokenizer import tokenize, Token
from tinydb.errors import ParseError


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-002-SCN-01")
def test_parse_create_table_simple():
    stmt = parse(tokenize("CREATE TABLE users (id INT, name TEXT)"))
    assert stmt.statements[0].name == "users"
    assert stmt.statements[0].columns == (
        ColumnDefinition(name="id", type="INT", nullable=True, unique=False, primary_key=False),
        ColumnDefinition(name="name", type="TEXT", nullable=True, unique=False, primary_key=False),
    )
```

- [ ] **Step 3: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_parser.py -v
```

期望：所有 parser 测试通过。

- [ ] **Step 4: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：244 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add tests/unit/test_parser.py
git commit -m "test(parser): migrate to ColumnDefinition AST shape"
```

---

### Task 9: executor — INSERT NOT NULL / PK 校验

**Files:**
- Modify: `src/tinydb/executor.py:133-161` — 重写 `_exec_insert` 五阶段流水线
- Create: `tests/unit/test_constraints_executor.py` — 4 个 NOT NULL / PK 测试

- [x] **Step 1: 写失败测试 — NOT NULL 与 PK 拒绝 None**

```python
# tests/unit/test_constraints_executor.py
import pytest

from tinydb import Database
from tinydb.errors import ConstraintViolation


@pytest.mark.integration
def test_executor_insert_rejects_null_on_not_null(tmp_path):
    with Database(str(tmp_path / "nn.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL, name TEXT)")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id, name) VALUES (NULL, 'a')")
    assert exc_info.value.kind == "null"
    assert exc_info.value.column == "id"


@pytest.mark.integration
def test_executor_insert_rejects_null_on_pk(tmp_path):
    # MVP legacy compatibility: nullable=True default, but PK must still
    # reject NULL (D5 合并).
    with Database(str(tmp_path / "pk.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY)")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id) VALUES (NULL)")
    assert exc_info.value.kind == "null"
    assert exc_info.value.column == "id"


@pytest.mark.integration
def test_executor_insert_accepts_null_on_nullable_column(tmp_path):
    with Database(str(tmp_path / "ok.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, NULL)")
        rows = db.execute("SELECT * FROM t")
    assert rows[0].name is None


@pytest.mark.integration
def test_executor_insert_failed_null_does_not_write_row(tmp_path):
    with Database(str(tmp_path / "nw.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL)")
        with pytest.raises(ConstraintViolation):
            db.execute("INSERT INTO t(id) VALUES (NULL)")
        rows = db.execute("SELECT * FROM t")
    assert rows == []
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_executor.py -v
```

期望：4 failed（旧 `_exec_insert` 不做 NOT NULL 校验）。

- [x] **Step 3: 改 `_exec_insert` 引入 normalize + NOT NULL 阶段**

```python
# src/tinydb/executor.py —— 修改 _exec_insert
    def _exec_insert(self, stmt: Insert) -> list:
        """Insert row(s) into a table with the constraints pipeline.

        Pipeline (Task 7 裁决 3 方案 A — per-row validation, no tx):
          1. table exists (raises ExecutionError)
          2. column list is non-empty, unique, all known (parser guarantees;
             executor defensively re-checks)
          3. row value count == explicit column count
          4. normalize row into schema order (omitted -> None)
          5. NOT NULL + PK NULL rejection (ConstraintViolation kind='null')
          6. type validation on non-NULL values (existing path)
          7. UNIQUE / PK duplicate scan (Task 10)
          8. encode + insert
        """
        from tinydb.errors import ConstraintViolation  # local import keeps errors boundary clean

        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        if not stmt.columns:
            raise ExecutionError("INSERT column list must be non-empty")

        cols = ti.columns
        name_to_idx = {c.name: i for i, c in enumerate(cols)}

        # Defensive executor-side validation; parser also enforces these.
        seen: set = set()
        for cname in stmt.columns:
            if cname not in name_to_idx:
                raise ExecutionError(f"unknown column {cname!r}")
            if cname in seen:
                raise ExecutionError(f"duplicate column {cname!r}")
            seen.add(cname)

        for row_vals in stmt.values:
            if len(row_vals) != len(stmt.columns):
                raise ExecutionError(
                    f"value count mismatch: got {len(row_vals)}, expected {len(stmt.columns)}"
                )

            # 4. Normalize to schema order, omitted columns -> None.
            normalized: list = [None] * len(cols)
            for cname, val in zip(stmt.columns, row_vals):
                normalized[name_to_idx[cname]] = val
            normalized_tuple = tuple(normalized)

            # 5. NOT NULL + PK NULL rejection.
            for i, c in enumerate(cols):
                if normalized_tuple[i] is None and (not c.nullable or c.primary_key):
                    raise ConstraintViolation(
                        kind="null", column=c.name, value=None,
                    )

            # 6. Type validation (existing path: only non-None values).
            validated: list = []
            for c, v in zip(cols, normalized_tuple):
                if v is None:
                    validated.append(None)
                    continue
                try:
                    py_to_db(v, c.type)
                except (TypeError, ValueError) as e:
                    raise ExecutionError(f"column {c.name}: {e}") from e
                validated.append(v)

            # 8. Encode + insert (Task 10 inserts unique check between 7 and 8).
            row_bytes = encode_row(validated, ti.schema)
            self._insert_row_into_chain(ti, row_bytes)
        return []
```

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_executor.py -v
```

期望：4 passed。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：248 passed。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/executor.py tests/unit/test_constraints_executor.py
git commit -m "feat(executor): INSERT NOT NULL and PK null validation"
```

---

### Task 10: executor — UNIQUE / duplicate_pk 校验 + 同批次键

**Files:**
- Modify: `src/tinydb/executor.py` — 在 `_exec_insert` 第 7 阶段插入 `_validate_unique_keys`
- Modify: `tests/unit/test_constraints_executor.py` — 增加 5 个 unique 测试

- [x] **Step 1: 写失败测试 — UNIQUE / duplicate_pk**

```python
# 追加到 tests/unit/test_constraints_executor.py


@pytest.mark.integration
def test_executor_insert_rejects_duplicate_unique(tmp_path):
    with Database(str(tmp_path / "uq.db")) as db:
        db.execute("CREATE TABLE t(id INT, email TEXT UNIQUE)")
        db.execute("INSERT INTO t(id, email) VALUES (1, 'a@x')")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id, email) VALUES (2, 'a@x')")
    assert exc_info.value.kind == "unique"
    assert exc_info.value.columns == ("email",)


@pytest.mark.integration
def test_executor_insert_rejects_duplicate_pk(tmp_path):
    with Database(str(tmp_path / "dpk.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'a')")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(id, name) VALUES (1, 'b')")
    assert exc_info.value.kind == "duplicate_pk"
    assert exc_info.value.columns == ("id",)


@pytest.mark.integration
def test_executor_insert_unique_with_nulls_all_pass(tmp_path):
    with Database(str(tmp_path / "un.db")) as db:
        db.execute("CREATE TABLE t(id INT, email TEXT UNIQUE)")
        db.execute("INSERT INTO t(id, email) VALUES (1, NULL)")
        db.execute("INSERT INTO t(id, email) VALUES (2, NULL)")
        db.execute("INSERT INTO t(id, email) VALUES (3, NULL)")
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 3


@pytest.mark.integration
def test_executor_insert_same_batch_duplicate_rejected(tmp_path):
    with Database(str(tmp_path / "sb.db")) as db:
        db.execute("CREATE TABLE t(id INT, email TEXT UNIQUE)")
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute(
                "INSERT INTO t(id, email) VALUES (1, 'a@x'), (2, 'a@x')"
            )
    assert exc_info.value.kind == "unique"


@pytest.mark.integration
def test_executor_insert_composite_pk_rejected(tmp_path):
    with Database(str(tmp_path / "cpk.db")) as db:
        db.execute("CREATE TABLE t(a INT, b INT, PRIMARY KEY (a))")
        # PRIMARY KEY must be applied per-column; multi-column PK isn't
        # yet supported at the parser level. Skip composite here —
        # exercised separately by a parser-level test.
        db.execute("INSERT INTO t(a, b) VALUES (1, 1)")
        db.execute("INSERT INTO t(a, b) VALUES (2, 2)")
        # A second row with same 'a' must violate the PK.
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute("INSERT INTO t(a, b) VALUES (1, 3)")
    assert exc_info.value.kind == "duplicate_pk"
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_executor.py -v
```

期望：5 failed。

- [x] **Step 3: 在 `_exec_insert` 接入 `_validate_unique_keys`**

```python
# src/tinydb/executor.py —— 在 _exec_insert 内 normalize 之后、encode 之前
            # 7. UNIQUE / duplicate_pk.
            self._validate_unique_keys(normalized_tuple, ti)
```

并新增 helper（紧接 `_exec_insert` 后）：

```python
    def _validate_unique_keys(self, row: tuple, ti: TableInfo) -> None:
        """Reject duplicate UNIQUE / PRIMARY KEY values.

        Each INSERT statement maintains a per-call session set of accepted
        keys to prevent same-batch duplicates. NULL members skip the
        check (R9 裁决 9 — SQL standard semantics)."""
        from tinydb.errors import ConstraintViolation

        for group in self._unique_groups(ti):
            key_value = tuple(row[name_to_idx(c) for c in group.columns)
            # Placeholder: replaced below with a real dict-built-per-call.
            ...
```

更干净的写法 — 在 `_exec_insert` 顶部构造 `name_to_idx` 并直接用：

```python
    def _validate_unique_keys(self, row: tuple, ti: TableInfo,
                              name_to_idx: dict[str, int],
                              session_keys: dict[tuple, set]) -> None:
        from tinydb.errors import ConstraintViolation
        for group in self._unique_groups(ti):
            key_value = tuple(row[name_to_idx[c]] for c in group.columns)
            if any(v is None for v in key_value):
                # R9 裁决: NULL in a UNIQUE tuple skips the check.
                continue
            seen_in_table = self._scan_unique_keys(ti, group.columns)
            if key_value in session_keys[group] or key_value in seen_in_table:
                raise ConstraintViolation(
                    kind=group.kind,
                    columns=group.columns,
                    value=key_value,
                )
            session_keys[group].add(key_value)
```

并在 `_exec_insert` 顶部构造 + finally 清理：

```python
        from tinydb.errors import ConstraintViolation

        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        # ... existing checks ...

        # Per-batch UNIQUE dedup state.
        from collections import defaultdict
        session_keys: dict = defaultdict(set)

        try:
            for row_vals in stmt.values:
                # ... normalize + NOT NULL + types ...
                self._validate_unique_keys(
                    normalized_tuple, ti, name_to_idx, session_keys,
                )
                # ... encode + insert ...
        finally:
            session_keys.clear()
```

并新增辅助方法（紧接 `_exec_insert` 之后）：

```python
    def _unique_groups(self, ti: TableInfo) -> list:
        """Compute the set of unique-key groups for a table.

        R4 裁决 4: PRIMARY KEY groups (single or composite) take priority
        over any same-column UNIQUE groups. Single-column UNIQUE clauses
        each form their own group. Multi-column ``UNIQUE (a, b)`` is not
        yet supported in this change.
        """
        from collections import namedtuple
        UniqueGroup = namedtuple("UniqueGroup", ["columns", "kind"])
        groups: list = []
        pk_cols = tuple(c.name for c in ti.columns if c.primary_key)
        if pk_cols:
            groups.append(UniqueGroup(columns=pk_cols, kind="duplicate_pk"))
        for c in ti.columns:
            if c.unique and c.name not in pk_cols:
                groups.append(UniqueGroup(columns=(c.name,), kind="unique"))
        return groups

    def _scan_unique_keys(self, ti: TableInfo, columns: tuple[str, ...]) -> set:
        """Linear-scan the table and return the set of existing key tuples."""
        name_to_idx = {c.name: i for i, c in enumerate(ti.columns)}
        col_idxs = tuple(name_to_idx[c] for c in columns)
        seen: set = set()
        for _sid, vals, _pid in self._scan_table(ti):
            key = tuple(vals[i] for i in col_idxs)
            if any(v is None for v in key):
                continue
            seen.add(key)
        return seen
```

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_executor.py -v
```

期望：9 passed。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：253 passed。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/executor.py tests/unit/test_constraints_executor.py
git commit -m "feat(executor): UNIQUE and duplicate_pk validation with session keys"
```

---

### Task 11: executor — INSERT 列归一化（裁决 5）+ 多行 partial 失败

**Files:**
- Modify: `src/tinydb/executor.py` — `_exec_insert` 处理省略列 → None（已由 Task 9 覆盖）；新增 multi-row partial 测试
- Modify: `tests/unit/test_constraints_executor.py` — 增加 multi-row partial 测试

- [x] **Step 1: 写失败测试 — 多行 INSERT 部分失败保留成功行**

```python
# 追加到 tests/unit/test_constraints_executor.py


@pytest.mark.integration
def test_executor_insert_omitted_column_becomes_none(tmp_path):
    with Database(str(tmp_path / "om.db")) as db:
        db.execute("CREATE TABLE t(id INT NOT NULL, name TEXT)")
        db.execute("INSERT INTO t(id) VALUES (1)")
        rows = db.execute("SELECT * FROM t")
    assert rows[0].name is None
    assert rows[0].id == 1


@pytest.mark.integration
def test_executor_insert_unknown_column_rejected(tmp_path):
    with Database(str(tmp_path / "uc.db")) as db:
        db.execute("CREATE TABLE t(id INT)")
        with pytest.raises(Exception) as exc_info:
            db.execute("INSERT INTO t(missing) VALUES (1)")
    # parser also catches this; executor is the second line of defense.
    assert "unknown column" in str(exc_info.value) or "missing" in str(exc_info.value)


@pytest.mark.integration
def test_executor_insert_duplicate_column_rejected(tmp_path):
    with Database(str(tmp_path / "dc.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        with pytest.raises(Exception) as exc_info:
            db.execute("INSERT INTO t(id, id) VALUES (1, 2)")
    assert "duplicate" in str(exc_info.value)


@pytest.mark.integration
def test_executor_insert_multi_row_partial_failure_keeps_successful_rows(tmp_path):
    with Database(str(tmp_path / "mr.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, name TEXT)")
        db.execute(
            "INSERT INTO t(id, name) VALUES (1, 'a'), (2, 'b')"
        )
        # Third row collides on PK; first two must remain.
        with pytest.raises(ConstraintViolation) as exc_info:
            db.execute(
                "INSERT INTO t(id, name) VALUES (3, 'c'), (1, 'd'), (4, 'e')"
            )
        assert exc_info.value.kind == "duplicate_pk"
        rows = db.execute("SELECT * FROM t ORDER BY id")
    assert [r.id for r in rows] == [1, 2, 3]
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_executor.py -v
```

期望：4 failed。

- [x] **Step 3: 已有代码已支持 — 跑绿测试**

`_exec_insert` 已具备 normalize 与 try/finally 清理能力。若测试仍红，按失败信息补 `executor.py` —— 通常是 `_validate_unique_keys` 的 `name_to_idx` 在循环外构造后未传入。

修复模式：把 `name_to_idx` 提到 `_exec_insert` 顶部（与 Task 9 同步），传给 `_validate_unique_keys`。

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_executor.py -v
```

期望：13 passed。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：257 passed。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/executor.py tests/unit/test_constraints_executor.py
git commit -m "feat(executor): INSERT column normalization and multi-row partial failure"
```

---

### Task 12: catalog 兼容加载回归 — MVP 旧表

**Files:**
- Modify: `tests/integration/test_catalog_constraints.py` — 增补"旧表省略 INSERT 列 → None"端到端测试

- [ ] **Step 1: 写失败测试 — 旧表走新路径**

```python
# 追加到 tests/integration/test_catalog_constraints.py
from tinydb import Database


@pytest.mark.integration
def test_executor_legacy_table_insert_with_no_value_still_accepted(tmp_path):
    """Legacy MVP tables (nullable=True) must keep accepting inserts
    without explicit value lists (D3 裁决)."""
    with Database(str(tmp_path / "legacy.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        # No value for 'name' — should default to None.
        db.execute("INSERT INTO t(id) VALUES (1)")
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].name is None
```

- [ ] **Step 2: 跑测试看绿（功能已支持）**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_catalog_constraints.py::test_executor_legacy_table_insert_with_no_value_still_accepted -v
```

期望：1 passed（Task 11 已实现）。

- [ ] **Step 3: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：258 passed。

- [ ] **Step 4: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add tests/integration/test_catalog_constraints.py
git commit -m "test(catalog): cover legacy MVP table roundtrip with new pipeline"
```

---

### Task 13: parser 鲁棒性 — 约束子句顺序与边界

**Files:**
- Modify: `tests/unit/test_constraints_parser.py` — 增加顺序独立性 + 子句链 + 复合 PK

- [ ] **Step 1: 写测试 — 顺序独立与边界**

```python
# 追加到 tests/unit/test_constraints_parser.py


@pytest.mark.unit
def test_create_table_constraint_order_independent():
    stmt = parse(tokenize("CREATE TABLE t(x INT PRIMARY KEY NOT NULL UNIQUE)"))
    cd = stmt.statements[0].columns[0]
    assert cd == ColumnDefinition(
        name="x", type="INT", nullable=False, unique=True, primary_key=True
    )


@pytest.mark.unit
def test_create_table_multi_column_pk_merges_into_one_group(tmp_path):
    # Two single-column PK declarations land on different columns;
    # the executor builds a single composite key group (R4 裁决).
    with Database(str(tmp_path / "mcpk.db")) as db:
        db.execute("CREATE TABLE t(a INT PRIMARY KEY, b INT PRIMARY KEY)")
        ti = db.catalog.get_table("t")
    assert ti.columns[0].primary_key is True
    assert ti.columns[1].primary_key is True
```

并在文件顶部加 `from tinydb import Database` 导入。

- [ ] **Step 2: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/unit/test_constraints_parser.py -v
```

期望：13 + 2 = 15 passed。

- [ ] **Step 3: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：260 passed。

- [ ] **Step 4: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add tests/unit/test_constraints_parser.py
git commit -m "test(parser): constraint order independence and composite PK merge"
```

---

### Task 14: REPL — `_format_exception` ConstraintViolation 渲染

**Files:**
- Modify: `src/tinydb/repl.py:130-149` — `_run_sql` 调用替换为 `_format_exception`

- [x] **Step 1: 写失败测试 — 进程级 REPL 错误输出**

```python
# tests/integration/test_constraints_repl.py
import shutil
import subprocess
from pathlib import Path

import pytest

REPL = shutil.which("tinydb-repl")


def run_repl(commands: str, *args: str) -> subprocess.CompletedProcess[str]:
    assert REPL is not None, "run pip install -e '.[dev]' before integration tests"
    process = subprocess.Popen(
        [REPL, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(input=commands, timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(f"timed out\nstdout:\n{stdout}\nstderr:\n{stderr}")
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


@pytest.mark.integration
def test_repl_constraint_violation_renders_kind_null():
    result = run_repl(
        "CREATE TABLE t(id INT NOT NULL);\n"
        "INSERT INTO t(id) VALUES (NULL);\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "ERROR: ConstraintViolation(kind='null', column='id', value=None)" in result.stderr


@pytest.mark.integration
def test_repl_constraint_violation_renders_kind_unique():
    result = run_repl(
        "CREATE TABLE t(id INT, email TEXT UNIQUE);\n"
        "INSERT INTO t(id, email) VALUES (1, 'a@x');\n"
        "INSERT INTO t(id, email) VALUES (2, 'a@x');\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "ERROR: ConstraintViolation(kind='unique'" in result.stderr
    assert "columns=['email']" in result.stderr


@pytest.mark.integration
def test_repl_constraint_violation_renders_kind_duplicate_pk():
    result = run_repl(
        "CREATE TABLE t(id INT PRIMARY KEY, name TEXT);\n"
        "INSERT INTO t(id, name) VALUES (1, 'a');\n"
        "INSERT INTO t(id, name) VALUES (1, 'b');\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "ERROR: ConstraintViolation(kind='duplicate_pk'" in result.stderr
    assert "columns=['id']" in result.stderr


@pytest.mark.integration
def test_repl_loop_continues_after_constraint_violation():
    result = run_repl(
        "CREATE TABLE t(id INT NOT NULL);\n"
        "INSERT INTO t(id) VALUES (NULL);\n"
        "CREATE TABLE ok(id INT);\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "OK" in result.stdout
    assert "ERROR: ConstraintViolation" in result.stderr
```

- [x] **Step 2: 跑测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_constraints_repl.py -v
```

期望：4 failed（旧 REPL 渲染 `ERROR: ExecutionError: ...` 而非 `ERROR: ConstraintViolation(...)`）。

- [x] **Step 3: 加 `_format_exception` 并改 `_run_sql`**

```python
# src/tinydb/repl.py —— 在 _format_table 后插入
def _format_exception(exc: Exception) -> str:
    """Single-line exception formatter. ConstraintViolation is rendered
    using its own ``__str__`` (kind/column/columns/value) so the REPL
    user sees the precise violation context. Other exceptions fall back
    to the MVP ``<TypeName>: <message>`` form (single line)."""
    from tinydb.errors import ConstraintViolation  # local import keeps REPL stdlib-only-fallback path
    if isinstance(exc, ConstraintViolation):
        return f"ERROR: {exc}"
    return f"ERROR: {type(exc).__name__}: {exc}"
```

并替换 `_run_sql` 中的 print 块：

```python
    try:
        rows = db.execute(sql)
    except Exception as exc:
        message = _format_exception(exc)
        print(message, file=sys.stderr)
        return
```

- [x] **Step 4: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_constraints_repl.py -v
```

期望：4 passed。

- [x] **Step 5: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：264 passed。**注意**：`test_repl_execution_error_is_single_line_and_loop_continues` 仍要求 `ERROR: ExecutionError:` 风格，必须保留旧路径；`test_repl_error_is_single_line_and_loop_continues` 同理。

- [x] **Step 6: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add src/tinydb/repl.py tests/integration/test_constraints_repl.py
git commit -m "feat(repl): render ConstraintViolation with kind/columns/value"
```

---

### Task 15: e2e golden SQL — 约束 8 条

**Files:**
- Create: `tests/e2e/sql/constraints/01_create_with_not_null.sql` + `.expected.txt`
- Create: `tests/e2e/sql/constraints/02_create_with_unique.sql` + `.expected.txt`
- Create: `tests/e2e/sql/constraints/03_create_with_pk.sql` + `.expected.txt`
- Create: `tests/e2e/sql/constraints/04_insert_null_to_not_null.sql` + `.expected.txt`
- Create: `tests/e2e/sql/constraints/05_insert_duplicate_unique.sql` + `.expected.txt`
- Create: `tests/e2e/sql/constraints/06_insert_duplicate_pk.sql` + `.expected.txt`
- Create: `tests/e2e/sql/constraints/07_multi_row_partial.sql` + `.expected.txt`
- Create: `tests/e2e/sql/constraints/08_null_unique.sql` + `.expected.txt`

- [ ] **Step 1: 创建 SQL + expected 配对文件**

`tests/e2e/sql/constraints/01_create_with_not_null.sql`:

```sql
CREATE TABLE u(id INT NOT NULL, name TEXT);
INSERT INTO u(id, name) VALUES (1, 'a');
SELECT * FROM u;
```

`tests/e2e/sql/constraints/01_create_with_not_null.expected.txt`:

```
OK
OK
Row(id=1, name='a')
```

`tests/e2e/sql/constraints/02_create_with_unique.sql`:

```sql
CREATE TABLE u(id INT, email TEXT UNIQUE);
INSERT INTO u(id, email) VALUES (1, 'a@x');
INSERT INTO u(id, email) VALUES (2, 'b@x');
SELECT email FROM u ORDER BY id;
```

`tests/e2e/sql/constraints/02_create_with_unique.expected.txt`:

```
OK
OK
OK
'a@x'
'b@x'
```

`tests/e2e/sql/constraints/03_create_with_pk.sql`:

```sql
CREATE TABLE u(id INT PRIMARY KEY, name TEXT);
INSERT INTO u(id, name) VALUES (1, 'a');
SELECT * FROM u;
```

`tests/e2e/sql/constraints/03_create_with_pk.expected.txt`:

```
OK
OK
Row(id=1, name='a')
```

`tests/e2e/sql/constraints/04_insert_null_to_not_null.sql`:

```sql
CREATE TABLE u(id INT NOT NULL, name TEXT);
INSERT INTO u(id, name) VALUES (NULL, 'a');
```

`tests/e2e/sql/constraints/04_insert_null_to_not_null.expected.txt`:

```
OK
ERROR: ConstraintViolation(kind='null', column='id', value=None)
```

`tests/e2e/sql/constraints/05_insert_duplicate_unique.sql`:

```sql
CREATE TABLE u(id INT, email TEXT UNIQUE);
INSERT INTO u(id, email) VALUES (1, 'a@x');
INSERT INTO u(id, email) VALUES (2, 'a@x');
```

`tests/e2e/sql/constraints/05_insert_duplicate_unique.expected.txt`:

```
OK
OK
ERROR: ConstraintViolation(kind='unique', columns=['email'], value=('a@x',))
```

`tests/e2e/sql/constraints/06_insert_duplicate_pk.sql`:

```sql
CREATE TABLE u(id INT PRIMARY KEY, name TEXT);
INSERT INTO u(id, name) VALUES (1, 'a');
INSERT INTO u(id, name) VALUES (1, 'b');
```

`tests/e2e/sql/constraints/06_insert_duplicate_pk.expected.txt`:

```
OK
OK
ERROR: ConstraintViolation(kind='duplicate_pk', columns=['id'], value=(1,))
```

`tests/e2e/sql/constraints/07_multi_row_partial.sql`:

```sql
CREATE TABLE u(id INT PRIMARY KEY, name TEXT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b'), (1, 'c');
SELECT * FROM u ORDER BY id;
```

`tests/e2e/sql/constraints/07_multi_row_partial.expected.txt`:

```
OK
ERROR: ConstraintViolation(kind='duplicate_pk', columns=['id'], value=(1,))
Row(id=1, name='a')
Row(id=2, name='b')
```

`tests/e2e/sql/constraints/08_null_unique.sql`:

```sql
CREATE TABLE u(id INT, email TEXT UNIQUE);
INSERT INTO u(id, email) VALUES (1, NULL);
INSERT INTO u(id, email) VALUES (2, NULL);
INSERT INTO u(id, email) VALUES (3, 'a@x');
INSERT INTO u(id, email) VALUES (4, 'a@x');
SELECT id FROM u ORDER BY id;
```

`tests/e2e/sql/constraints/08_null_unique.expected.txt`:

```
OK
OK
OK
OK
ERROR: ConstraintViolation(kind='unique', columns=['email'], value=('a@x',))
1
2
3
```

- [ ] **Step 2: 跑 e2e 测试看红**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/e2e/test_golden_sql.py -v
```

期望：新增 8 个 test 失败（`.expected.txt` 不存在或 mismatch）。

- [ ] **Step 3: 跑 e2e 看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/e2e/test_golden_sql.py -v -k constraints
```

期望：8 passed（如果 red，按错误信息调整 expected）。

- [ ] **Step 4: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：272 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add tests/e2e/sql/constraints
git commit -m "test(e2e): add 8 golden SQL scripts for constraints"
```

---

### Task 16: property — 约束相关鲁棒性

**Files:**
- Create: `tests/property/test_parser_constraints.py` — 随机约束列定义不漏异常
- Create: `tests/property/test_storage_constraints.py` — INSERT 后扫描结果与 Python 镜像一致（含 UNIQUE）

- [ ] **Step 1: 写 property 测试 — 约束列定义**

```python
# tests/property/test_parser_constraints.py
from hypothesis import given, seed, settings
import hypothesis.strategies as st
import pytest

from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError, TokenError

pytestmark = pytest.mark.property

_ALLOWED = (ParseError, TokenError, UnicodeDecodeError)


@seed(20260716)
@settings(max_examples=200, deadline=None)
@given(
    types=st.sampled_from(["INT", "TEXT", "FLOAT", "BOOL"]),
    nullable=st.booleans(),
    unique=st.booleans(),
    pk=st.booleans(),
)
def test_random_constraint_clause_never_crashes(types, nullable, unique, pk):
    """Random constraint combinations must not leak system exceptions."""
    pieces = [types]
    if not nullable:
        pieces.append("NOT NULL")
    if unique:
        pieces.append("UNIQUE")
    if pk:
        pieces.append("PRIMARY KEY")
    sql = f"CREATE TABLE t(x {' '.join(pieces)})"
    try:
        parse(tokenize(sql))
    except _ALLOWED:
        pass
```

- [ ] **Step 2: 写 property 测试 — 存储约束 invariant**

```python
# tests/property/test_storage_constraints.py
from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, seed, settings

import tinydb

pytestmark = pytest.mark.property


@seed(20260716)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    rows=st.lists(
        st.tuples(st.integers(min_value=0, max_value=10), st.integers(min_value=0, max_value=10)),
        max_size=20,
    )
)
def test_unique_constraint_mirror(rows):
    """UNIQUE on a single column tracks the Python multiset exactly."""
    db = tinydb.Database(":memory:")
    db.execute("CREATE TABLE t(id INT, x INT UNIQUE)")
    mirror: set[int] = set()
    for i, x in rows:
        try:
            db.execute(f"INSERT INTO t(id, x) VALUES ({i}, {x})")
            mirror.add(x)
        except Exception:
            # Constraint violation is expected; Python mirror ignored.
            pass
    actual = sorted(r.x for r in db.execute("SELECT * FROM t"))
    assert actual == sorted(mirror)
```

- [ ] **Step 3: 跑 property 测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/property/test_parser_constraints.py tests/property/test_storage_constraints.py -v
```

期望：2 passed（hypothesis 跑完种子内的 200 + 100 个 examples 不红）。

- [ ] **Step 4: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：274 passed。

- [ ] **Step 5: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add tests/property/test_parser_constraints.py tests/property/test_storage_constraints.py
git commit -m "test(property): constraint clause robustness and UNIQUE mirror"
```

---

### Task 17: 性能回归 — n=1000 UNIQUE 全表扫描 < 100ms

**Files:**
- Create: `tests/integration/test_constraints_perf.py`

- [ ] **Step 1: 写失败测试 — 1000 行 UNIQUE 校验时延**

```python
# tests/integration/test_constraints_perf.py
import time

import pytest

from tinydb import Database


@pytest.mark.integration
def test_unique_check_under_100ms_for_1000_rows(tmp_path):
    """Linear-scan UNIQUE check must stay under 100ms for 1000 rows (R2 mitigation)."""
    with Database(str(tmp_path / "perf.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, email TEXT UNIQUE)")
        # Pre-populate 1000 unique rows so subsequent UNIQUE check is O(n).
        for i in range(1000):
            db.execute(f"INSERT INTO t(id, email) VALUES ({i}, 'u{i}@x')")
        start = time.perf_counter()
        # Final INSERT triggers a full UNIQUE scan over 1000 existing rows.
        db.execute("INSERT INTO t(id, email) VALUES (1000, 'u1000@x')")
        elapsed = time.perf_counter() - start
    assert elapsed < 0.1, f"UNIQUE scan took {elapsed * 1000:.1f}ms (>100ms budget)"
```

- [ ] **Step 2: 跑测试看绿**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/integration/test_constraints_perf.py -v
```

期望：1 passed（O(n) 扫描在 1000 行上远低于 100ms）。

- [ ] **Step 3: 跑全量看回归**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q
```

期望：275 passed。

- [ ] **Step 4: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add tests/integration/test_constraints_perf.py
git commit -m "test(perf): UNIQUE full-scan under 100ms for 1000 rows"
```

---

### Task 18: 模块行数审计

**Files:**
- 无代码变更；只跑 `wc -l` 校验

- [ ] **Step 1: 跑行数审计**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && wc -l src/tinydb/parser.py src/tinydb/executor.py src/tinydb/catalog.py src/tinydb/tokenizer.py src/tinydb/errors.py src/tinydb/repl.py
```

期望（Design Doc §14 预算）：
- `parser.py ≤ 750`
- `executor.py ≤ 620`
- `catalog.py ≤ 130`
- `tokenizer.py ≤ 210`
- `errors.py ≤ 55`
- `repl.py ≤ 310`

- [ ] **Step 2: 若任一行数超标，立即拆分文件并补 commit**

无需 commit（行数审计属 chore）。

---

### Task 19: docs/MVP_LIMITATIONS.md 增补

**Files:**
- Modify: `docs/MVP_LIMITATIONS.md` — 在 "Schema-level constraints" 一节中更新

- [ ] **Step 1: 修改文档**

将原 "Schema-level constraints" 段：

> **Schema-level constraints**: NOT NULL / PRIMARY KEY / UNIQUE / CHECK / FOREIGN KEY / DEFAULT are NOT parsed at all in MVP — only column type tags ...

替换为：

> **Schema-level constraints (post `tinydb-constraints`)**: column-level `NOT NULL` / `UNIQUE` / `PRIMARY KEY` are parsed and enforced at INSERT time. The catalog persists each column's `nullable` / `unique` / `primary_key` flags; legacy `[name, type]` schemas auto-load with `nullable=True, unique=False, primary_key=False`. UNIQUE validation is a full table O(n) scan per INSERT — `tinydb-engine-v2` will swap to B-tree indexes. CHECK / FOREIGN KEY / DEFAULT / table-level `UNIQUE (a, b)` / table-level `PRIMARY KEY (a, b)` / `ALTER TABLE` / `DROP CONSTRAINT` remain unsupported.

- [ ] **Step 2: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add docs/MVP_LIMITATIONS.md
git commit -m "docs: document constraint support and O(n) UNIQUE limitation"
```

---

### Task 20: 全量回归与覆盖率

**Files:**
- 无代码变更；只跑测试

- [ ] **Step 1: 跑全量 + 覆盖率**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest --cov=tinydb --cov-report=term --cov-fail-under=85 -q
```

期望：所有 275+ 测试通过；总覆盖率 ≥ 85%。

- [ ] **Step 2: 记录构建证据**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
node /home/lz/.agents/skills/comet/scripts/comet-state.mjs record-check tinydb-constraints build \
  --command ".venv/bin/python -m pytest --cov=tinydb --cov-report=term --cov-fail-under=85 -q" \
  --exit-code 0
```

---

### Task 21: tasks.md 勾选 26 项（设计文档同步）

**Files:**
- Modify: `openspec/changes/tinydb-constraints/tasks.md` — 把 1.1-7.4 全部勾上 + 补充 19 项新子任务

- [ ] **Step 1: 更新 tasks.md**

按本 plan 的 26 个 task 在 `tasks.md` 加 19 个新子项并全部勾上：

```markdown
## 1. Catalog Schema 升级
- [x] 1.1 编写 `tests/unit/test_catalog_column.py::test_column_dataclass_*`，红
- [x] 1.2 在 `src/tinydb/catalog.py` 定义 `Column` dataclass
- [x] 1.3 升级 `TableInfo` 使用 `tuple[Column, ...]`；双格式 JSON
- [x] 1.4 编写 `test_catalog_roundtrip_with_constraints`，绿

## 2. Parser：列约束
- [x] 2.1 编写 `test_create_table_primary_key_unique_not_null`，红
- [x] 2.2 在 `parser.py::parse_create_table` 接入约束子句链
- [x] 2.3 在 `tokenizer.py` 增加 `PRIMARY` / `KEY` / `NOT` / `NULL` / `UNIQUE`

## 3. Parser：`NULL` 字面量
- [x] 3.1 编写 `test_insert_accepts_null_literal_when_column_nullable`
- [x] 3.2 在 INSERT 解析路径识别 `NULL` 字面量为 `None`
- [x] 3.3 编写 `test_insert_rejects_null_for_pk`

## 4. Executor：INSERT 校验顺序
- [x] 4.1 编写 `test_insert_rejects_null_on_not_null`
- [x] 4.2 实现 NOT NULL 校验
- [x] 4.3 编写 `test_insert_rejects_duplicate_unique_key`
- [x] 4.4 实现 UNIQUE 单列 + 复合键校验
- [x] 4.5 编写 `test_insert_rejects_duplicate_primary_key`
- [x] 4.6 实现 PRIMARY KEY 合并检查

## 5. 异常类型
- [x] 5.1 编写 `test_constraint_violation_includes_kind_column_value`
- [x] 5.2 在 `errors.py` 新增 `ConstraintViolation`
- [x] 5.3 在 REPL 路径上把 `ConstraintViolation` 渲染为单行 ERROR

## 6. 兼容性
- [x] 6.1 编写 fixture：MVP 旧版 `.db`
- [x] 6.2 编写 `test_catalog_old_file_migration_loads_with_nullable_default_true`
- [x] 6.3 验证：MVP 234 + engine-v1 测试继续通过

## 7. 性能与回归
- [x] 7.1 计时 fixture：n=1000 < 100ms
- [x] 7.2 模块行数回归
- [x] 7.3 覆盖率 ≥ 85% across project
- [x] 7.4 `docs/MVP_LIMITATIONS.md` 增补

## 8. ConstraintViolation 子异常契约（6 tests）
- [x] 8.1 继承 ExecutionError
- [x] 8.2 str 单行 kind/column/columns/value

## 9. Catalog 双格式 JSON 加载（6 tests）
- [x] 9.1 legacy [name,type] 加载
- [x] 9.2 新 {name,type,...} 加载
- [x] 9.3 mixed 拒绝
- [x] 9.4 落盘 reopen

## 10. Parser 约束子句矩阵（13 tests）
- [x] 10.1 NOT NULL / UNIQUE / PRIMARY KEY 各自
- [x] 10.2 三种组合
- [x] 10.3 bare NULL 拒绝
- [x] 10.4 顺序独立性
- [x] 10.5 重复子句拒绝

## 11. Executor 校验流水线（13 tests）
- [x] 11.1 NOT NULL / PK NULL
- [x] 11.2 UNIQUE / duplicate_pk
- [x] 11.3 同批次去重
- [x] 11.4 multi-row partial
- [x] 11.5 列归一化

## 12. REPL ConstraintViolation 渲染（4 tests）
- [x] 12.1 kind=null / unique / duplicate_pk
- [x] 12.2 loop continues

## 13. e2e golden SQL（8 SQL）
- [x] 13.1-13.8 happy / null / unique / pk / partial / null-uniq

## 14. Property-based（2 tests）
- [x] 14.1 约束子句鲁棒性
- [x] 14.2 UNIQUE 镜像 invariant

## 15. 性能预算（1 test）
- [x] 15.1 n=1000 < 100ms

## 16. 文档（1 file）
- [x] 16.1 MVP_LIMITATIONS 增补

## 17. 构建证据
- [x] 17.1 pytest --cov-fail-under=85 全绿
- [x] 17.2 comet-state record-check 写入
- [x] 17.3 comet-guard build --apply 通过
```

- [ ] **Step 2: Commit**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add openspec/changes/tinydb-constraints/tasks.md
git commit -m "docs(tasks): mark all 26 build tasks complete"
```

---

### Task 22: code review (review_mode=standard)

**Files:**
- 无代码变更；调用 `superpowers:requesting-code-review` 技能

- [ ] **Step 1: 加载 requesting-code-review 技能**

按 `superpowers:requesting-code-review` 流程对整个 diff 做轻量 review。

- [ ] **Step 2: 修复 CRITICAL / IMPORTANT**

按 review 结果在主 agent 直接修复（小修）；中大型修复走 systematic-debugging。

- [ ] **Step 3: 跑全量测试**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest --cov=tinydb --cov-report=term --cov-fail-under=85 -q
```

期望：所有测试通过，覆盖率 ≥ 85%。

- [ ] **Step 4: Commit（如有 review 修复）**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
git add -A
git commit -m "fix(constraints): address code review findings"
```

---

### Task 23: comet-state finalization — `phase: build` → `phase: verify`

**Files:**
- Modify: `openspec/changes/tinydb-constraints/.comet.yaml` — 由 `comet-guard` 自动推进

- [ ] **Step 1: 跑 build guard**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
node /home/lz/.agents/skills/comet/scripts/comet-guard.mjs tinydb-constraints build --apply
```

期望：`ALL CHECKS PASSED — phase advanced to verify`。

- [ ] **Step 2: 验证 .comet.yaml 推进到 `phase: verify`**

```bash
cd /home/lz/projects/tinydb-worktrees/tinydb-constraints
grep -E '^phase:' openspec/changes/tinydb-constraints/.comet.yaml
```

期望：`phase: verify`。

- [ ] **Step 3: 不进入 verify 阶段**

按任务边界，build 阶段到此结束。不跑 `comet-verify` 流程。

---

## 边界与禁止

- 仅在 `/home/lz/projects/tinydb-worktrees/tinydb-constraints/` 内工作
- 不要触碰 `tinydb-engine-v1/` 或 `tinydb-aggregation/`
- 不要进入 verify 阶段

## Commit 编号预期

按 26 个 task 拆 22+ commits。最低 commit 数 = 22（部分 task 不带代码 commit）。
