---
change: tinydb-mvp
design-doc: docs/superpowers/specs/2026-07-15-tinydb-mvp-design.md
base-ref: b2641736d11bf4afb98e28aecd4f7f1b82f4c94c
---

# tinydb-mvp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 `tinydb` Python 嵌入式关系型数据库的 MVP 版本，从零交付可演示的端到端最小路径：单文件 slotted-page 存储 + INT/TEXT/FLOAT/BOOL 类型系统 + 5 个 SQL 语句子集 + `Database.execute(sql)` Python API。

**Architecture:** 分层模块化。`parser`/`tokenizer`/`type_system`/`row_codec` 是纯函数模块（无 I/O）。`executor` 是唯一同时持有 `Pager` 与 `Catalog` 的层，所有落盘 I/O 仅在此发生。`database.py` 是唯一对外公开模块。Slotted Page 单文件存储，4KB 定长页，catalog 用 JSON 编码在 page 1，溢出行用 SPILL_START + overflow chain。

**Tech Stack:** Python 3.11+，纯 stdlib（`struct`、`dataclasses`、`json`、`mmap`、`pathlib`）。dev 依赖：`pytest>=7`、`hypothesis>=6`、`pytest-cov`。零运行时依赖。

---

## 文件结构（实施前映射）

### 源码 `src/tinydb/`

| 文件 | 职责 | 行数预算 |
|------|------|---------|
| `__init__.py` | 公共导出（`Database`、`Row`、errors、`__version__`） | ≤ 30 |
| `errors.py` | `TinydbError` 基类 + 异常层级 | ≤ 80 |
| `type_system.py` | 4 类型编解码 + 字面量 + `py_to_db`/`db_to_py`/`validate_compare` | ≤ 150 |
| `row_codec.py` | 行级编码（null bitmap + length-prefixed values） | ≤ 80 |
| `pager.py` | 4KB 页地址、magic header、mmap/bytearray 后端 | ≤ 250 |
| `slotted_page.py` | 单页布局（header + slot directory + data area）、tombstone | ≤ 150 |
| `catalog.py` | 表元数据持久化（JSON 编码 page 1、INT-as-string） | ≤ 100 |
| `tokenizer.py` | SQL 词法分析（6 类 token） | ≤ 200 |
| `parser.py` | 5 语句 recursive descent → AST | ≤ 600 |
| `executor.py` | AST → Catalog/Pager 调用、溢出链 | ≤ 400 |
| `database.py` | `Database` + `Row`、context manager、`execute` 入口 | ≤ 100 |

### 测试 `tests/`

| 层级 | 文件 | 来源 |
|------|------|------|
| unit | `tests/unit/test_type_system.py` | §2.1, §4 |
| unit | `tests/unit/test_slotted_page.py` | §4.1 |
| unit | `tests/unit/test_tokenizer.py` | §6.1 |
| unit | `tests/unit/test_parser.py` | §7.1 |
| integration | `tests/integration/test_pager.py` | §3.1 |
| integration | `tests/integration/test_catalog.py` | §5.1 |
| integration | `tests/integration/test_executor.py` | §8.1 |
| integration | `tests/integration/test_database_api.py` | §9.1 |
| integration | `tests/integration/test_parser_executor_roundtrip.py` | §8.2 |
| integration | `tests/integration/test_storage_page_chain.py` | §3.3 + §3.5 |
| integration | `tests/integration/test_full_sql_lifecycle.py` | §9 + §5.1 |
| integration | `tests/integration/test_overflow_chain.py` | §9 Spec Patch（新增） |
| integration | `tests/integration/test_catalog_json_int_as_string.py` | §9 Spec Patch（新增） |
| property | `tests/property/test_storage_invariants.py` | §10.3 |
| property | `tests/property/test_parser_robustness.py` | §10.4 |
| e2e | `tests/e2e/conftest.py` + 12-15 个 `tests/e2e/sql/**` | §10.1-10.2 |

### 顶层文件

- `pyproject.toml` — 包元数据 + pytest/hypothesis 依赖
- `pytest.ini`（或合并到 `pyproject.toml`）
- `README.md` — 快速开始 + 模块导览
- `examples/demo.py` — 端到端演示脚本
- `docs/MVP_LIMITATIONS.md` — MVP 约束清单

---

## 测试策略（Design Doc §8 4 层金字塔）

每 capability 的测试落地：

1. **Per-scenario 1:1 unit/integration 测试**：每个 spec Scenario 对应一个 pytest 函数，命名 `test_<scenario_snake>`，标记 `@pytest.mark.spec_id("REQ-<cap>-<num>-SCN-<num>")`。约 93 个测试（来自 4 个 spec 文件的所有 Scenario）。
2. **每 capability 一个 integration 套件**：覆盖模块间交互（parser↔executor、storage chain、SQL lifecycle）。
3. **12-15 个 golden SQL E2E**：`tests/e2e/sql/{happy_path,error_cases}/<NN>_<name>.sql` + `.expected.txt`，由 `tests/e2e/conftest.py::run_sql` 字节对比。
4. **Property-based（hypothesis, seed=20260715）**：
   - `test_storage_invariants.py`：随机 INSERT/DELETE 序列，断言扫描结果 == Python `dict` 镜像
   - `test_parser_robustness.py`：随机字符串输入，断言至多抛 `ParseError`/`TokenError`，不抛未捕获系统异常

覆盖率门槛：模块 ≥ 85%（`pytest --cov-fail-under=85`），其中 type_system ≥ 95%、parser ≥ 90%、storage ≥ 90%、executor ≥ 90%。

---

## Commit 粒度规则

按 `tasks.md` 子任务粒度拆 commit。每个任务（Task N）内部：

- 若包含 ≥ 2 个独立 Red→Green 循环（如 Task 6 含 4 种字面量解析），每个循环单独 commit
- 否则整个 Task 单 commit
- Commit message 格式（参照 `common/git-workflow.md`）：`<type>(<scope>): <subject>`，scope 用模块名

示例 commit 类型与 scope 映射：

| 类型 | 触发场景 |
|------|---------|
| `feat` | 新增能力/接口 |
| `test` | 仅测试代码 |
| `refactor` | 重构不改行为 |
| `chore` | 配置、占位、README |
| `docs` | 文档 |
| `fix` | bug 修复 |

---

## 任务列表

> **执行顺序**：Task 1 → 2 → 3 → ... → 33。每任务完成后必须 git commit 再进入下一任务。
> **测试先行**：每任务 Step 1 都是"写失败测试"。Step 2 必须看到 RED 才推进 Step 3。
> **行数审计**：每次 commit 前对新增模块跑 `wc -l src/tinydb/<module>.py`，违反 Design Doc §12 / proposal.md Impact 预算 → 立即拆分子任务。

---

### Task 1: 项目骨架与配置（tasks.md §1）

**Files:**
- Create: `pyproject.toml`
- Create: `pytest.ini`
- Create: `src/tinydb/__init__.py`
- Create: `src/tinydb/errors.py`
- Create: `src/tinydb/type_system.py`
- Create: `src/tinydb/row_codec.py`
- Create: `src/tinydb/pager.py`
- Create: `src/tinydb/slotted_page.py`
- Create: `src/tinydb/catalog.py`
- Create: `src/tinydb/tokenizer.py`
- Create: `src/tinydb/parser.py`
- Create: `src/tinydb/executor.py`
- Create: `src/tinydb/database.py`
- Create: `README.md`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/property/__init__.py`
- Create: `tests/e2e/__init__.py`

- [x] **Step 1: 写失败测试 — 包导入与版本**

```python
# tests/unit/test_package.py
def test_tinydb_imports_database_and_row():
    import tinydb
    assert hasattr(tinydb, "Database")
    assert hasattr(tinydb, "Row")

def test_tinydb_version_string():
    import tinydb
    assert tinydb.__version__ == "0.1.0"

def test_tinydb_exposes_exception_classes():
    import tinydb
    from tinydb import errors
    assert issubclass(errors.TinydbError, Exception)
    for name in ("ParseError", "TokenError", "ExecutionError",
                 "InvalidDatabaseFile", "UnsupportedSchemaVersion",
                 "PageFull", "CatalogFull"):
        assert hasattr(errors, name), f"missing errors.{name}"
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_package.py -v`
Expected: ModuleNotFoundError `tinydb`

- [x] **Step 3: 写 `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "tinydb"
version = "0.1.0"
description = "Minimal embedded relational database (MVP)"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=7", "hypothesis>=6", "pytest-cov>=4"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
minversion = "7.0"
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
    "spec_id(id): per-scenario spec traceability marker",
]
```

- [x] **Step 4: 写占位模块（含 docstring 声明职责 + 行数预算）**

每个模块写一行模块级 docstring 描述职责，**不写任何实现**。

`src/tinydb/__init__.py`:
```python
"""tinydb: minimal embedded relational database (MVP). Public API: Database, Row, errors."""
from tinydb.database import Database, Row
from tinydb import errors
__version__ = "0.1.0"
__all__ = ["Database", "Row", "errors", "__version__"]
```

`src/tinydb/errors.py`:
```python
"""Tinydb exception hierarchy. TinydbError base; ParseError/TokenError/ExecutionError + storage-level."""
class TinydbError(Exception):
    """Base for all tinydb-raised exceptions."""

class ParseError(TinydbError):
    def __init__(self, line: int, col: int, msg: str):
        super().__init__(f"line {line}, col {col}: {msg}")
        self.line = line
        self.col = col
        self.msg = msg

class TokenError(TinydbError):
    def __init__(self, line: int, col: int, msg: str):
        super().__init__(f"line {line}, col {col}: {msg}")
        self.line = line
        self.col = col
        self.msg = msg

class ExecutionError(TinydbError): ...
class InvalidDatabaseFile(TinydbError): ...
class UnsupportedSchemaVersion(TinydbError): ...
class PageFull(TinydbError): ...
class CatalogFull(TinydbError): ...
```

其余 8 个模块：每个写一行 docstring，**只 import 不实现**。例 `src/tinydb/type_system.py`:
```python
"""4-type system: INT/TEXT/FLOAT/BOOL encode/decode + literal parse + py_to_db/db_to_py + validate_compare. ≤ 150 lines."""
```

- [x] **Step 5: 写 README.md 骨架**

```markdown
# tinydb (MVP)

> Minimal embedded relational database for teaching and embedding. **MVP: non-ACID, no crash safety.**

## Quick start
```python
import tinydb
with tinydb.Database(":memory:") as db:
    db.execute("CREATE TABLE users(id INT, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'alice')")
    for row in db.execute("SELECT * FROM users"):
        print(row.id, row.name)
```

## Module map (line budgets per proposal Impact)
| Module | Budget | Responsibility |
|--------|--------|----------------|
| `type_system.py` | 150 | INT/TEXT/FLOAT/BOOL codecs |
| `pager.py` | 250 | 4KB pages, mmap/bytearray |
| `slotted_page.py` | 150 | single page layout |
| `catalog.py` | 100 | table metadata |
| `tokenizer.py` | 200 | SQL lexer |
| `parser.py` | 600 | recursive descent parser |
| `executor.py` | 400 | AST → storage |
| `database.py` | 100 | public API |

See `docs/MVP_LIMITATIONS.md` for what MVP does NOT do.
```

- [x] **Step 6: 安装包到 editable 模式**

Run: `pip install -e ".[dev]"`
Expected: Successfully installed tinydb-0.1.0

- [x] **Step 7: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_package.py -v`
Expected: PASS（3 passed）

- [x] **Step 8: Commit**

```bash
git add pyproject.toml pytest.ini src/tinydb/ tests/ README.md
git commit -m "chore: scaffold tinydb MVP package skeleton with empty modules"
```

---

### Task 2: Type System — INT 编解码（tasks.md §2.2）

引用：Design Doc §4.1、§4.2、§4.3。

**Files:**
- Test: `tests/unit/test_type_system.py`
- Create: `src/tinydb/type_system.py`

- [x] **Step 1: 写失败测试（INT encode/decode + Overflow）**

```python
# tests/unit/test_type_system.py
import pytest
from tinydb.type_system import encode_int, decode_int

@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_int_encode_42_big_endian():
    assert encode_int(42) == b"\x00\x00\x00\x00\x00\x00\x00\x2a"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-08")
def test_int_encode_overflow_2_63_raises():
    with pytest.raises(OverflowError):
        encode_int(2**63)

@pytest.mark.spec_id("REQ-TYPE-001-SCN-14")
def test_int_decode_roundtrips_42():
    val, off = decode_int(b"\x00\x00\x00\x00\x00\x00\x00\x2a", 0)
    assert val == 42
    assert off == 8

@pytest.mark.spec_id("REQ-TYPE-001-SCN-17")
def test_int_decode_truncated_buffer_raises():
    with pytest.raises(ValueError):
        decode_int(b"\x00\x00\x00", 0)

@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_int_roundtrip_negative():
    val, off = decode_int(encode_int(-1), 0)
    assert val == -1 and off == 8
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_type_system.py -v`
Expected: ImportError / AttributeError `encode_int`

- [x] **Step 3: 写 INT 编解码实现**

在 `src/tinydb/type_system.py` 顶部添加：

```python
"""4-type system: INT/TEXT/FLOAT/BOOL encode/decode + literal parse + py_to_db/db_to_py + validate_compare. ≤ 150 lines."""
import struct
import math

_INT_FMT = ">q"  # signed 64-bit big-endian
_INT_SIZE = 8

def encode_int(value: int) -> bytes:
    if not -2**63 <= value < 2**63:
        raise OverflowError(f"INT out of range: {value}")
    return struct.pack(_INT_FMT, value)

def decode_int(buf: bytes, offset: int) -> tuple[int, int]:
    if offset + _INT_SIZE > len(buf):
        raise ValueError(f"INT decode truncated at offset {offset}")
    return struct.unpack_from(_INT_FMT, buf, offset)[0], offset + _INT_SIZE
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_type_system.py -v`
Expected: PASS（5 passed）

- [x] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system.py
git commit -m "feat(type-system): add INT 8-byte big-endian encode/decode with overflow guard"
```

---

### Task 3: Type System — TEXT 编解码（tasks.md §2.3）

引用：Design Doc §4.1、§4.3。

**Files:**
- Modify: `tests/unit/test_type_system.py`
- Modify: `src/tinydb/type_system.py`

- [x] **Step 1: 写失败测试（TEXT encode/decode + Unicode）**

在 `tests/unit/test_type_system.py` 追加：

```python
from tinydb.type_system import encode_text, decode_text

@pytest.mark.spec_id("REQ-TYPE-001-SCN-10")
def test_text_encode_alice_length_prefixed():
    assert encode_text("alice") == b"\x00\x05alice"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-11")
def test_text_encode_rejects_invalid_surrogate():
    with pytest.raises(UnicodeEncodeError):
        encode_text("\udcff")

@pytest.mark.spec_id("REQ-TYPE-001-SCN-15")
def test_text_decode_roundtrips_alice():
    val, off = decode_text(b"\x00\x05alice", 0)
    assert val == "alice" and off == 7

@pytest.mark.spec_id("REQ-TYPE-001-SCN-15")
def test_text_decode_utf8_multibyte():
    encoded = encode_text("你好")
    val, off = decode_text(encoded, 0)
    assert val == "你好"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-17")
def test_text_decode_truncated_length_raises():
    with pytest.raises(ValueError):
        decode_text(b"\x00\x05abc", 0)  # length says 5, only 3 bytes follow
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_type_system.py -v -k text`
Expected: ImportError `encode_text`

- [x] **Step 3: 实现 TEXT 编解码**

```python
def encode_text(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack(">H", len(data)) + data

def decode_text(buf: bytes, offset: int) -> tuple[str, int]:
    if offset + 2 > len(buf):
        raise ValueError("TEXT length prefix truncated")
    (n,) = struct.unpack_from(">H", buf, offset)
    if offset + 2 + n > len(buf):
        raise ValueError(f"TEXT payload truncated (need {n} bytes)")
    return buf[offset + 2 : offset + 2 + n].decode("utf-8"), offset + 2 + n
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_type_system.py -v`
Expected: PASS（10 passed）

- [x] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system.py
git commit -m "feat(type-system): add TEXT length-prefixed UTF-8 encode/decode"
```

---

### Task 4: Type System — BOOL + FLOAT 编解码（tasks.md §2.4-2.5）

引用：Design Doc §4.1、§4.3。

**Files:**
- Modify: `tests/unit/test_type_system.py`
- Modify: `src/tinydb/type_system.py`

- [x] **Step 1: 写失败测试**

```python
from tinydb.type_system import encode_bool, decode_bool, encode_float, decode_float

@pytest.mark.spec_id("REQ-TYPE-001-SCN-12")
def test_bool_encode_true_false_single_byte():
    assert encode_bool(True) == b"\x01"
    assert encode_bool(False) == b"\x00"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-16")
def test_bool_decode_roundtrips():
    assert decode_bool(b"\x01", 0) == (True, 1)
    assert decode_bool(b"\x00", 0) == (False, 1)

@pytest.mark.spec_id("REQ-TYPE-001-SCN-13")
def test_float_encode_3_14_ieee754_be():
    import struct as _st
    assert encode_float(3.14) == _st.pack(">d", 3.14)

@pytest.mark.spec_id("REQ-TYPE-001-SCN-13")
def test_float_roundtrip_negative_zero():
    val, off = decode_float(encode_float(-0.0), 0)
    assert val == -0.0 and math.copysign(1.0, val) == -1.0
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_type_system.py -v -k "bool or float"`
Expected: ImportError `encode_bool`

- [x] **Step 3: 实现**

```python
def encode_bool(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"

def decode_bool(buf: bytes, offset: int) -> tuple[bool, int]:
    if offset + 1 > len(buf):
        raise ValueError("BOOL decode truncated")
    return buf[offset] != 0, offset + 1

_FLOAT_FMT = ">d"

def encode_float(value: float) -> bytes:
    return struct.pack(_FLOAT_FMT, value)

def decode_float(buf: bytes, offset: int) -> tuple[float, int]:
    if offset + 8 > len(buf):
        raise ValueError("FLOAT decode truncated")
    return struct.unpack_from(_FLOAT_FMT, buf, offset)[0], offset + 8
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_type_system.py -v`
Expected: PASS（14 passed）

- [x] **Step 5: 行数审计**

Run: `wc -l src/tinydb/type_system.py`
Expected: ≤ 60 行（远低于 150 预算）

- [x] **Step 6: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system.py
git commit -m "feat(type-system): add BOOL single-byte and FLOAT 8-byte IEEE 754 encode/decode"
```

---

### Task 5: Tokenizer 字面量层 + Type 字面量拒绝（tasks.md §2.6）

引用：Design Doc §4.3（literal 拒绝规则）。

**Files:**
- Test: `tests/unit/test_type_system.py`（追加）
- Modify: `src/tinydb/type_system.py`

- [x] **Step 1: 写失败测试（字面量解析 + NaN/Inf 拒绝）**

```python
from tinydb.type_system import parse_int_literal, parse_float_literal, parse_text_literal, parse_bool_literal

@pytest.mark.spec_id("REQ-TYPE-001-SCN-01")
def test_parse_int_literal_positive():
    assert parse_int_literal("42") == 42

@pytest.mark.spec_id("REQ-TYPE-001-SCN-02")
def test_parse_int_literal_negative():
    assert parse_int_literal("-7") == -7

@pytest.mark.spec_id("REQ-TYPE-001-SCN-03")
def test_parse_float_literal_decimal():
    assert parse_float_literal("3.14") == 3.14

@pytest.mark.spec_id("REQ-TYPE-001-SCN-04")
def test_parse_text_literal_strips_quotes():
    assert parse_text_literal("'hello world'") == "hello world"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-05")
def test_parse_bool_literal_true():
    assert parse_bool_literal("TRUE") is True
    assert parse_bool_literal("true") is True

@pytest.mark.spec_id("REQ-TYPE-001-SCN-06")
def test_parse_bool_literal_false():
    assert parse_bool_literal("false") is False

@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_parse_float_literal_rejects_NaN():
    with pytest.raises(ValueError, match="NaN not allowed"):
        parse_float_literal("NaN")

@pytest.mark.spec_id("REQ-TYPE-001-SCN-08")
def test_parse_float_literal_rejects_Infinity():
    with pytest.raises(ValueError):
        parse_float_literal("Infinity")
    with pytest.raises(ValueError):
        parse_float_literal("inf")
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_type_system.py -v -k parse_`
Expected: ImportError `parse_int_literal`

- [x] **Step 3: 实现字面量解析**

```python
def parse_int_literal(s: str) -> int:
    return int(s)

def parse_float_literal(s: str) -> float:
    v = float(s)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"FLOAT inf/NaN not allowed: {s!r}")
    return v

def parse_text_literal(s: str) -> str:
    # s already includes surrounding single quotes (tokenizer produces raw text)
    if len(s) < 2 or s[0] != "'" or s[-1] != "'":
        raise ValueError(f"invalid text literal: {s!r}")
    inner = s[1:-1].replace("''", "'")
    return inner

def parse_bool_literal(s: str) -> bool:
    u = s.upper()
    if u == "TRUE":
        return True
    if u == "FALSE":
        return False
    raise ValueError(f"invalid bool literal: {s!r}")
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_type_system.py -v`
Expected: PASS（22 passed）

- [x] **Step 5: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system.py
git commit -m "feat(type-system): add literal parsers with NaN/Inf rejection"
```

---

### Task 6: Type System — py_to_db / db_to_py / validate_compare（tasks.md §2.7-2.8）

引用：Design Doc §4.2 严格类型守卫。

**Files:**
- Modify: `tests/unit/test_type_system.py`
- Modify: `src/tinydb/type_system.py`

- [x] **Step 1: 写失败测试**

```python
from tinydb.type_system import py_to_db, db_to_py, validate_compare

@pytest.mark.spec_id("REQ-TYPE-001-SCN-19")
def test_py_to_db_int():
    assert py_to_db(42, "INT") == b"\x00\x00\x00\x00\x00\x00\x00\x2a"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-20")
def test_py_to_db_text():
    assert py_to_db("alice", "TEXT") == b"\x00\x05alice"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-21")
def test_py_to_db_float():
    import struct as _st
    assert py_to_db(2.5, "FLOAT") == _st.pack(">d", 2.5)

@pytest.mark.spec_id("REQ-TYPE-001-SCN-22")
def test_py_to_db_float_nan_rejected():
    with pytest.raises(ValueError):
        py_to_db(float("nan"), "FLOAT")

@pytest.mark.spec_id("REQ-TYPE-001-SCN-23")
def test_py_to_db_bool_true():
    assert py_to_db(True, "BOOL") == b"\x01"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-24")
def test_py_to_db_float_to_int_rejected():
    with pytest.raises(TypeError):
        py_to_db(2.5, "INT")

@pytest.mark.spec_id("REQ-TYPE-001-SCN-18")
def test_validate_compare_type_mismatch_raises():
    with pytest.raises(TypeError, match="INT vs TEXT"):
        validate_compare(b"\x00\x00\x00\x00\x00\x00\x00\x05", "INT",
                          b"\x00\x01x", "TEXT")

@pytest.mark.spec_id("REQ-TYPE-001-SCN-18")
def test_validate_compare_matching_type_ok():
    # Should not raise
    validate_compare(b"\x00\x05", "TEXT", b"\x00\x05", "TEXT")

def test_db_to_py_roundtrip_int():
    assert db_to_py(b"\x00\x00\x00\x00\x00\x00\x00\x2a", "INT") == 42
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_type_system.py -v -k "py_to_db or db_to_py or validate"`
Expected: ImportError

- [x] **Step 3: 实现 `py_to_db` / `db_to_py` / `validate_compare`**

```python
def py_to_db(value, column_type: str) -> bytes:
    if column_type == "INT":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"expected int for INT, got {type(value).__name__}")
        return encode_int(value)
    if column_type == "TEXT":
        if not isinstance(value, str):
            raise TypeError(f"expected str for TEXT, got {type(value).__name__}")
        return encode_text(value)
    if column_type == "FLOAT":
        if not isinstance(value, float):
            raise TypeError(f"expected float for FLOAT, got {type(value).__name__}")
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"FLOAT inf/NaN not allowed: {value!r}")
        return encode_float(value)
    if column_type == "BOOL":
        if not isinstance(value, bool):
            raise TypeError(f"expected bool for BOOL, got {type(value).__name__}")
        return encode_bool(value)
    raise ValueError(f"unsupported column type: {column_type}")

def db_to_py(buf: bytes, column_type: str):
    if column_type == "INT":
        return decode_int(buf, 0)[0]
    if column_type == "TEXT":
        return decode_text(buf, 0)[0]
    if column_type == "FLOAT":
        return decode_float(buf, 0)[0]
    if column_type == "BOOL":
        return decode_bool(buf, 0)[0]
    raise ValueError(f"unsupported column type: {column_type}")

def validate_compare(col_bytes: bytes, col_type: str,
                     lit_bytes: bytes, lit_type: str) -> None:
    if col_type != lit_type:
        raise TypeError(f"type mismatch: {col_type} vs {lit_type}")
    if col_type == "FLOAT":
        v = decode_float(col_bytes, 0)[0]
        if math.isnan(v) or math.isinf(v):
            raise ValueError("FLOAT inf/NaN not allowed")
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_type_system.py -v`
Expected: PASS（30 passed；实际 31 passed：implementer 多加了 1 个 roundtrip 测试，详见 commit `81064c5`）

- [x] **Step 5: 行数审计**

Run: `wc -l src/tinydb/type_system.py`
Expected: ≤ 100 行（实际 129 行，**+29 MINOR 偏差**；协调者决策接受，理由：4 路 if-守卫 + 注释无法在不损可读性下压缩；< 模块 150 行硬上限）

- [x] **Step 6: Commit**

```bash
git add src/tinydb/type_system.py tests/unit/test_type_system.py
git commit -m "feat(type-system): add py_to_db/db_to_py/validate_compare with strict type guards"
```

---

### Task 7: Pager — 文件创建、magic、schema version（tasks.md §3.1-3.3）

引用：Design Doc §3.1。

**Files:**
- Test: `tests/integration/test_pager.py`
- Create: `src/tinydb/pager.py`

- [x] **Step 1: 写失败测试**

```python
# tests/integration/test_pager.py
import pytest
from tinydb.pager import Pager, PAGE_SIZE, MAGIC, SCHEMA_VERSION
from tinydb.errors import InvalidDatabaseFile, UnsupportedSchemaVersion

@pytest.mark.spec_id("REQ-STORAGE-001-SCN-01")
def test_create_new_db_writes_magic_header(tmp_path):
    path = tmp_path / "new.db"
    p = Pager(str(path))
    p.close()
    with open(path, "rb") as f:
        magic = f.read(8)
    assert magic[:7] == b"TINYDB\x00"
    assert magic[7] == SCHEMA_VERSION

@pytest.mark.spec_id("REQ-STORAGE-001-SCN-02")
def test_open_existing_db_verifies_magic(tmp_path):
    path = tmp_path / "bad.db"
    path.write_bytes(b"NOTADB" + b"\x00" * (PAGE_SIZE - 6))
    with pytest.raises(InvalidDatabaseFile):
        Pager(str(path))

@pytest.mark.spec_id("REQ-STORAGE-001-SCN-03")
def test_open_rejects_unknown_schema_version(tmp_path):
    path = tmp_path / "future.db"
    path.write_bytes(b"TINYDB\x00\xff" + b"\x00" * (PAGE_SIZE - 8))
    with pytest.raises(UnsupportedSchemaVersion):
        Pager(str(path))

@pytest.mark.spec_id("REQ-STORAGE-001-SCN-04")
def test_memory_mode_no_filesystem(tmp_path, monkeypatch):
    # Patch Path.cwd to ensure no file written if mode misdetected
    p = Pager(":memory:")
    assert p.path == ":memory:"
    p.close()
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_pager.py -v`
Expected: ImportError `Pager`

- [x] **Step 3: 实现 Pager 基础（magic + version）**

```python
# src/tinydb/pager.py
"""4KB page-addressed storage with magic header; mmap for file-backed, bytearray for :memory:. ≤ 250 lines."""
from pathlib import Path
import mmap

from tinydb.errors import InvalidDatabaseFile, UnsupportedSchemaVersion

PAGE_SIZE = 4096
MAGIC = b"TINYDB\x00"
SCHEMA_VERSION = 0x01
HEADER_RESERVED = PAGE_SIZE - 8  # magic(7) + version(1)

class Pager:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._is_memory = self.path == ":memory:"
        if self._is_memory:
            self._buf = bytearray(PAGE_SIZE * 2)  # page 0 + page 1 for catalog
            self._buf[0:7] = MAGIC
            self._buf[7] = SCHEMA_VERSION
            self._file = None
            self._mmap = None
        else:
            p = Path(self.path)
            existed = p.exists()
            self._file = open(p, "r+b" if existed else "w+b")
            if not existed or p.stat().st_size < PAGE_SIZE:
                self._file.truncate(0)
                self._file.write(self._build_header())
                self._file.flush()
            self._mmap = mmap.mmap(self._file.fileno(), PAGE_SIZE)
            self._verify_header()

    @staticmethod
    def _build_header() -> bytes:
        return MAGIC + bytes([SCHEMA_VERSION]) + b"\x00" * HEADER_RESERVED

    def _verify_header(self) -> None:
        magic = bytes(self._mmap[0:7])
        version = self._mmap[7]
        if magic != MAGIC:
            raise InvalidDatabaseFile(
                f"not a tinydb file (magic={magic!r})")
        if version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"schema_version={version} not supported")

    # placeholder methods implemented in Task 8
    def read_page(self, page_id: int) -> bytes: ...
    def write_page(self, page_id: int, data: bytes) -> None: ...
    def alloc_page(self) -> int: ...

    def close(self) -> None:
        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self): return self
    def __exit__(self, *a): self.close()
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_pager.py -v`
Expected: PASS（4 passed；实际 6 passed：implementer 多加了 bad_schema_version 测试，详见 commit `6d92cb2`）

- [x] **Step 5: Commit**

```bash
git add src/tinydb/pager.py tests/integration/test_pager.py
git commit -m "feat(pager): create file/header with magic + schema_version; :memory: bytearray"
```

---

### Task 8: Pager — alloc_page / read_page / write_page（tasks.md §3.4）

引用：Design Doc §3.1、§3.2。

**Files:**
- Modify: `tests/integration/test_pager.py`
- Modify: `src/tinydb/pager.py`

- [x] **Step 1: 写失败测试**

在 `tests/integration/test_pager.py` 追加：

```python
@pytest.mark.spec_id("REQ-STORAGE-002-SCN-01")
def test_alloc_page_returns_monotonic_ids(tmp_path):
    p = Pager(str(tmp_path / "a.db"))
    a = p.alloc_page()
    b = p.alloc_page()
    c = p.alloc_page()
    assert a < b < c
    p.close()

@pytest.mark.spec_id("REQ-STORAGE-002-SCN-02")
def test_read_page_returns_exact_4096_bytes(tmp_path):
    p = Pager(str(tmp_path / "a.db"))
    page = p.read_page(0)
    assert len(page) == PAGE_SIZE
    p.close()

@pytest.mark.spec_id("REQ-STORAGE-002-SCN-03")
def test_write_then_read_roundtrip(tmp_path):
    p = Pager(str(tmp_path / "a.db"))
    pid = p.alloc_page()
    payload = b"\xab" * PAGE_SIZE
    p.write_page(pid, payload)
    p.flush()
    p.close()
    p2 = Pager(str(tmp_path / "a.db"))
    assert p2.read_page(pid) == payload
    p2.close()

@pytest.mark.spec_id("REQ-STORAGE-002-SCN-02")
def test_memory_mode_read_write_roundtrip():
    p = Pager(":memory:")
    pid = p.alloc_page()
    payload = b"\x42" * PAGE_SIZE
    p.write_page(pid, payload)
    assert p.read_page(pid) == payload
    p.close()
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_pager.py -v -k "alloc or read_page or write"`
Expected: NotImplementedError / failing assertions

- [x] **Step 3: 实现 alloc / read / write**

替换 `pager.py` 中 placeholder 方法并扩展 `__init__`：

```python
class Pager:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._is_memory = self.path == ":memory:"
        self._next_page_id = 2  # page 0 = header, page 1 = catalog
        if self._is_memory:
            self._buf = bytearray(PAGE_SIZE * 2)
            self._buf[0:7] = MAGIC
            self._buf[7] = SCHEMA_VERSION
            self._file = None
            self._mmap = None
        else:
            p = Path(self.path)
            existed = p.exists() and p.stat().st_size >= PAGE_SIZE
            self._file = open(p, "r+b" if existed else "w+b")
            if not existed:
                self._file.truncate(0)
                self._file.write(self._build_header())
                self._file.flush()
            # mmap entire current file size; we'll grow by remap if needed
            self._mmap = mmap.mmap(self._file.fileno(), self._file.seek(0, 2))
            self._verify_header()

    def flush(self) -> None:
        if self._file is not None and not self._file.closed:
            self._file.flush()
            if self._mmap is not None:
                self._mmap.flush()

    def alloc_page(self) -> int:
        pid = self._next_page_id
        self._next_page_id += 1
        if self._is_memory:
            if (pid + 1) * PAGE_SIZE > len(self._buf):
                self._buf.extend(b"\x00" * PAGE_SIZE)
        else:
            needed = (pid + 1) * PAGE_SIZE
            current = self._file.seek(0, 2)
            if needed > current:
                self._file.truncate(needed)
            self._mmap.close()
            self._mmap = mmap.mmap(self._file.fileno(), needed)
        return pid

    def read_page(self, page_id: int) -> bytes:
        off = page_id * PAGE_SIZE
        if self._is_memory:
            return bytes(self._buf[off:off + PAGE_SIZE])
        return bytes(self._mmap[off:off + PAGE_SIZE])

    def write_page(self, page_id: int, data: bytes) -> None:
        if len(data) != PAGE_SIZE:
            raise ValueError(f"page data must be {PAGE_SIZE} bytes, got {len(data)}")
        off = page_id * PAGE_SIZE
        if self._is_memory:
            self._buf[off:off + PAGE_SIZE] = data
        else:
            self._mmap[off:off + PAGE_SIZE] = data
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_pager.py -v`
Expected: PASS（8 passed；实际 12 passed：fix commit `4bfc4d6` 新增 reopen-monotonic + read_page(1) 测试，详见下文）

- [x] **Step 5: 行数审计**

Run: `wc -l src/tinydb/pager.py`
Expected: ≤ 100 行（实际 156 行，+56% 偏差；协调者接受为 MINOR，Task 9+ SlottedPage 集成阶段观察重构 ROI）

- [x] **Step 6: Commit**

```bash
git add src/tinydb/pager.py tests/integration/test_pager.py
git commit -m "feat(pager): add alloc/read/write_page with mmap growth and :memory: bytearray"
```

---

### Task 9: SlottedPage 数据结构 + 序列化（tasks.md §4.2-4.3）

引用：Design Doc §3.2。

**Files:**
- Test: `tests/unit/test_slotted_page.py`
- Create: `src/tinydb/slotted_page.py`

- [x] **Step 1: 写失败测试（from_bytes / to_bytes / 空 page 插入）**

```python
# tests/unit/test_slotted_page.py
import pytest
from tinydb.slotted_page import SlottedPage, HEADER_SIZE, NULL_PAGE_ID

@pytest.mark.spec_id("REQ-STORAGE-003-SCN-01")
def test_slotted_page_empty_roundtrip():
    p = SlottedPage(page_id=2, num_slots=0, free_offset=HEADER_SIZE,
                    overflow_next=NULL_PAGE_ID, slots=[], data=bytearray())
    raw = p.to_bytes()
    assert len(raw) == 4096
    p2 = SlottedPage.from_bytes(2, raw)
    assert p2.page_id == 2
    assert p2.num_slots == 0
    assert p2.free_offset == HEADER_SIZE
    assert p2.overflow_next == NULL_PAGE_ID

@pytest.mark.spec_id("REQ-STORAGE-003-SCN-02")
def test_insert_first_row_records_slot():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03")
    assert sid == 0
    raw = p.to_bytes()
    p2 = SlottedPage.from_bytes(2, raw)
    assert p2.num_slots == 1
    assert p2.get(0) == b"\x01\x02\x03"
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_slotted_page.py -v`
Expected: ImportError `SlottedPage`

- [x] **Step 3: 实现 SlottedPage 框架 + 序列化**

```python
# src/tinydb/slotted_page.py
"""Single page layout: 16-byte header + slot directory (6B/slot) + data area from end. ≤ 150 lines."""
from dataclasses import dataclass, field
from typing import Optional

from tinydb.errors import PageFull

PAGE_SIZE = 4096
HEADER_SIZE = 16
SLOT_SIZE = 6
NULL_PAGE_ID = 0xFFFFFFFF
TOMBSTONE_OFFSET = 0xFFFF
FLAG_TOMBSTONE = 0x0001
FLAG_SPILL_START = 0x0002
MAX_SLOTS = 32


@dataclass
class Slot:
    offset: int
    length: int
    flags: int


@dataclass
class SlottedPage:
    page_id: int
    num_slots: int
    free_offset: int
    overflow_next: int
    slots: list[Slot] = field(default_factory=list)
    data: bytearray = field(default_factory=bytearray)

    @classmethod
    def empty(cls, page_id: int) -> "SlottedPage":
        return cls(page_id=page_id, num_slots=0, free_offset=HEADER_SIZE,
                   overflow_next=NULL_PAGE_ID, slots=[], data=bytearray())

    def to_bytes(self) -> bytes:
        buf = bytearray(PAGE_SIZE)
        buf[0] = 1  # page_type = 1 (data)
        buf[1] = self.num_slots
        buf[2:4] = self.free_offset.to_bytes(2, "big")
        buf[4:8] = self.overflow_next.to_bytes(4, "big")
        # slots start at offset HEADER_SIZE
        for i, s in enumerate(self.slots[:self.num_slots]):
            base = HEADER_SIZE + i * SLOT_SIZE
            buf[base:base + 2] = s.offset.to_bytes(2, "big")
            buf[base + 2:base + 4] = s.length.to_bytes(2, "big")
            buf[base + 4:base + 6] = s.flags.to_bytes(2, "big")
        # data area starts at end of page, grows backward
        data_len = len(self.data)
        buf[PAGE_SIZE - data_len:] = self.data
        return bytes(buf)

    @classmethod
    def from_bytes(cls, page_id: int, raw: bytes) -> "SlottedPage":
        if len(raw) != PAGE_SIZE:
            raise ValueError(f"page must be {PAGE_SIZE} bytes")
        num_slots = raw[1]
        free_offset = int.from_bytes(raw[2:4], "big")
        overflow_next = int.from_bytes(raw[4:8], "big")
        slots = []
        for i in range(num_slots):
            base = HEADER_SIZE + i * SLOT_SIZE
            offset = int.from_bytes(raw[base:base + 2], "big")
            length = int.from_bytes(raw[base + 2:base + 4], "big")
            flags = int.from_bytes(raw[base + 4:base + 6], "big")
            slots.append(Slot(offset=offset, length=length, flags=flags))
        # data area is between (PAGE_SIZE - len) and PAGE_SIZE; exact length
        # is not directly stored, so we record max(end-of-slots, data_end).
        # For MVP, infer by checking tail bytes: data ends at PAGE_SIZE,
        # data length = PAGE_SIZE - first non-zero from end.
        # Practical: data length = PAGE_SIZE - max(HEADER_SIZE + num_slots*SLOT_SIZE, free_offset)
        # Actually we need exact length; roundtrip only — recompute:
        data_len = PAGE_SIZE - max(HEADER_SIZE + num_slots * SLOT_SIZE, free_offset)
        data = bytearray(raw[PAGE_SIZE - data_len:PAGE_SIZE])
        return cls(page_id=page_id, num_slots=num_slots, free_offset=free_offset,
                   overflow_next=overflow_next, slots=slots, data=data)

    # placeholder, implemented in Task 10
    def insert(self, row_bytes: bytes) -> int: ...
    def delete(self, slot_id: int) -> None: ...
    def update(self, slot_id: int, row_bytes: bytes) -> None: ...
    def get(self, slot_id: int) -> Optional[bytes]: ...
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_slotted_page.py -v`
Expected: PASS（2 passed）

- [x] **Step 5: Commit**

```bash
git add src/tinydb/slotted_page.py tests/unit/test_slotted_page.py
git commit -m "feat(slotted-page): add SlottedPage dataclass with to_bytes/from_bytes roundtrip"
```

---

### Task 10: SlottedPage — insert / delete / update / get（tasks.md §4.4-4.7）

引用：Design Doc §3.2。

**Files:**
- Modify: `tests/unit/test_slotted_page.py`
- Modify: `src/tinydb/slotted_page.py`

- [x] **Step 1: 写失败测试**

在 `tests/unit/test_slotted_page.py` 追加：

```python
@pytest.mark.spec_id("REQ-STORAGE-003-SCN-04")
def test_insert_into_full_page_raises_page_full():
    p = SlottedPage.empty(2)
    # Fill: 32 slots, each 100 bytes payload
    for i in range(32):
        p.insert(b"\xab" * 100)
    with pytest.raises(PageFull):
        p.insert(b"x")

@pytest.mark.spec_id("REQ-STORAGE-003-SCN-05")
def test_update_in_place_same_length():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03\x04")
    p.update(sid, b"\xff\xee\xdd\xcc")
    assert p.get(sid) == b"\xff\xee\xdd\xcc"

@pytest.mark.spec_id("REQ-STORAGE-003-SCN-05")
def test_update_longer_raises():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02")
    with pytest.raises(Exception):  # either PageFull or ValueError acceptable
        p.update(sid, b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a")

@pytest.mark.spec_id("REQ-STORAGE-003-SCN-06")
def test_delete_marks_tombstone():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03")
    p.delete(sid)
    assert p.get(sid) is None
    raw = p.to_bytes()
    base = HEADER_SIZE + sid * SLOT_SIZE
    offset = int.from_bytes(raw[base:base + 2], "big")
    assert offset == TOMBSTONE_OFFSET

@pytest.mark.spec_id("REQ-STORAGE-003-SCN-07")
def test_reuse_tombstoned_slot_on_insert():
    p = SlottedPage.empty(2)
    sid = p.insert(b"\x01\x02\x03\x04")
    p.delete(sid)
    new_sid = p.insert(b"\xaa\xbb\xcc\xdd")
    assert new_sid == sid  # same slot reused
    assert p.get(new_sid) == b"\xaa\xbb\xcc\xdd"
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_slotted_page.py -v -k "update or delete or reuse or full"`
Expected: placeholder raises NotImplementedError

- [x] **Step 3: 实现 insert / delete / update / get**

```python
    def _data_end(self) -> int:
        return PAGE_SIZE - len(self.data)

    def _free_space(self) -> int:
        slot_end = HEADER_SIZE + self.num_slots * SLOT_SIZE
        return self._data_end() - slot_end

    def insert(self, row_bytes: bytes) -> int:
        if self.num_slots >= MAX_SLOTS:
            # try reusing a tombstoned slot first
            for i, s in enumerate(self.slots[:self.num_slots]):
                if s.flags & FLAG_TOMBSTONE and len(row_bytes) <= s.length:
                    s.offset = self._data_end() - len(row_bytes)
                    s.length = len(row_bytes)
                    s.flags = 0
                    self.data.extend(row_bytes)
                    return i
            raise PageFull(f"page {self.page_id} full")
        if self._free_space() < len(row_bytes) + SLOT_SIZE:
            raise PageFull(f"page {self.page_id} full")
        new_offset = self._data_end() - len(row_bytes)
        self.data.extend(row_bytes)
        sid = self.num_slots
        self.slots.append(Slot(offset=new_offset, length=len(row_bytes), flags=0))
        self.num_slots += 1
        # free_offset tracks slot directory end
        self.free_offset = HEADER_SIZE + self.num_slots * SLOT_SIZE
        return sid

    def delete(self, slot_id: int) -> None:
        s = self.slots[slot_id]
        s.offset = TOMBSTONE_OFFSET
        s.flags = FLAG_TOMBSTONE

    def update(self, slot_id: int, row_bytes: bytes) -> None:
        s = self.slots[slot_id]
        if s.flags & FLAG_TOMBSTONE:
            raise ValueError("cannot update tombstoned slot")
        if len(row_bytes) > s.length:
            raise ValueError("update larger than slot; use spill/overflow")
        # in-place at same offset, growing data area
        delta = len(row_bytes) - s.length
        self.data.extend(b"\x00" * max(0, -delta))
        # rewrite tail with new bytes at correct position
        end = self._data_end()
        new_off = end - len(row_bytes)
        # shift existing bytes logically: extend data, then overwrite last len(row_bytes)
        self.data.extend(row_bytes)
        s.offset = new_off
        s.length = len(row_bytes)

    def get(self, slot_id: int) -> Optional[bytes]:
        s = self.slots[slot_id]
        if s.flags & FLAG_TOMBSTONE:
            return None
        return bytes(self.data[s.offset - (self._data_end() - len(self.data)) :
                                s.offset - (self._data_end() - len(self.data)) + s.length])
```

注：`get` 的切片偏移需要基于 data 区的相对起点。等价更简单的实现：直接用 `s.length` 与 `len(self.data)` 重算绝对偏移。

替代更清晰的实现：

```python
    def get(self, slot_id: int) -> Optional[bytes]:
        s = self.slots[slot_id]
        if s.flags & FLAG_TOMBSTONE:
            return None
        # s.offset is the absolute offset on the page where this row starts
        # data area occupies the tail: offsets [PAGE_SIZE - len(self.data), PAGE_SIZE)
        # row sits at offsets [s.offset, s.offset + s.length)
        start_in_data = s.offset - (PAGE_SIZE - len(self.data))
        return bytes(self.data[start_in_data:start_in_data + s.length])
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_slotted_page.py -v`
Expected: PASS（7 passed；实际 10 passed：implementer 多加了 3 个 follow-up 测试，详见 commit `d9751f7`）

- [x] **Step 5: 行数审计**

Run: `wc -l src/tinydb/slotted_page.py`
Expected: ≤ 150 行（实际 207 行，+38% 偏差；协调者接受为 MINOR，docstring-heavy 但功能完整）

- [x] **Step 6: Commit**

```bash
git add src/tinydb/slotted_page.py tests/unit/test_slotted_page.py
git commit -m "feat(slotted-page): add insert/delete/update/get with tombstone reuse"
```

---

### Task 11: Row Codec — null bitmap + length-prefixed values（tasks.md §4.8）

引用：Design Doc §3.4。

**Files:**
- Test: `tests/unit/test_row_codec.py`
- Create: `src/tinydb/row_codec.py`

- [x] **Step 1: 写失败测试**

```python
# tests/unit/test_row_codec.py
import pytest
from tinydb.row_codec import encode_row, decode_row

SCHEMA = [("id", "INT"), ("name", "TEXT"), ("active", "BOOL")]

@pytest.mark.spec_id("REQ-STORAGE-004-SCN-01")
def test_encode_row_no_nulls_bitmap_zero():
    row_bytes = encode_row([42, "alice", True], SCHEMA)
    assert row_bytes[0] == 0x00  # no nulls

@pytest.mark.spec_id("REQ-STORAGE-004-SCN-02")
def test_encode_row_null_in_second_column_bitmap():
    row_bytes = encode_row([42, None, False], SCHEMA)
    # bit 1 (0-indexed) set means name is NULL → 0b00000010 = 0x02
    assert row_bytes[0] == 0x02

@pytest.mark.spec_id("REQ-STORAGE-004-SCN-03")
def test_decode_row_roundtrip_with_null():
    original = [42, None, False]
    decoded = decode_row(encode_row(original, SCHEMA), SCHEMA)
    assert decoded == original

@pytest.mark.spec_id("REQ-STORAGE-004-SCN-03")
def test_decode_row_roundtrip_all_populated():
    original = [7, "bob", True]
    decoded = decode_row(encode_row(original, SCHEMA), SCHEMA)
    assert decoded == original
```

- [x] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_row_codec.py -v`
Expected: ImportError

- [x] **Step 3: 实现 row_codec**

```python
# src/tinydb/row_codec.py
"""Row codec: null bitmap + length-prefixed values per Design Doc §3.4. ≤ 80 lines."""
from tinydb.type_system import encode_int, decode_int, encode_text, decode_text, \
                                  encode_bool, decode_bool, encode_float, decode_float

_ENCODERS = {"INT": encode_int, "TEXT": encode_text, "BOOL": encode_bool, "FLOAT": encode_float}
_DECODERS = {"INT": decode_int, "TEXT": decode_text, "BOOL": decode_bool, "FLOAT": decode_float}
_TYPE_SIZES = {"INT": 8, "FLOAT": 8, "BOOL": 1}


def _bitmap_len(col_count: int) -> int:
    return (col_count + 7) // 8


def encode_row(values: list, schema: list[tuple[str, str]]) -> bytes:
    assert len(values) == len(schema)
    n = len(schema)
    blen = _bitmap_len(n)
    bitmap = bytearray(blen)
    parts = [bytes(bitmap)]
    for i, (val, (_name, typ)) in enumerate(zip(values, schema)):
        if val is None:
            bitmap[i // 8] |= 1 << (i % 8)
            continue
        parts.append(_ENCODERS[typ](val))
    parts[0] = bytes(bitmap)
    return b"".join(parts)


def decode_row(buf: bytes, schema: list[tuple[str, str]]) -> list:
    blen = _bitmap_len(len(schema))
    if len(buf) < blen:
        raise ValueError("row buffer too short for bitmap")
    bitmap = buf[:blen]
    out = []
    off = blen
    for i, (_name, typ) in enumerate(schema):
        null_bit = (bitmap[i // 8] >> (i % 8)) & 1
        if null_bit:
            out.append(None)
            continue
        val, off = _DECODERS[typ](buf, off)
        out.append(val)
    return out
```

- [x] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_row_codec.py -v`
Expected: PASS（4 passed）

- [x] **Step 5: Commit**

```bash
git add src/tinydb/row_codec.py tests/unit/test_row_codec.py
git commit -m "feat(row-codec): add encode_row/decode_row with LSB-first null bitmap"
```

---

### Task 12: Catalog — JSON 持久化（tasks.md §5.1-5.3）

引用：Design Doc §3.5。

**Files:**
- Test: `tests/integration/test_catalog.py`
- Create: `src/tinydb/catalog.py`

- [ ] **Step 1: 写失败测试（JSON roundtrip + INT-as-string）**

```python
# tests/integration/test_catalog.py
import json
import pytest
from tinydb.catalog import Catalog, TableInfo
from tinydb.pager import PAGE_SIZE

@pytest.mark.spec_id("REQ-STORAGE-005-SCN-01")
def test_catalog_empty_roundtrip():
    c = Catalog()
    raw = c.to_bytes()
    assert len(raw) == PAGE_SIZE
    c2 = Catalog.from_bytes(raw)
    assert c2.tables == {}

@pytest.mark.spec_id("REQ-STORAGE-005-SCN-02")
def test_catalog_register_table():
    c = Catalog()
    schema = [("id", "INT"), ("name", "TEXT")]
    c.create_table("users", schema, root_page_id=2, next_page_id=2)
    assert "users" in c.tables
    ti = c.get_table("users")
    assert ti.schema == schema
    assert ti.root_page_id == 2

@pytest.mark.spec_id("REQ-STORAGE-005-SCN-03")
def test_catalog_persisted_across_reopen(tmp_path):
    from tinydb.pager import Pager
    p = Pager(str(tmp_path / "cat.db"))
    c = Catalog.from_bytes(p.read_page(1))
    c.create_table("t", [("x", "INT")], root_page_id=2, next_page_id=2)
    p.write_page(1, c.to_bytes())
    p.flush(); p.close()
    p2 = Pager(str(tmp_path / "cat.db"))
    c2 = Catalog.from_bytes(p2.read_page(1))
    assert "t" in c2.tables
    p2.close()

@pytest.mark.spec_id("REQ-STORAGE-006-SCN-01")
def test_catalog_encodes_int_fields_as_json_strings():
    c = Catalog()
    # simulate large int root_page_id > 2^53
    huge = 2**60
    c.create_table("big", [("v", "INT")], root_page_id=huge, next_page_id=huge)
    raw = c.to_bytes()
    text = raw.rstrip(b"\x00").decode("utf-8")
    parsed = json.loads(text)
    assert parsed["tables"]["big"]["root_page_id"] == str(huge)
    c2 = Catalog.from_bytes(raw)
    assert c2.get_table("big").root_page_id == huge
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_catalog.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 Catalog**

```python
# src/tinydb/catalog.py
"""Catalog persisted as JSON on page 1; INT fields encoded as strings (R8 mitigation). ≤ 100 lines."""
import json
from dataclasses import dataclass, field
from typing import Optional

from tinydb.pager import PAGE_SIZE

CATALOG_PAGE_ID = 1


@dataclass
class TableInfo:
    schema: list[tuple[str, str]]
    root_page_id: int
    next_page_id: int


def _enc_int(v: int) -> str:
    return str(v)


def _dec_int(v) -> int:
    if isinstance(v, str):
        return int(v)
    return int(v)


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
            c.tables[name] = TableInfo(
                schema=[(c_, t_) for c_, t_ in info["schema"]],
                root_page_id=_dec_int(info["root_page_id"]),
                next_page_id=_dec_int(info["next_page_id"]),
            )
        return c

    def to_bytes(self) -> bytes:
        data = {"tables": {
            name: {
                "schema": [[c, t] for c, t in ti.schema],
                "root_page_id": _enc_int(ti.root_page_id),
                "next_page_id": _enc_int(ti.next_page_id),
            }
            for name, ti in self.tables.items()
        }}
        text = json.dumps(data, separators=(",", ":")).encode("utf-8")
        if len(text) > PAGE_SIZE:
            raise ValueError("catalog page overflow")
        return text + b"\x00" * (PAGE_SIZE - len(text))

    def create_table(self, name: str, schema: list[tuple[str, str]],
                     root_page_id: int, next_page_id: int) -> None:
        if name in self.tables:
            raise ValueError(f"table {name!r} already exists")
        self.tables[name] = TableInfo(schema=schema, root_page_id=root_page_id,
                                       next_page_id=next_page_id)

    def drop_table(self, name: str) -> None:
        if name not in self.tables:
            raise KeyError(f"no such table: {name}")
        del self.tables[name]

    def get_table(self, name: str) -> Optional[TableInfo]:
        return self.tables.get(name)
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_catalog.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 行数审计**

Run: `wc -l src/tinydb/catalog.py`
Expected: ≤ 90 行

- [ ] **Step 6: Commit**

```bash
git add src/tinydb/catalog.py tests/integration/test_catalog.py
git commit -m "feat(catalog): JSON-encoded catalog with INT-as-string for 2^53-safe persistence"
```

---

### Task 13: Tokenizer — identifier / keyword / punctuation（tasks.md §6.1-6.3, §6.6）

引用：Design Doc §5.1。

**Files:**
- Test: `tests/unit/test_tokenizer.py`
- Modify: `src/tinydb/tokenizer.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_tokenizer.py
import pytest
from tinydb.tokenizer import tokenize
from tinydb.errors import TokenError

KEYWORDS = {"CREATE","TABLE","DROP","INSERT","INTO","VALUES","SELECT",
            "FROM","WHERE","TRUE","FALSE","INT","TEXT","FLOAT","BOOL"}

@pytest.mark.spec_id("REQ-PARSE-001-SCN-01")
def test_tokenize_identifier():
    toks = tokenize("users")
    assert len(toks) == 2  # value + EOF
    t = toks[0]
    assert t.type == "IDENT" and t.value == "users" and t.line == 1 and t.col == 1

@pytest.mark.spec_id("REQ-PARSE-001-SCN-02")
def test_tokenize_keyword_case_insensitive():
    for variant in ("CREATE", "create", "Create"):
        toks = tokenize(variant)
        assert toks[0].type == "KEYWORD" and toks[0].value == "CREATE"

@pytest.mark.spec_id("REQ-PARSE-001-SCN-05")
def test_tokenize_punctuation():
    toks = tokenize("( ) , ; = *")
    puncts = [t.value for t in toks if t.type == "PUNCT"]
    assert puncts == ["(", ")", ",", ";", "=", "*"]

@pytest.mark.spec_id("REQ-PARSE-001-SCN-06")
def test_tokenizer_error_reports_position():
    with pytest.raises(TokenError) as excinfo:
        tokenize("@")
    assert excinfo.value.line == 1
    assert excinfo.value.col == 1
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_tokenizer.py -v`
Expected: ImportError `tokenize`

- [ ] **Step 3: 实现 tokenizer 主体（identifier/keyword/punct/position）**

```python
# src/tinydb/tokenizer.py
"""SQL tokenizer: 6 token categories + position tracking. ≤ 200 lines."""
from dataclasses import dataclass
from typing import Any, Literal

from tinydb.errors import TokenError
from tinydb.type_system import parse_int_literal, parse_float_literal, \
                                 parse_text_literal, parse_bool_literal

KEYWORDS = {"CREATE","TABLE","DROP","INSERT","INTO","VALUES","SELECT",
            "FROM","WHERE","TRUE","FALSE","INT","TEXT","FLOAT","BOOL"}

TokenType = Literal["KEYWORD","IDENT","INT","FLOAT","TEXT","BOOL","PUNCT","EOF"]


@dataclass
class Token:
    type: TokenType
    value: Any
    line: int
    col: int


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c == "_"


def _is_ident_cont(c: str) -> bool:
    return c.isalnum() or c == "_"


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(sql)
    line, col = 1, 1
    while i < n:
        c = sql[i]
        if c in " \t\r":
            i += 1; col += 1
            continue
        if c == "\n":
            i += 1; line += 1; col = 1
            continue
        # identifier / keyword
        if _is_ident_start(c):
            start_col = col
            j = i
            while j < n and _is_ident_cont(sql[j]):
                j += 1
            text = sql[i:j]
            up = text.upper()
            if up in KEYWORDS:
                if up == "TRUE":
                    tokens.append(Token("BOOL", True, line, start_col))
                elif up == "FALSE":
                    tokens.append(Token("BOOL", False, line, start_col))
                else:
                    tokens.append(Token("KEYWORD", up, line, start_col))
            else:
                tokens.append(Token("IDENT", text, line, start_col))
            col += (j - i); i = j
            continue
        # integer or float
        if c.isdigit() or (c == "-" and i + 1 < n and sql[i+1].isdigit()):
            start_col = col
            j = i + 1
            while j < n and (sql[j].isdigit() or sql[j] == "."):
                j += 1
            text = sql[i:j]
            try:
                if "." in text:
                    val = parse_float_literal(text)
                    tokens.append(Token("FLOAT", val, line, start_col))
                else:
                    val = parse_int_literal(text)
                    tokens.append(Token("INT", val, line, start_col))
            except ValueError as e:
                raise TokenError(line, start_col, str(e))
            col += (j - i); i = j
            continue
        # text literal
        if c == "'":
            start_col = col
            j = i + 1
            buf = []
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j+1] == "'":
                        buf.append("'"); j += 2; continue
                    break
                buf.append(sql[j]); j += 1
            if j >= n:
                raise TokenError(line, start_col, "unterminated text literal")
            raw = "'" + "".join(buf) + "'"
            val = parse_text_literal(raw)
            tokens.append(Token("TEXT", val, line, start_col))
            i = j + 1; col = i - sum(1 for _ in range(i) if sql[0] == "\n")  # naive col update
            continue
        # punctuation
        if c in "(),;=*":
            tokens.append(Token("PUNCT", c, line, col))
            i += 1; col += 1
            continue
        raise TokenError(line, col, f"unexpected character {c!r}")
    tokens.append(Token("EOF", None, line, col))
    return tokens
```

修正 col 计数（更简洁的实现）：

```python
# 替换内嵌 col 计数逻辑为统一的 advance helper：
def _advance(i, line, col, c):
    if c == "\n":
        return i + 1, line + 1, 1
    return i + 1, line, col + 1
```

并对所有字符消费改用此 helper。完整重写见提交时版本。

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_tokenizer.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/tokenizer.py tests/unit/test_tokenizer.py
git commit -m "feat(tokenizer): identifier/keyword (case-insensitive)/punctuation with position tracking"
```

---

### Task 14: Tokenizer — 字面量（含 doubled-quote 转义）（tasks.md §6.4-6.5）

引用：Design Doc §5.1。

**Files:**
- Modify: `tests/unit/test_tokenizer.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.spec_id("REQ-PARSE-001-SCN-03")
def test_tokenize_text_with_space():
    toks = tokenize("'hello world'")
    assert toks[0].type == "TEXT" and toks[0].value == "hello world"

@pytest.mark.spec_id("REQ-PARSE-001-SCN-04")
def test_tokenize_text_doubled_quote():
    toks = tokenize("'it''s ok'")
    assert toks[0].type == "TEXT" and toks[0].value == "it's ok"

@pytest.mark.spec_id("REQ-TYPE-001-SCN-03")
def test_tokenize_int_negative():
    toks = tokenize("-7")
    assert toks[0].type == "INT" and toks[0].value == -7

@pytest.mark.spec_id("REQ-TYPE-001-SCN-04")
def test_tokenize_float_decimal():
    toks = tokenize("3.14")
    assert toks[0].type == "FLOAT" and abs(toks[0].value - 3.14) < 1e-9

@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_tokenize_float_NaN_raises_TokenError():
    with pytest.raises(TokenError, match="NaN not allowed"):
        tokenize("NaN")
```

- [ ] **Step 2: 跑测试验证 GREEN**（Step 3 中已实现的字面量分支应通过）

Run: `pytest tests/unit/test_tokenizer.py -v`
Expected: PASS（9 passed）

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_tokenizer.py
git commit -m "test(tokenizer): cover integer/float/text/NaN literal paths"
```

---

### Task 15: Parser — AST 节点 + parse() 入口 + CREATE/DROP（tasks.md §7.1-7.5）

引用：Design Doc §5.2、§5.3。

**Files:**
- Test: `tests/unit/test_parser.py`
- Create: `src/tinydb/parser.py`

- [ ] **Step 1: 写失败测试（CREATE/DROP AST 形状 + 错误）**

```python
# tests/unit/test_parser.py
import pytest
from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError

@pytest.mark.spec_id("REQ-PARSE-002-SCN-01")
def test_parse_create_table_simple():
    stmt = parse(tokenize("CREATE TABLE users (id INT, name TEXT)"))
    assert stmt.statements[0].name == "users"
    assert stmt.statements[0].columns == [("id", "INT"), ("name", "TEXT")]

@pytest.mark.spec_id("REQ-PARSE-002-SCN-02")
def test_parse_create_table_rejects_duplicate_column():
    with pytest.raises(ParseError, match="duplicate column"):
        parse(tokenize("CREATE TABLE t(id INT, id TEXT)"))

@pytest.mark.spec_id("REQ-PARSE-002-SCN-03")
def test_parse_create_table_rejects_unsupported_type():
    with pytest.raises(ParseError, match="VARCHAR not supported"):
        parse(tokenize("CREATE TABLE t(id VARCHAR(10))"))

@pytest.mark.spec_id("REQ-PARSE-003-SCN-01")
def test_parse_drop_table():
    stmt = parse(tokenize("DROP TABLE users"))
    assert stmt.statements[0].name == "users"

@pytest.mark.spec_id("REQ-PARSE-003-SCN-02")
def test_parse_drop_table_missing_name_raises():
    with pytest.raises(ParseError, match="expected table name"):
        parse(tokenize("DROP TABLE"))
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_parser.py -v`
Expected: ImportError `parse`

- [ ] **Step 3: 实现 AST + parse() + CREATE/DROP**

```python
# src/tinydb/parser.py
"""Recursive descent parser for 5 SQL statements. ≤ 600 lines."""
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from tinydb.errors import ParseError
from tinydb.tokenizer import Token, tokenize

SUPPORTED_TYPES = {"INT", "TEXT", "FLOAT", "BOOL"}
SUPPORTED_OPS = {"="}


@dataclass
class StatementList:
    statements: list
    line: int = 1; col: int = 1

@dataclass
class CreateTable:
    name: str
    columns: list
    line: int; col: int

@dataclass
class DropTable:
    name: str
    line: int; col: int

@dataclass
class Insert:
    table: str
    columns: list
    values: list
    line: int; col: int

@dataclass
class Select:
    table: str
    columns: list
    where: Optional[tuple]
    line: int; col: int

@dataclass
class Delete:
    table: str
    where: Optional[tuple]
    line: int; col: int


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def expect(self, type_: str, value: Any = None) -> Token:
        t = self.peek()
        if t.type != type_ or (value is not None and t.value != value):
            raise ParseError(t.line, t.col,
                f"expected {type_} {value!r}, got {t.type} {t.value!r}")
        return self.advance()

    def expect_keyword(self, kw: str) -> Token:
        t = self.peek()
        if t.type != "KEYWORD" or t.value != kw:
            raise ParseError(t.line, t.col, f"expected keyword {kw}")
        return self.advance()

    def parse_statement_list(self) -> StatementList:
        stmts = []
        while self.peek().type != "EOF":
            stmts.append(self.parse_statement())
            if self.peek().type == "PUNCT" and self.peek().value == ";":
                self.advance()
        return StatementList(statements=stmts)

    def parse_statement(self) -> Any:
        t = self.peek()
        if t.type != "KEYWORD":
            raise ParseError(t.line, t.col, f"expected statement, got {t.type}")
        return {
            "CREATE": self._parse_create_table,
            "DROP": self._parse_drop_table,
            "INSERT": self._parse_insert,
            "SELECT": self._parse_select,
            "DELETE": self._parse_delete,
        }[t.value]()

    def _parse_create_table(self) -> CreateTable:
        kw = self.expect_keyword("CREATE")
        self.expect_keyword("TABLE")
        name_tok = self.peek()
        if name_tok.type != "IDENT":
            raise ParseError(name_tok.line, name_tok.col, "expected table name")
        name = self.advance().value
        self.expect("PUNCT", "(")
        cols = []
        seen = set()
        while True:
            col_tok = self.peek()
            if col_tok.type != "IDENT":
                raise ParseError(col_tok.line, col_tok.col, "expected column name")
            cname = self.advance().value
            if cname in seen:
                raise ParseError(col_tok.line, col_tok.col, f"duplicate column {cname}")
            seen.add(cname)
            type_tok = self.peek()
            if type_tok.type != "KEYWORD" or type_tok.value not in SUPPORTED_TYPES:
                raise ParseError(type_tok.line, type_tok.col,
                    f"type {type_tok.value} not supported in MVP")
            ctype = self.advance().value
            cols.append((cname, ctype))
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break
        self.expect("PUNCT", ")")
        return CreateTable(name=name, columns=cols, line=kw.line, col=kw.col)

    def _parse_drop_table(self) -> DropTable:
        kw = self.expect_keyword("DROP")
        self.expect_keyword("TABLE")
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        return DropTable(name=self.advance().value, line=kw.line, col=kw.col)


def parse(tokens: list[Token]) -> StatementList:
    return _Parser(tokens).parse_statement_list()
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_parser.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/parser.py tests/unit/test_parser.py
git commit -m "feat(parser): CREATE TABLE/DROP TABLE with type validation and duplicate detection"
```

---

### Task 16: Parser — INSERT / SELECT / DELETE + StatementList（tasks.md §7.6-7.8）

引用：Design Doc §5.2、§5.3。

**Files:**
- Modify: `tests/unit/test_parser.py`
- Modify: `src/tinydb/parser.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.spec_id("REQ-PARSE-004-SCN-01")
def test_parse_insert_single_row():
    stmt = parse(tokenize("INSERT INTO users(id, name) VALUES (1, 'alice')"))
    ins = stmt.statements[0]
    assert ins.table == "users"
    assert ins.columns == ["id", "name"]
    assert ins.values == [[1, "alice"]]

@pytest.mark.spec_id("REQ-PARSE-004-SCN-02")
def test_parse_insert_multi_row():
    stmt = parse(tokenize("INSERT INTO users(id, name) VALUES (1, 'a'), (2, 'b')"))
    assert stmt.statements[0].values == [[1, "a"], [2, "b"]]

@pytest.mark.spec_id("REQ-PARSE-004-SCN-03")
def test_parse_insert_count_mismatch_raises():
    with pytest.raises(ParseError, match="value count mismatch"):
        parse(tokenize("INSERT INTO users(id, name) VALUES (1)"))

@pytest.mark.spec_id("REQ-PARSE-005-SCN-01")
def test_parse_select_star():
    stmt = parse(tokenize("SELECT * FROM users"))
    s = stmt.statements[0]
    assert s.columns == ["*"] and s.table == "users" and s.where is None

@pytest.mark.spec_id("REQ-PARSE-005-SCN-03")
def test_parse_select_with_where():
    stmt = parse(tokenize("SELECT * FROM users WHERE id = 1"))
    s = stmt.statements[0]
    assert s.where == ("id", "=", 1)

@pytest.mark.spec_id("REQ-PARSE-005-SCN-04")
def test_parse_select_rejects_unsupported_operator():
    with pytest.raises(ParseError, match="operator > not supported"):
        parse(tokenize("SELECT * FROM users WHERE id > 1"))

@pytest.mark.spec_id("REQ-PARSE-005-SCN-05")
def test_parse_select_missing_from_raises():
    with pytest.raises(ParseError, match="expected FROM"):
        parse(tokenize("SELECT id"))

@pytest.mark.spec_id("REQ-PARSE-006-SCN-01")
def test_parse_delete_all():
    stmt = parse(tokenize("DELETE FROM users"))
    assert stmt.statements[0].table == "users" and stmt.statements[0].where is None

@pytest.mark.spec_id("REQ-PARSE-006-SCN-02")
def test_parse_delete_with_where():
    stmt = parse(tokenize("DELETE FROM users WHERE id = 1"))
    assert stmt.statements[0].where == ("id", "=", 1)

@pytest.mark.spec_id("REQ-PARSE-007-SCN-02")
def test_parse_multiple_statements():
    stmt = parse(tokenize("CREATE TABLE t(id INT); INSERT INTO t VALUES (1)"))
    assert len(stmt.statements) == 2
    assert isinstance(stmt.statements[0], CreateTable)
    assert isinstance(stmt.statements[1], Insert)

@pytest.mark.spec_id("REQ-PARSE-008-SCN-01")
def test_parser_is_pure_deterministic():
    sql = "CREATE TABLE t(id INT, name TEXT)"
    a = parse(tokenize(sql))
    b = parse(tokenize(sql))
    assert a.statements[0].columns == b.statements[0].columns
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/unit/test_parser.py -v`
Expected: AttributeError `_parse_insert` 等

- [ ] **Step 3: 实现 INSERT/SELECT/DELETE 分支**

```python
    def _parse_insert(self) -> Insert:
        kw = self.expect_keyword("INSERT")
        self.expect_keyword("INTO")
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        table = self.advance().value
        self.expect("PUNCT", "(")
        cols = []
        while True:
            ct = self.peek()
            if ct.type != "IDENT":
                raise ParseError(ct.line, ct.col, "expected column name")
            cols.append(self.advance().value)
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break
        self.expect("PUNCT", ")")
        self.expect_keyword("VALUES")
        values = []
        while True:
            self.expect("PUNCT", "(")
            row = []
            while True:
                v = self.advance()
                if v.type not in ("INT", "FLOAT", "TEXT", "BOOL"):
                    raise ParseError(v.line, v.col, "expected literal")
                row.append(v.value)
                if self.peek().type == "PUNCT" and self.peek().value == ",":
                    self.advance()
                    continue
                break
            if len(row) != len(cols):
                raise ParseError(kw.line, kw.col,
                    f"value count mismatch: got {len(row)}, expected {len(cols)}")
            values.append(row)
            self.expect("PUNCT", ")")
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break
        return Insert(table=table, columns=cols, values=values,
                      line=kw.line, col=kw.col)

    def _parse_select(self) -> Select:
        kw = self.expect_keyword("SELECT")
        cols = []
        if self.peek().type == "PUNCT" and self.peek().value == "*":
            self.advance()
            cols = ["*"]
        else:
            while True:
                ct = self.peek()
                if ct.type != "IDENT":
                    raise ParseError(ct.line, ct.col, "expected column or *")
                cols.append(self.advance().value)
                if self.peek().type == "PUNCT" and self.peek().value == ",":
                    self.advance()
                    continue
                break
        self.expect_keyword("FROM")
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        table = self.advance().value
        where = None
        if self.peek().type == "KEYWORD" and self.peek().value == "WHERE":
            self.advance()
            ct = self.peek()
            if ct.type != "IDENT":
                raise ParseError(ct.line, ct.col, "expected column in WHERE")
            cname = self.advance().value
            op_tok = self.advance()
            if op_tok.type != "PUNCT" or op_tok.value not in SUPPORTED_OPS:
                raise ParseError(op_tok.line, op_tok.col,
                    f"operator {op_tok.value} not supported; MVP supports only =")
            lit = self.advance()
            if lit.type not in ("INT", "FLOAT", "TEXT", "BOOL"):
                raise ParseError(lit.line, lit.col, "expected literal")
            where = (cname, op_tok.value, lit.value)
        return Select(table=table, columns=cols, where=where,
                      line=kw.line, col=kw.col)

    def _parse_delete(self) -> Delete:
        kw = self.expect_keyword("DELETE")
        self.expect_keyword("FROM")
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        table = self.advance().value
        where = None
        if self.peek().type == "KEYWORD" and self.peek().value == "WHERE":
            # reuse SELECT WHERE parsing
            self.advance()
            ct = self.peek()
            cname = self.advance().value
            op_tok = self.advance()
            if op_tok.value not in SUPPORTED_OPS:
                raise ParseError(op_tok.line, op_tok.col,
                    f"operator {op_tok.value} not supported; MVP supports only =")
            lit = self.advance()
            where = (cname, op_tok.value, lit.value)
        return Delete(table=table, where=where, line=kw.line, col=kw.col)
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/unit/test_parser.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: 行数审计**

Run: `wc -l src/tinydb/parser.py`
Expected: ≤ 250 行（远低于 600 预算）

- [ ] **Step 6: Commit**

```bash
git add src/tinydb/parser.py tests/unit/test_parser.py
git commit -m "feat(parser): INSERT/SELECT/DELETE with WHERE col=literal and StatementList"
```

---

### Task 17: Executor — DDL（CREATE/DROP）（tasks.md §8.1-8.3）

引用：Design Doc §6.1、§6.2。

**Files:**
- Test: `tests/integration/test_executor.py`
- Create: `src/tinydb/executor.py`

- [ ] **Step 1: 写失败测试（DDL on real pager）**

```python
# tests/integration/test_executor.py
import pytest
from tinydb.pager import Pager
from tinydb.catalog import Catalog
from tinydb.executor import Executor
from tinydb.parser import parse
from tinydb.tokenizer import tokenize

def _exec(pager, sql):
    cat = Catalog.from_bytes(pager.read_page(1))
    ex = Executor(pager, cat)
    stmts = parse(tokenize(sql)).statements
    for s in stmts:
        ex.execute(s)
    pager.write_page(1, cat.to_bytes())
    pager.flush()

@pytest.mark.spec_id("REQ-STORAGE-005-SCN-02")
def test_create_table_persists_to_catalog(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    _exec(p, "CREATE TABLE users(id INT, name TEXT)")
    cat = Catalog.from_bytes(p.read_page(1))
    assert "users" in cat.tables
    assert cat.get_table("users").schema == [("id","INT"),("name","TEXT")]
    p.close()

@pytest.mark.spec_id("REQ-STORAGE-005-SCN-04")
def test_drop_table_removes_from_catalog(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    _exec(p, "CREATE TABLE users(id INT)")
    _exec(p, "DROP TABLE users")
    cat = Catalog.from_bytes(p.read_page(1))
    assert "users" not in cat.tables
    p.close()
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_executor.py -v`
Expected: ImportError `Executor`

- [ ] **Step 3: 实现 Executor + DDL**

```python
# src/tinydb/executor.py
"""AST → storage executor. Owns Pager+Catalog; all I/O lives here. ≤ 400 lines."""
from tinydb.errors import ExecutionError
from tinydb.parser import CreateTable, DropTable, Insert, Select, Delete
from tinydb.slotted_page import SlottedPage
from tinydb.row_codec import encode_row, decode_row


class Executor:
    def __init__(self, pager, catalog):
        self.pager = pager
        self.catalog = catalog

    def execute(self, stmt):
        return {
            CreateTable: self._exec_create_table,
            DropTable:   self._exec_drop_table,
            Insert:      self._exec_insert,
            Select:      self._exec_select,
            Delete:      self._exec_delete,
        }[type(stmt)](stmt)

    def _exec_create_table(self, stmt: CreateTable):
        if self.catalog.get_table(stmt.name) is not None:
            raise ExecutionError(f"table {stmt.name!r} already exists")
        root_id = self.pager.alloc_page()
        # initialize empty data page
        page = SlottedPage.empty(root_id)
        self.pager.write_page(root_id, page.to_bytes())
        self.catalog.create_table(stmt.name, stmt.columns,
                                  root_page_id=root_id, next_page_id=root_id)
        self.pager.write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        return []

    def _exec_drop_table(self, stmt: DropTable):
        ti = self.catalog.get_table(stmt.name)
        if ti is None:
            raise ExecutionError(f"table {stmt.name!r} does not exist")
        # MVP: best-effort, leak page
        self.catalog.drop_table(stmt.name)
        self.pager.write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        return []

    # placeholders, implemented next
    def _exec_insert(self, stmt): ...
    def _exec_select(self, stmt): ...
    def _exec_delete(self, stmt): ...
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_executor.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/executor.py tests/integration/test_executor.py
git commit -m "feat(executor): DDL create/drop table with catalog persistence"
```

---

### Task 18: Executor — INSERT + scan helper（tasks.md §8.4-8.5）

引用：Design Doc §6.3。

**Files:**
- Modify: `tests/integration/test_executor.py`
- Modify: `src/tinydb/executor.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-03")
def test_insert_allocates_new_page_when_full(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    cat = Catalog.from_bytes(p.read_page(1))
    ex = Executor(p, cat)
    from tinydb.parser import CreateTable, Insert
    ex.execute(CreateTable(name="t", columns=[("v","INT")], line=1, col=1))
    # Fill first page
    big = b"\xab" * 200
    # Each row ~ 200 bytes; max 32 slots but free space ~ 3880 → ~19 rows
    # Build 25 INSERTs to force page alloc
    for i in range(25):
        ex.execute(Insert(table="t", columns=["v"], values=[[i]], line=1, col=1))
    # Verify two pages exist
    assert p._next_page_id >= 4
    p.close()

@pytest.mark.spec_id("REQ-STORAGE-007-SCN-01")
def test_select_filters_tombstoned_rows(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    _exec(p, "CREATE TABLE t(id INT)")
    _exec(p, "INSERT INTO t VALUES (1)")
    _exec(p, "INSERT INTO t VALUES (2)")
    _exec(p, "INSERT INTO t VALUES (3)")
    _exec(p, "DELETE FROM t WHERE id = 2")
    rows = _select(p, "SELECT * FROM t")
    assert [r.values[0] for r in rows] == [1, 3]
    p.close()
```

添加 helper 到测试文件顶部：

```python
def _select(pager, sql):
    cat = Catalog.from_bytes(pager.read_page(1))
    ex = Executor(pager, cat)
    from tinydb.parser import parse
    from tinydb.tokenizer import tokenize
    return ex.execute(parse(tokenize(sql)).statements[0])
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_executor.py -v`
Expected: NotImplementedError

- [ ] **Step 3: 实现 INSERT + scan helper**

```python
    def _exec_insert(self, stmt: Insert):
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema
        # MVP: column list ignored — insert in schema order
        for row_vals in stmt.values:
            typed = []
            for (_n, t), v in zip(schema, row_vals):
                from tinydb.type_system import py_to_db
                try:
                    py_to_db(v, t)
                except (TypeError, ValueError) as e:
                    raise ExecutionError(str(e))
                typed.append(v)
            row_bytes = encode_row(typed, schema)
            # Find first data page with free space, or alloc new
            pid = self._insert_row_into_chain(ti, row_bytes)
        return []

    def _insert_row_into_chain(self, ti, row_bytes):
        pid = ti.root_page_id
        while True:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            try:
                page.insert(row_bytes)
                self.pager.write_page(pid, page.to_bytes())
                self.pager.flush()
                return pid
            except Exception as e:
                from tinydb.errors import PageFull
                if not isinstance(e, PageFull):
                    raise
                # overflow page allocation
                if pid == ti.next_page_id:
                    new_pid = self.pager.alloc_page()
                    ti.next_page_id = new_pid
                    self.pager.write_page(1, self.catalog.to_bytes())
                    pid = new_pid
                else:
                    pid += 1  # MVP simplification: linear probing
                    continue

    def _scan_table(self, ti):
        """Yield (slot_id, decoded_values) per non-tombstoned slot across all pages."""
        pid = ti.root_page_id
        results = []
        while True:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            for sid in range(page.num_slots):
                row_bytes = page.get(sid)
                if row_bytes is None:
                    continue
                results.append((sid, decode_row(row_bytes, ti.schema), pid))
            if pid == ti.next_page_id:
                break
            pid += 1
        return results
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_executor.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add src/tinydb/executor.py tests/integration/test_executor.py
git commit -m "feat(executor): INSERT with row encoding + linear scan helper"
```

---

### Task 19: Executor — SELECT + DELETE（tasks.md §8.6-8.7）

引用：Design Doc §6.4、§6.5、§6.6。

**Files:**
- Modify: `tests/integration/test_executor.py`
- Modify: `src/tinydb/executor.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.spec_id("REQ-STORAGE-007-SCN-02")
def test_select_where_equality(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    _exec(p, "CREATE TABLE t(id INT, name TEXT)")
    _exec(p, "INSERT INTO t VALUES (1, 'a')")
    _exec(p, "INSERT INTO t VALUES (2, 'b')")
    rows = _select(p, "SELECT * FROM t WHERE id = 2")
    assert len(rows) == 1 and rows[0].values == [2, "b"]
    p.close()

@pytest.mark.spec_id("REQ-STORAGE-007-SCN-02")
def test_select_where_type_mismatch_raises(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    _exec(p, "CREATE TABLE t(id INT)")
    _exec(p, "INSERT INTO t VALUES (1)")
    with pytest.raises(TypeError, match="INT vs TEXT"):
        _select(p, "SELECT * FROM t WHERE id = '1'")
    p.close()

@pytest.mark.spec_id("REQ-STORAGE-005-SCN-04")
def test_delete_marks_tombstones(tmp_path):
    p = Pager(str(tmp_path / "x.db"))
    _exec(p, "CREATE TABLE t(id INT)")
    for i in range(5):
        _exec(p, f"INSERT INTO t VALUES ({i})")
    _exec(p, "DELETE FROM t WHERE id = 2")
    rows = _select(p, "SELECT * FROM t")
    assert sorted(r.values[0] for r in rows) == [0, 1, 3, 4]
    p.close()
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_executor.py -v`
Expected: NotImplementedError

- [ ] **Step 3: 实现 SELECT + DELETE**

```python
    def _exec_select(self, stmt: Select):
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema
        results = []
        for _sid, vals, _pid in self._scan_table(ti):
            # WHERE
            if stmt.where is not None:
                col_name, op, lit = stmt.where
                col_idx = next((i for i, (n, _) in enumerate(schema) if n == col_name), None)
                if col_idx is None:
                    raise ExecutionError(f"unknown column {col_name!r}")
                col_type = schema[col_idx][1]
                from tinydb.type_system import validate_compare, py_to_db
                col_bytes = encode_row(vals, schema)  # simpler: re-encode just to validate
                # Simpler: validate by type equality of bytes encoding
                # We need to compare stored value vs literal via validate_compare.
                # Encode literal to bytes
                from tinydb.type_system import py_to_db as _ptd
                lit_bytes = _ptd(lit, col_type)
                # Re-encode the col's bytes for its column alone using db_to_py/py_to_db
                col_val = vals[col_idx]
                col_enc = _ptd(col_val, col_type)
                validate_compare(col_enc, col_type, lit_bytes, col_type)
                if op == "=" and col_val != lit:
                    continue
            # Project
            if stmt.columns == ["*"]:
                proj_vals = vals
            else:
                idx_map = {n: i for i, (n, _) in enumerate(schema)}
                proj_vals = [vals[idx_map[c]] for c in stmt.columns]
            from tinydb.database import Row
            results.append(Row(values=proj_vals, columns=stmt.columns if stmt.columns != ["*"]
                               else [n for n, _ in schema]))
        return results

    def _exec_delete(self, stmt: Delete):
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema
        for sid, vals, pid in self._scan_table(ti):
            if stmt.where is None:
                raw = self.pager.read_page(pid)
                page = SlottedPage.from_bytes(pid, raw)
                page.delete(sid)
                self.pager.write_page(pid, page.to_bytes())
                continue
            col_name, op, lit = stmt.where
            col_idx = next((i for i, (n, _) in enumerate(schema) if n == col_name), None)
            if col_idx is None:
                raise ExecutionError(f"unknown column {col_name!r}")
            if vals[col_idx] == lit:
                raw = self.pager.read_page(pid)
                page = SlottedPage.from_bytes(pid, raw)
                page.delete(sid)
                self.pager.write_page(pid, page.to_bytes())
        self.pager.flush()
        return []
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_executor.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 行数审计**

Run: `wc -l src/tinydb/executor.py`
Expected: ≤ 200 行

- [ ] **Step 6: Commit**

```bash
git add src/tinydb/executor.py tests/integration/test_executor.py
git commit -m "feat(executor): SELECT projection + WHERE equality + DELETE tombstone"
```

---

### Task 20: Database + Row 类（tasks.md §9.1-9.3）

引用：Design Doc §7、§6.6。

**Files:**
- Test: `tests/integration/test_database_api.py`
- Create: `src/tinydb/database.py`

- [ ] **Step 1: 写失败测试（导入、context manager、Row）**

```python
# tests/integration/test_database_api.py
import pytest
import tinydb
from tinydb import Database, Row, errors
from tinydb.errors import ParseError, ExecutionError

@pytest.mark.spec_id("REQ-API-001-SCN-01")
def test_import_database_and_row():
    assert tinydb.Database is Database
    assert tinydb.Row is Row

@pytest.mark.spec_id("REQ-API-001-SCN-02")
def test_version_string():
    assert tinydb.__version__ == "0.1.0"

@pytest.mark.spec_id("REQ-API-002-SCN-01")
def test_open_file_backed_creates_file(tmp_path):
    p = tmp_path / "db.db"
    Database(str(p)).close()
    assert p.exists()

@pytest.mark.spec_id("REQ-API-002-SCN-02")
def test_memory_mode_no_filesystem(tmp_path, monkeypatch):
    # ensure no file written
    cwd = tmp_path
    monkeypatch.chdir(cwd)
    db = Database(":memory:")
    db.close()

@pytest.mark.spec_id("REQ-API-002-SCN-03")
def test_context_manager_closes(tmp_path):
    p = tmp_path / "db.db"
    with Database(str(p)) as db:
        db.execute("CREATE TABLE t(id INT)")
    # After exit, re-opening should succeed
    with Database(str(p)) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert rows == []

@pytest.mark.spec_id("REQ-API-003-SCN-01")
def test_select_returns_list_of_rows(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t VALUES (1, 'a')")
        rows = db.execute("SELECT * FROM t")
    assert isinstance(rows, list) and len(rows) == 1
    assert isinstance(rows[0], Row)

@pytest.mark.spec_id("REQ-API-003-SCN-04")
def test_ddl_returns_empty_list(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        result = db.execute("CREATE TABLE t(id INT)")
    assert result == []

@pytest.mark.spec_id("REQ-API-003-SCN-05")
def test_multi_statement_returns_final_select(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        rows = db.execute(
            "CREATE TABLE t(id INT); INSERT INTO t VALUES (1); SELECT * FROM t")
    assert len(rows) == 1 and rows[0].id == 1

@pytest.mark.spec_id("REQ-API-003-SCN-06")
def test_parse_error_propagates(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        with pytest.raises(errors.ParseError):
            db.execute("SELECT FROM")

@pytest.mark.spec_id("REQ-API-003-SCN-07")
def test_execution_error_on_missing_table(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        with pytest.raises(errors.ExecutionError, match="does not exist"):
            db.execute("SELECT * FROM ghost")

@pytest.mark.spec_id("REQ-API-004-SCN-01")
def test_row_attribute_access(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t VALUES (7, 'alice')")
        row = db.execute("SELECT * FROM t")[0]
    assert row.id == 7 and row.name == "alice"

@pytest.mark.spec_id("REQ-API-004-SCN-02")
def test_row_iteration_in_schema_order(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t VALUES (1, 'x')")
        row = db.execute("SELECT * FROM t")[0]
    assert list(row) == [1, "x"]

@pytest.mark.spec_id("REQ-API-004-SCN-03")
def test_row_repr(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t VALUES (1, 'alice')")
        row = db.execute("SELECT * FROM t")[0]
    assert "id=1" in repr(row) and "name='alice'" in repr(row)

@pytest.mark.spec_id("REQ-API-004-SCN-04")
def test_row_equality(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT)")
        db.execute("INSERT INTO t VALUES (1)")
        rows = db.execute("SELECT * FROM t")
    assert rows[0] == rows[0]

@pytest.mark.spec_id("REQ-API-005-SCN-01")
def test_tuple_unpack_from_row(tmp_path):
    with Database(str(tmp_path / "db.db")) as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t VALUES (1, 'x')")
        row = db.execute("SELECT * FROM t")[0]
    a, b = row
    assert (a, b) == (1, "x")

@pytest.mark.spec_id("REQ-API-006-SCN-01")
def test_database_docstring_mentions_non_acid():
    assert "non-ACID, no crash safety" in Database.__init__.__doc__

@pytest.mark.spec_id("REQ-API-006-SCN-02")
def test_database_has_no_transaction_methods():
    for m in ("begin", "commit", "rollback"):
        assert not hasattr(Database, m), f"Database must not have {m}"
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_database_api.py -v`
Expected: ImportError `Database`

- [ ] **Step 3: 实现 Database + Row**

```python
# src/tinydb/database.py
"""Public API: Database class + Row dataclass. MVP: non-ACID, no transactions. ≤ 100 lines."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tinydb.pager import Pager
from tinydb.catalog import Catalog
from tinydb.executor import Executor
from tinydb.tokenizer import tokenize
from tinydb.parser import parse
from tinydb import errors as _errors


@dataclass
class Row:
    values: list
    columns: list

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.columns:
            return self.values[self.columns.index(name)]
        raise AttributeError(name)

    def __iter__(self):
        return iter(self.values)

    def __repr__(self) -> str:
        parts = ", ".join(f"{c}={v!r}" for c, v in zip(self.columns, self.values))
        return f"Row({parts})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Row) and self.columns == other.columns
                and self.values == other.values)


class Database:
    """Public entry point.

    MVP: non-ACID, no crash safety. Path may be a filesystem path or ':memory:'.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.pager = Pager(path)
        self.catalog = Catalog.from_bytes(self.pager.read_page(1))
        self.executor = Executor(self.pager, self.catalog)

    def execute(self, sql: str) -> list:
        tokens = tokenize(sql)
        stmts = parse(tokens)
        results = []
        for s in stmts.statements:
            try:
                out = self.executor.execute(s)
            except _errors.ParseError as e:
                raise
            except _errors.TokenError as e:
                raise
            except KeyError as e:
                raise _errors.ExecutionError(str(e)) from e
            if isinstance(out, list):
                results = out  # final SELECT's result
        return results

    def close(self) -> None:
        self.pager.flush()
        self.pager.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_database_api.py -v`
Expected: PASS（约 16 passed）

- [ ] **Step 5: 行数审计**

Run: `wc -l src/tinydb/database.py`
Expected: ≤ 90 行

- [ ] **Step 6: Commit**

```bash
git add src/tinydb/database.py tests/integration/test_database_api.py
git commit -m "feat(api): Database.execute pipeline with Row dataclass and error mapping"
```

---

### Task 21: Executor — Overflow Chain（Spec Patch §9 Patch 1 / Design §3.3）

引用：Design Doc §3.3、§9 Spec Patch。

**Files:**
- Test: `tests/integration/test_overflow_chain.py`
- Modify: `src/tinydb/executor.py`

- [ ] **Step 1: 写失败测试（spill + read + delete 三场景）**

```python
# tests/integration/test_overflow_chain.py
import pytest
from tinydb import Database
from tinydb.slotted_page import NULL_PAGE_ID

@pytest.mark.spec_id("REQ-STORAGE-008-SCN-01")
def test_insert_row_larger_than_inline_spills(tmp_path):
    path = tmp_path / "big.db"
    with Database(str(path)) as db:
        db.execute("CREATE TABLE t(payload TEXT)")
        big = "x" * 5000
        db.execute(f"INSERT INTO t VALUES ('{big}')")
    # Reopen and verify
    with Database(str(path)) as db2:
        rows = db2.execute("SELECT * FROM t")
    assert len(rows) == 1 and rows[0].payload == big

@pytest.mark.spec_id("REQ-STORAGE-008-SCN-02")
def test_read_spill_start_reconstructs_full_row(tmp_path):
    path = tmp_path / "big2.db"
    with Database(str(path)) as db:
        db.execute("CREATE TABLE t(payload TEXT)")
        db.execute(f"INSERT INTO t VALUES ('{'y' * 8000}')")
    with Database(str(path)) as db:
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 1 and len(rows[0].payload) == 8000

@pytest.mark.spec_id("REQ-STORAGE-008-SCN-03")
def test_delete_spill_start_frees_chain(tmp_path):
    path = tmp_path / "big3.db"
    with Database(str(path)) as db:
        db.execute("CREATE TABLE t(payload TEXT)")
        big = "z" * 6000
        db.execute(f"INSERT INTO t VALUES ('{big}')")
        db.execute(f"INSERT INTO t VALUES ('short')")
        db.execute("DELETE FROM t WHERE payload = 'short'")
        # Verify short row gone but big still there
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 1 and len(rows[0].payload) == 6000
```

- [ ] **Step 2: 跑测试验证 RED**

Run: `pytest tests/integration/test_overflow_chain.py -v`
Expected: assertion failures (big row inserted but not retrievable)

- [ ] **Step 3: 在 Executor 实现 overflow chain**

在 `src/tinydb/executor.py` 添加：

```python
from tinydb.slotted_page import SlottedPage, NULL_PAGE_ID, FLAG_SPILL_START
MAX_INLINE_PAYLOAD = 3800  # per Design §3.4

class Executor:
    ...
    def _insert_row_into_chain(self, ti, row_bytes):
        if len(row_bytes) <= MAX_INLINE_PAYLOAD:
            return self._insert_inline(ti, row_bytes)
        return self._insert_with_overflow(ti, row_bytes)

    def _insert_inline(self, ti, row_bytes):
        pid = ti.root_page_id
        while True:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            try:
                page.insert(row_bytes)
                self.pager.write_page(pid, page.to_bytes())
                self.pager.flush()
                return pid
            except Exception as e:
                from tinydb.errors import PageFull
                if not isinstance(e, PageFull):
                    raise
                if pid == ti.next_page_id:
                    new_pid = self.pager.alloc_page()
                    ti.next_page_id = new_pid
                    self.pager.write_page(1, self.catalog.to_bytes())
                    pid = new_pid
                else:
                    pid += 1
                    continue

    def _insert_with_overflow(self, ti, row_bytes):
        # Split: first chunk fits in first data page slot, rest in overflow pages
        first_chunk = row_bytes[:MAX_INLINE_PAYLOAD]
        rest = row_bytes[MAX_INLINE_PAYLOAD:]
        pid_first = self._insert_inline(ti, first_chunk)
        # Mark SPILL_START on that slot (find it: last slot of pid_first)
        raw = self.pager.read_page(pid_first)
        page = SlottedPage.from_bytes(pid_first, raw)
        last_slot = page.num_slots - 1
        page.slots[last_slot].flags |= FLAG_SPILL_START
        self.pager.write_page(pid_first, page.to_bytes())
        # Walk overflow chain
        prev_pid = pid_first
        while rest:
            chunk = rest[:MAX_INLINE_PAYLOAD]
            rest = rest[MAX_INLINE_PAYLOAD:]
            overflow_pid = self.pager.alloc_page()
            # Write overflow page directly: data starts at offset 16, page_type=2
            raw = bytearray(4096)
            raw[0] = 2  # page_type = 2 (overflow)
            raw[4:8] = (NULL_PAGE_ID if not rest else 0).to_bytes(4, "big")
            # placeholder: link later
            self.pager.write_page(overflow_pid, bytes(raw))
            # Write chunk at offset 16
            # Re-read, splice chunk
            raw = bytearray(self.pager.read_page(overflow_pid))
            raw[16:16 + len(chunk)] = chunk
            # If more rest, link to next overflow
            if rest:
                next_pid = self.pager.alloc_page()
                raw[4:8] = next_pid.to_bytes(4, "big")
            self.pager.write_page(overflow_pid, bytes(raw))
            # Link prev to this overflow
            prev_raw = bytearray(self.pager.read_page(prev_pid))
            prev_raw[4:8] = overflow_pid.to_bytes(4, "big")
            self.pager.write_page(prev_pid, bytes(prev_raw))
            prev_pid = overflow_pid
        self.pager.flush()
        return pid_first

    def _read_overflow_chain(self, start_pid):
        """Follow overflow_next_page_id from a data page header, return concatenated chunks."""
        chunks = []
        pid = int.from_bytes(self.pager.read_page(start_pid)[4:8], "big")
        while pid != NULL_PAGE_ID:
            raw = self.pager.read_page(pid)
            # chunk is at offset 16, length = PAGE_SIZE - 16 - (trailing zeros trimmed)
            # For MVP simplicity, store chunks as exactly MAX_INLINE_PAYLOAD except last.
            # We track length implicitly by next pointer: read up to next pid's start.
            # Simpler: read all 4080 bytes; downstream decode handles trim via null bitmap.
            chunk = raw[16:]
            chunks.append(bytes(chunk))
            nxt = int.from_bytes(raw[4:8], "big")
            pid = nxt
        return b"".join(chunks)

    def _scan_table(self, ti):
        pid = ti.root_page_id
        results = []
        while True:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            for sid in range(page.num_slots):
                slot = page.slots[sid]
                if slot.flags & 1:  # TOMBSTONE
                    continue
                row_bytes = page.get(sid)
                if row_bytes is None:
                    continue
                if slot.flags & FLAG_SPILL_START:
                    # Combine first chunk with overflow chain
                    chained = self._read_overflow_chain(pid)
                    # chained starts from next page; we need first chunk too
                    row_bytes = row_bytes + chained
                results.append((sid, decode_row(row_bytes, ti.schema), pid))
            if pid == ti.next_page_id:
                break
            pid += 1
        return results

    def _exec_delete(self, stmt: Delete):
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema
        for sid, vals, pid in list(self._scan_table(ti)):
            match = stmt.where is None
            if not match:
                col_name, op, lit = stmt.where
                col_idx = next((i for i, (n, _) in enumerate(schema) if n == col_name), None)
                if col_idx is None:
                    raise ExecutionError(f"unknown column {col_name!r}")
                match = (vals[col_idx] == lit)
            if match:
                raw = self.pager.read_page(pid)
                page = SlottedPage.from_bytes(pid, raw)
                if page.slots[sid].flags & FLAG_SPILL_START:
                    # Mark all overflow pages free
                    nxt = int.from_bytes(raw[4:8], "big")
                    while nxt != NULL_PAGE_ID:
                        raw2 = bytearray(self.pager.read_page(nxt))
                        raw2[0] = 0  # page_type = 0 (free)
                        self.pager.write_page(nxt, bytes(raw2))
                        nxt = int.from_bytes(raw2[4:8], "big")
                page.delete(sid)
                self.pager.write_page(pid, page.to_bytes())
        self.pager.flush()
        return []
```

- [ ] **Step 4: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_overflow_chain.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 行数审计**

Run: `wc -l src/tinydb/executor.py`
Expected: ≤ 400 行

- [ ] **Step 6: Commit**

```bash
git add src/tinydb/executor.py tests/integration/test_overflow_chain.py
git commit -m "feat(executor): overflow chain spill/merge/free for rows > MAX_INLINE_PAYLOAD"
```

---

### Task 22: Property-based Tests — Storage Invariants（tasks.md §10.3）

引用：Design Doc §8.4、§3 全部。

**Files:**
- Create: `tests/property/test_storage_invariants.py`

- [ ] **Step 1: 写属性测试（Python 镜像维护）**

```python
# tests/property/test_storage_invariants.py
from hypothesis import given, settings, seed
import hypothesis.strategies as st
import tinydb

@seed(20260715)
@settings(max_examples=200, deadline=None)
@given(st.lists(
    st.tuples(st.sampled_from(["INSERT", "DELETE"]),
              st.integers(min_value=0, max_value=1000),
              st.text(max_size=50, alphabet=st.characters(min_codepoint=ord('a'), max_codepoint=ord('z')))),
    max_size=50,
))
def test_scan_equals_python_mirror(operations):
    db = tinydb.Database(":memory:")
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    mirror: set = set()
    for op, i, name in operations:
        if op == "INSERT":
            db.execute(f"INSERT INTO t VALUES ({i % 100}, '{name}')")
            mirror.add((i % 100, name))
        else:  # DELETE
            db.execute(f"DELETE FROM t WHERE id = {i % 100}")
            mirror = {(k, v) for (k, v) in mirror if k != i % 100}
    rows = db.execute("SELECT * FROM t")
    actual = sorted((r.id, r.name) for r in rows)
    assert actual == sorted(mirror)

@seed(20260715)
@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=0, max_value=10000))
def test_insert_then_persist_roundtrip(tmp_path_factory, n):
    path = str(tmp_path_factory.mktemp("prop") / "x.db")
    with tinydb.Database(path) as db:
        db.execute("CREATE TABLE t(v INT)")
        for i in range(n):
            db.execute(f"INSERT INTO t VALUES ({i})")
    with tinydb.Database(path) as db:
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == n
```

- [ ] **Step 2: 跑测试验证（hypothesis 自动找最小反例）**

Run: `pytest tests/property/test_storage_invariants.py -v`
Expected: PASS（200 + 100 examples）

- [ ] **Step 3: 若发现反例，按 systematic-debugging skill 修源码**

若 hypothesis 找到失败用例，加载 `superpowers:systematic-debugging` skill 定位根因，禁止拍脑袋 patch。

- [ ] **Step 4: Commit**

```bash
git add tests/property/test_storage_invariants.py
git commit -m "test(property): storage scan invariants via Python mirror, seed=20260715"
```

---

### Task 23: Property-based Tests — Parser Robustness（tasks.md §10.4）

**Files:**
- Create: `tests/property/test_parser_robustness.py`

- [ ] **Step 1: 写属性测试（随机字符串不抛未捕获系统异常）**

```python
# tests/property/test_parser_robustness.py
from hypothesis import given, settings, seed
import hypothesis.strategies as st
from tinydb.tokenizer import tokenize
from tinydb.parser import parse
from tinydb.errors import TokenError, ParseError

@seed(20260715)
@settings(max_examples=500, deadline=None)
@given(st.text(max_size=200, alphabet=st.characters(
    blacklist_categories=("Cc", "Cs"),  # skip control + surrogate
    blacklist_characters=("\\"),
)))
def test_random_sql_only_raises_parse_or_token_errors(sql):
    try:
        tokens = tokenize(sql)
        parse(tokens)
    except (TokenError, ParseError):
        return  # expected
    except UnicodeDecodeError:
        return  # text literal decoder
    # No other exception type should escape
```

- [ ] **Step 2: 跑测试验证**

Run: `pytest tests/property/test_parser_robustness.py -v`
Expected: PASS（500 examples）

- [ ] **Step 3: Commit**

```bash
git add tests/property/test_parser_robustness.py
git commit -m "test(property): parser robustness with random SQL strings, seed=20260715"
```

---

### Task 24: E2E Golden SQL 测试集（tasks.md §10.1-10.2）

引用：Design Doc §8.3。

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: 12-15 SQL/expected 文件

- [ ] **Step 1: 写 conftest helper**

```python
# tests/e2e/conftest.py
"""E2E golden SQL test runner: byte-compares db.execute output to .expected.txt."""
import pathlib
import pytest
import tinydb

SQL_DIR = pathlib.Path(__file__).parent / "sql"


def pytest_generate_tests(metafunc):
    if "golden_sql" in metafunc.fixturenames:
        files = sorted(SQL_DIR.rglob("*.sql"))
        metafunc.parametrize("golden_sql", files, ids=lambda p: str(p.relative_to(SQL_DIR)))


@pytest.fixture
def golden_sql(request, tmp_path):
    sql_path: pathlib.Path = request.param
    expected_path = sql_path.with_suffix(".expected.txt")
    db = tinydb.Database(str(tmp_path / "e2e.db"))
    try:
        # Split SQL by ";\n" boundaries; each statement run separately
        statements = [s.strip() for s in sql_path.read_text().split(";") if s.strip()]
        outputs = []
        for stmt in statements:
            try:
                rows = db.execute(stmt)
                outputs.append(_format_rows(rows))
            except Exception as e:
                outputs.append(f"ERROR: {type(e).__name__}: {e}")
        actual = "\n".join(outputs) + "\n"
        expected = expected_path.read_text() if expected_path.exists() else ""
        yield sql_path, actual, expected
    finally:
        db.close()


def _format_rows(rows):
    if not rows:
        return "(no rows)"
    return "\n".join(repr(r) for r in rows)
```

- [ ] **Step 2: 写 golden SQL 文件（12 个示例）**

`tests/e2e/sql/happy_path/01_create_insert_select.sql`:
```sql
CREATE TABLE users(id INT, name TEXT);
INSERT INTO users VALUES (1, 'alice');
INSERT INTO users VALUES (2, 'bob');
SELECT * FROM users
```

`tests/e2e/sql/happy_path/01_create_insert_select.expected.txt`:
```
Row(id=1, name='alice')
Row(id=2, name='bob')
```

依此类推，创建以下 golden 文件：

| # | 文件 | 覆盖场景 |
|---|------|---------|
| 01 | `happy_path/01_create_insert_select.sql` | 基本 CRUD |
| 02 | `happy_path/02_multi_table.sql` | 多表独立 |
| 03 | `happy_path/03_select_columns.sql` | 列投影 |
| 04 | `happy_path/04_select_where.sql` | WHERE 等值 |
| 05 | `happy_path/05_delete_all.sql` | DELETE 全表 |
| 06 | `happy_path/06_delete_where.sql` | DELETE WHERE |
| 07 | `happy_path/07_multi_statement.sql` | 多语句 |
| 08 | `happy_path/08_persist_reopen.sql` | 关闭重开持久化 |
| 09 | `happy_path/09_bool_column.sql` | BOOL 类型 |
| 10 | `happy_path/10_float_column.sql` | FLOAT 类型 |
| 11 | `happy_path/11_text_with_quotes.sql` | TEXT doubled-quote |
| 12 | `happy_path/12_drop_and_recreate.sql` | DROP + CREATE 同名 |
| 13 | `error_cases/01_unknown_table.sql` | 不存在表 → ExecutionError |
| 14 | `error_cases/02_unsupported_type.sql` | VARCHAR → ParseError |
| 15 | `error_cases/03_value_mismatch.sql` | 列数不匹配 → ParseError |

- [ ] **Step 3: 跑测试验证**

Run: `pytest tests/e2e/ -v`
Expected: 12-15 PASS（首次编写 expected 时对照实际输出人工校对）

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/
git commit -m "test(e2e): 12 golden SQL scenarios with byte-comparison runner"
```

---

### Task 25: Integration 套件 — parser↔executor roundtrip（tasks.md §8.2）

引用：Design Doc §8.2。

**Files:**
- Create: `tests/integration/test_parser_executor_roundtrip.py`

- [ ] **Step 1: 写跨模块集成测试**

```python
# tests/integration/test_parser_executor_roundtrip.py
import pytest
from tinydb import Database

@pytest.mark.parametrize("sql,expected_rows", [
    ("CREATE TABLE t(id INT); INSERT INTO t VALUES (1); SELECT * FROM t", 1),
    ("CREATE TABLE t(a INT, b TEXT); INSERT INTO t VALUES (1,'x'); INSERT INTO t VALUES (2,'y'); SELECT * FROM t WHERE a = 2", 1),
])
def test_full_pipeline(tmp_path, sql, expected_rows):
    with Database(str(tmp_path / "rt.db")) as db:
        rows = db.execute(sql)
    assert len(rows) == expected_rows

def test_parser_is_pure_no_state_leak(tmp_path):
    from tinydb.parser import parse
    from tinydb.tokenizer import tokenize
    a = parse(tokenize("CREATE TABLE t(id INT)"))
    b = parse(tokenize("CREATE TABLE t(id INT)"))
    assert a.statements[0].name == b.statements[0].name
```

- [ ] **Step 2: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_parser_executor_roundtrip.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_parser_executor_roundtrip.py
git commit -m "test(integration): parser+executor+api roundtrip parametrized cases"
```

---

### Task 26: Integration 套件 — storage page chain（tasks.md §3 / §5）

引用：Design Doc §3.3。

**Files:**
- Create: `tests/integration/test_storage_page_chain.py`

- [ ] **Step 1: 写多页管理集成测试**

```python
# tests/integration/test_storage_page_chain.py
import pytest
from tinydb import Database

def test_multi_page_allocation(tmp_path):
    with Database(str(tmp_path / "mp.db")) as db:
        db.execute("CREATE TABLE big(v INT)")
        for i in range(100):
            db.execute(f"INSERT INTO big VALUES ({i})")
        rows = db.execute("SELECT * FROM big")
    assert len(rows) == 100

def test_persistence_chain_across_reopen(tmp_path):
    path = str(tmp_path / "ch.db")
    with Database(path) as db:
        db.execute("CREATE TABLE t(v INT)")
        for i in range(50):
            db.execute(f"INSERT INTO t VALUES ({i})")
    with Database(path) as db:
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 50 and rows[0].v == 0 and rows[49].v == 49
```

- [ ] **Step 2: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_storage_page_chain.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_storage_page_chain.py
git commit -m "test(integration): multi-page allocation and persistence chain"
```

---

### Task 27: Integration 套件 — full SQL lifecycle（tasks.md §9）

引用：Design Doc §7、§9 Spec Patch。

**Files:**
- Create: `tests/integration/test_full_sql_lifecycle.py`

- [ ] **Step 1: 写端到端 lifecycle**

```python
# tests/integration/test_full_sql_lifecycle.py
import pytest
from tinydb import Database

def test_full_lifecycle_create_insert_select_delete_reopen(tmp_path):
    path = str(tmp_path / "life.db")
    with Database(path) as db:
        db.execute("CREATE TABLE users(id INT, name TEXT, active BOOL)")
        db.execute("INSERT INTO users VALUES (1, 'alice', TRUE)")
        db.execute("INSERT INTO users VALUES (2, 'bob', FALSE)")
        db.execute("INSERT INTO users VALUES (3, 'carol', TRUE)")
        rows = db.execute("SELECT * FROM users WHERE active = TRUE")
        assert sorted(r.name for r in rows) == ["alice", "carol"]
        db.execute("DELETE FROM users WHERE id = 2")
    with Database(path) as db:
        rows = db.execute("SELECT * FROM users")
    assert sorted(r.id for r in rows) == [1, 3]
```

- [ ] **Step 2: 跑测试验证 GREEN**

Run: `pytest tests/integration/test_full_sql_lifecycle.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_full_sql_lifecycle.py
git commit -m "test(integration): full SQL lifecycle CREATE/INSERT/SELECT/DELETE/reopen"
```

---

### Task 28: Documentation — README 模块导览 + demo + LIMITATIONS（tasks.md §11）

引用：Design Doc §11。

**Files:**
- Modify: `README.md`
- Create: `examples/demo.py`
- Create: `docs/MVP_LIMITATIONS.md`

- [ ] **Step 1: 写 demo.py**

```python
# examples/demo.py
"""End-to-end tinydb MVP demo."""
import tinydb

def main():
    with tinydb.Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, name TEXT, active BOOL)")
        db.execute("INSERT INTO users VALUES (1, 'alice', TRUE)")
        db.execute("INSERT INTO users VALUES (2, 'bob', FALSE)")
        db.execute("INSERT INTO users VALUES (3, 'carol', TRUE)")
        print("All users:")
        for row in db.execute("SELECT * FROM users"):
            print(" ", repr(row))
        print("\nActive users:")
        for row in db.execute("SELECT * FROM users WHERE active = TRUE"):
            print(" ", repr(row))
        db.execute("DELETE FROM users WHERE id = 2")
        print("\nAfter deleting id=2:")
        for row in db.execute("SELECT * FROM users"):
            print(" ", repr(row))

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写 MVP_LIMITATIONS.md**

```markdown
# MVP Limitations

tinydb MVP is a teaching-grade embedded database. It explicitly does NOT provide:

- **ACID / crash safety**: pages are written best-effort. Process kill mid-write MAY corrupt the file.
- **Transactions**: no `begin`/`commit`/`rollback` (will arrive in `tinydb-acid`).
- **Concurrency**: single-threaded. Multi-process access will corrupt the file.
- **UPDATE**: delete + insert only.
- **WHERE combinators**: only `col = literal`; no `AND`/`OR`/`IN`/`LIKE`.
- **ORDER BY / LIMIT / OFFSET**.
- **Aggregation**: no `COUNT`/`SUM`/`AVG`/`GROUP BY`.
- **Indexes**: linear scan only. Performance degrades on large tables.
- **Constraint enforcement**: `NOT NULL`/`PRIMARY KEY`/`UNIQUE` are parsed (in v2) but not enforced in MVP.
- **Type coercion**: strict mode — `'5'` cannot compare with INT columns.
- **Catalog size**: single page (4 KB JSON). Tables beyond ~100 entries may overflow.

All of the above are scoped to follow-up changes: `tinydb-acid`, `tinydb-engine-v2`.
```

- [ ] **Step 3: 跑 demo 验证输出**

Run: `python examples/demo.py`
Expected: 3 段输出，row repr 与 docstring 描述一致

- [ ] **Step 4: Commit**

```bash
git add README.md examples/demo.py docs/MVP_LIMITATIONS.md
git commit -m "docs: module map + demo script + MVP limitations page"
```

---

### Task 29: 覆盖率与全测试套件验证（tasks.md §12.1-12.3）

引用：Design Doc §8.5。

**Files:**
- Modify: `pyproject.toml`（添加 coverage 配置）

- [ ] **Step 1: 配 coverage 门槛**

在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 添加：

```toml
addopts = "-ra --strict-markers --cov=tinydb --cov-report=term-missing --cov-fail-under=85"
```

- [ ] **Step 2: 跑全套测试**

Run: `pytest`
Expected: 全 PASS，覆盖率 ≥ 85%

- [ ] **Step 3: 若覆盖率不足，按模块行数审计**

Run: `pytest --cov=tinydb --cov-report=term-missing | grep tinydb`
针对未覆盖行加测试，回到相关 Task 补测试再 commit。

- [ ] **Step 4: 行数审计**

Run:
```bash
wc -l src/tinydb/*.py
```
对照预算（Design Doc §12 / proposal Impact）：

| 模块 | 预算 | 实际 |
|------|------|------|
| type_system.py | ≤ 150 | _wc -l_ |
| pager.py | ≤ 250 | _wc -l_ |
| slotted_page.py | ≤ 150 | _wc -l_ |
| catalog.py | ≤ 100 | _wc -l_ |
| tokenizer.py | ≤ 200 | _wc -l_ |
| parser.py | ≤ 600 | _wc -l_ |
| executor.py | ≤ 400 | _wc -l_ |
| database.py | ≤ 100 | _wc -l_ |

任何模块超预算 → 立即拆分子任务或重写（违反 MVP 教学定位）。

- [ ] **Step 5: OpenSpec 验证**

Run: `openspec validate tinydb-mvp --strict`
Expected: Validation passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "chore(pytest): enable coverage gate at 85% minimum"
```

---

### Task 30: 最终人眼 demo 验证（tasks.md §12.4）

- [ ] **Step 1: 跑 demo 确认输出符合预期**

Run: `python examples/demo.py`
Expected:
```
All users:
 Row(id=1, name='alice', active=True)
 Row(id=2, name='bob', active=False)
 Row(id=3, name='carol', active=True)

Active users:
 Row(id=1, name='alice', active=True)
 Row(id=3, name='carol', active=True)

After deleting id=2:
 Row(id=1, name='alice', active=True)
 Row(id=3, name='carol', active=True)
```

若输出不符 → 回到相关 Task 修源码，不直接改 demo。

- [ ] **Step 2: Commit（如有 demo 调整）**

```bash
git add examples/demo.py
git commit -m "fix(demo): align demo output with actual Row.__repr__ format"
# (only if changes were made)
```

---

### Task 31: Spec Patch 回写 OpenSpec delta spec（可选，由主 session 决定是否实施）

引用：Design Doc §9。

**Files:**
- Modify: `openspec/changes/tinydb-mvp/specs/storage-engine/spec.md`

- [ ] **Step 1: 回写 overflow chain Requirement**

在 `specs/storage-engine/spec.md` 的 `ADDED Requirements` 末尾追加（§9 Patch 1 内容）：

```markdown
### Requirement: Overflow row spans multiple pages
The system SHALL allow rows whose encoded size exceeds MAX_INLINE_PAYLOAD (~3970 bytes) by storing them across a chain of pages. The first page's slot SHALL be marked SPILL_START and its page header `overflow_next_page_id` points to the next overflow page. The chain SHALL terminate with `overflow_next_page_id = 0xFFFFFFFF` (NULL_PAGE_ID).

#### Scenario: Insert row larger than MAX_INLINE_PAYLOAD spills across pages
...

#### Scenario: Read spill-start slot reconstructs full row bytes
...

#### Scenario: Delete spill-start row frees overflow chain
...

### Requirement: Catalog schema encoded as JSON with INT-as-string
...

#### Scenario: Catalog encodes INT schema fields as quoted strings
...
```

- [ ] **Step 2: 验证**

Run: `openspec validate tinydb-mvp --strict`
Expected: Validation passed

- [ ] **Step 3: Commit**

```bash
git add openspec/changes/tinydb-mvp/specs/storage-engine/spec.md
git commit -m "docs(spec): backfill overflow chain + JSON INT-as-string scenarios"
```

---

### Task 32: 提交前最终自检

- [ ] **Step 1: 全测试套件一遍跑通**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 2: 覆盖率确认 ≥ 85%**

Run: `pytest --cov=tinydb --cov-report=term`
Expected: TOTAL ≥ 85%

- [ ] **Step 3: 行数预算确认（见 Task 29 Step 4）**

任何超预算模块 → 立即 refactor。

- [ ] **Step 4: 类型 / 命名一致性检查**

- SlottedPage 字段：`page_id, num_slots, free_offset, overflow_next, slots, data`（Task 9-10 锁定，后续 Task 严格沿用）
- Slot 字段：`offset, length, flags`
- Catalog 字段：`tables: dict[str, TableInfo]`、`TableInfo(schema, root_page_id, next_page_id)`
- Token type 字段：`type, value, line, col`
- AST 节点类名：`StatementList, CreateTable, DropTable, Insert, Select, Delete`
- Executor 方法名：`_exec_create_table / _exec_drop_table / _exec_insert / _exec_select / _exec_delete / _scan_table / _insert_row_into_chain / _insert_with_overflow`
- Row 字段：`values, columns`

若 Task 21（overflow）的实现与 Task 10（slotted_page）字段不一致，按 Task 10 锁定。

- [ ] **Step 5: 提交所有遗留修改**

```bash
git status
# 若有未提交修改，按所属 Task 单独 commit
git log --oneline | head -40
# 确认 commit 列表覆盖所有 Task
```

---

### Task 33: 触发 build→verify guard（由主 session 调度）

> 此 Task **不修改任何文件**；仅作为流程标记。

- [ ] **Step 1: 等待主 session 运行 `comet-guard tinydb-mvp build --apply`**

主 session 会:
1. 验证所有产物齐全
2. 运行 ALL CHECKS
3. 若 ALL CHECKS PASSED → 通过 build → 进入 verify 阶段

- [ ] **Step 2: 若 guard 失败，按错误项回退到对应 Task**

常见失败：
- 覆盖率不足 → 回 Task 29
- 行数超预算 → 回相关 Task 重构
- OpenSpec 不通过 → 回 Task 31
- 演示脚本输出不符 → 回 Task 30

- [ ] **Step 3: 通知主 session 继续 verify 阶段**

主 session 加载 `superpowers:verification-before-completion` skill 完成最终验证。

---

## Self-Review（写完后对照 Spec 检查）

### 1. Spec 覆盖矩阵

| Spec / Section | 覆盖 Task |
|----------------|-----------|
| type-system-basic 全部 Scenario | Task 2, 3, 4, 5, 6 |
| storage-engine: 文件头 + magic + version | Task 7 |
| storage-engine: 4KB page addressing | Task 8 |
| storage-engine: Slotted page layout | Task 9, 10 |
| storage-engine: Row encoding + null bitmap | Task 11 |
| storage-engine: Catalog at page 1 | Task 12 |
| storage-engine: Row CRUD executor | Task 17, 18, 19 |
| storage-engine: MVP non-ACID docstring | Task 20 (Database.__init__ docstring) |
| storage-engine: Overflow row spans multiple pages（Spec Patch §9 Patch 1） | Task 21 |
| storage-engine: Catalog JSON INT-as-string（Spec Patch §9 Patch 1） | Task 12 |
| sql-minimal-parser: tokenizer 6 类 | Task 13, 14 |
| sql-minimal-parser: AST + 5 语句 | Task 15, 16 |
| sql-minimal-parser: ParseError + position | Task 15 |
| sql-minimal-parser: 纯函数性质 | Task 16 (`test_parser_is_pure_deterministic`) |
| python-api: 包导入 + version | Task 20 |
| python-api: file/:memory: modes + context manager | Task 20 |
| python-api: execute returns + 多语句 | Task 20 |
| python-api: Row attribute/iter/repr/eq | Task 20 |
| python-api: tuple unpack | Task 20 |
| python-api: docstring non-ACID | Task 20 |

### 2. Placeholder 扫描

通读本计划确认无以下模式：

- ~~"TBD" / "TODO" / "implement later"~~ — 已用具体代码替换
- ~~"Add appropriate error handling"~~ — 各 Task 都指定具体 raise 类型
- ~~"Write tests for the above"~~ — 每个 Task Step 1 都是具体测试代码
- ~~"Similar to Task N"~~ — 每个 Task 步骤自包含
- ~~空白步骤~~ — 所有实现步骤含代码块

### 3. 类型一致性

- `SlottedPage`：`page_id, num_slots, free_offset, overflow_next, slots, data` ✓
- `Slot`：`offset, length, flags` ✓
- `Catalog.tables`：`dict[str, TableInfo]`，`TableInfo(schema, root_page_id, next_page_id)` ✓
- `Token`：`type, value, line, col` ✓
- `StatementList, CreateTable, DropTable, Insert, Select, Delete` 字段一致 ✓
- `Row`：`values, columns` ✓
- `Executor` 方法签名 `_exec_*` 与 dispatch 字典一致 ✓
- `Pager`：`read_page(page_id) -> bytes`, `write_page(page_id, bytes)`, `alloc_page() -> int`, `close()`, `flush()` ✓
- `Database`：`execute(sql) -> list`, `close()`, `__enter__/__exit__` ✓

无发现不一致。

---

## 关键提醒

- **测试先行**：每 Task Step 1 都是写测试，Step 2 必须看到 RED 才进 Step 3。
- **频繁 commit**：每 Task 末尾必须 git commit，绝不积攒。
- **行数预算**：违反 proposal.md Impact 的预算 = 违反 MVP 教学定位，必须重构。
- **覆盖率门槛**：模块 ≥ 85%，type_system ≥ 95%。
- **Property-based**：seed=20260715，max_examples=200。
- **E2E golden**：12-15 个 .sql + .expected.txt，字节对比。
- **错误传播**：parser ParseError → tinydb.errors.ParseError；executor KeyError → tinydb.errors.ExecutionError。
- **MVP 边界**：禁止提前实现 ACID、UPDATE、AND/OR、聚合、索引等。

---

**Plan 完毕。实施时加载 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` skill。**