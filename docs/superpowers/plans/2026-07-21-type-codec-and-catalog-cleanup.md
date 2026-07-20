---
change: type-codec-and-catalog-cleanup
design-doc: docs/superpowers/specs/2026-07-21-type-codec-and-catalog-cleanup-design.md
base-ref: 54874de47807e1473f0a06b5ab761eefe726a145
---

# type-codec-and-catalog-cleanup 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (推荐) 或 `superpowers:executing-plans` 按 task 执行本计划。每个步骤使用 checkbox (`- [ ]`) 跟踪；每个 task 完成后只产生一个 commit。

**Goal:** 删除 `type_system.py` 的旧模块级转换 API 与 `catalog.py` 的 v1 双格式兼容分支，使 codec registry 和 Column v2 object format 成为唯一代码路径，同时同步测试、fixture、规范与验证记录。

**Architecture:** 保留 `codec_for(type, params)`、各 `TypeCodec` 实现、`validate_compare_types` 和 literal parser，不改 codec registry 的编码语义。Catalog 只从 `Column.to_dict()` 生成/读取 schema，`create_table` 只接收 `tuple[Column, ...]`；所有仓库内测试调用点直接迁移到这两个 canonical 接口，不增加 wrapper 或 deprecation 层。

**Tech Stack:** Python 3.11+ 标准库、pytest、pytest-cov、pyflakes、OpenSpec/Comet artifacts。

**设计依据：**
- §1 Context：H6/H7 是已经没有生产调用方的双轨代码，旧 API 只剩测试使用。
- §3 Decisions：D1 直接删除、D2 以 codec registry 改写规范、D3 单点替换调用方、D4 删除失效 fixture、D5 使用 feature branch。
- §4 Architecture：type_system.py、catalog.py、row_codec.py 的精确删除边界和错误信息。
- §5 Test & Fixture Changes：7 个测试文件的同步范围、整文件/测试用例/fixture 删除清单。
- §7 Migration & Risks：v2 数据继续兼容，v1 数组 schema 将明确失败；不提供 in-process migration 或 compat shim。
- §9 Verification Checklist：pytest、pyflakes、grep、coverage、verify report 和 archive 要求。

## 文件地图

| 文件 | 操作 | 责任 |
|---|---|---|
| `src/tinydb/type_system.py` | 修改 | 删除 11 个旧 helper 及其专用常量/说明，保留 registry、`codec_for`、`lookup`、`infer_literal_type`、`validate_compare_types`、`CodecError`、`parse_*_literal`。 |
| `src/tinydb/catalog.py` | 修改 | 让 `_load_column` 只接受 dict，删除 mixed-format 检查和 `create_table` 的 list-form 分支，更新 `Column` docstring。 |
| `src/tinydb/row_codec.py` | 修改 | 把旧 `py_to_db` 文档引用改为 `codec_for(...).validate(...)`。 |
| `tests/unit/test_type_system.py` | 删除 | 旧 11-helper API 的整套测试，不再保留与删除 API 绑定的测试。 |
| `tests/unit/test_aggregation_executor.py` | 修改 | `py_to_db(123, "TEXT")` 改为 `codec_for("TEXT").validate(123)`。 |
| `tests/unit/test_validate_compare_types.py` | 修改 | 清除“legacy `validate_compare` 仍保留”的过时模块说明；保留现代 API 测试。 |
| `tests/unit/test_engine_v1_executor.py` | 修改 | 清除通过 `py_to_db` 解释类型错误的过时模块说明；保留 executor 行为测试。 |
| `tests/integration/test_catalog.py` | 修改 | 所有 `create_table` 调用传入 `tuple[Column, ...]`。 |
| `tests/integration/test_catalog_overflow.py` | 修改 | 3 个 overflow catalog 构造改为 Column tuple。 |
| `tests/integration/test_catalog_constraints.py` | 修改 | 删除 3 个 v1/mixed-format 回归测试及无用 import，保留 v2 constraints 测试。 |
| `tests/fixtures/legacy_mvp_schema.json` | 删除 | 不再守护被删除的 `[name, type]` v1 schema loader。 |
| `tests/fixtures/mixed_invalid_schema.json` | 删除 | 不再守护已删除的 mixed-format 检查。 |
| `openspec/specs/type-system-basic/spec.md` | 修改 | 将 conversion requirement 改为 codec registry contract。 |
| `docs/superpowers/reports/2026-07-21-type-codec-and-catalog-cleanup-verify.md` | 创建 | 记录最终命令、结果、coverage 和迁移风险。 |
| `openspec/changes/type-codec-and-catalog-cleanup/` | 归档 | 验证通过后由 archive 流程合并 delta 并移动到 dated archive 目录。 |

实现从 `main@54874de47807e1473f0a06b5ab761eefe726a145` 开始。不要把预先存在的 Design Doc 或 OpenSpec artifacts 当作实现改动；它们只在 Task 4/5 的规范和归档步骤中使用。

---

### Task 1: H6 — 删除 type_system.py 双轨旧 API

**Files:**
- Modify: `src/tinydb/type_system.py:1-160` 及 registry 前的注释
- Modify: `src/tinydb/row_codec.py:27-29`
- Test (验证，不修改): `tests/unit/test_type_system_registry.py`, `tests/unit/test_type_system_v2.py`, `tests/unit/test_validate_compare_types.py`

- [x] **Step 1: 建立 H6 删除边界并确认生产代码只使用 canonical API**

运行：

```bash
grep -RInE 'encode_int|encode_text|encode_bool|encode_float|decode_int|decode_text|decode_bool|decode_float|py_to_db|db_to_py|validate_compare\b' src/tinydb
```

Expected：输出只来自 `src/tinydb/type_system.py` 的待删定义/说明和 `src/tinydb/row_codec.py` 的待改 docstring；不得修改 `codec_for`、各 `_...Codec` 类、`validate_compare_types` 或 `parse_*_literal`。

- [x] **Step 2: 删除 type_system.py 的 11 个旧函数和专用常量**

删除以下完整符号及其函数体：`encode_int`、`decode_int`、`encode_text`、`decode_text`、`encode_bool`、`decode_bool`、`encode_float`、`decode_float`、`py_to_db`、`db_to_py`、`validate_compare`。同时删除仅被这些函数使用的 `_INT_FMT`、`_INT_SIZE`、`_FLOAT_FMT`；`struct`、`math` 等仍被 registry/codecs 使用的 import 必须保留。

文件开头的说明改为只描述 canonical registry，不再宣称两条路径共存：

```python
"""Type system: codecs for INT/TEXT/FLOAT/BOOL/DECIMAL/DATE/TIME/TIMESTAMP/VARCHAR/CHAR/etc.

The codec registry is the canonical type contract. Every codec exposes
``encode_py``/``decode_bytes``/``validate`` and is selected with
:func:`codec_for`. Parametric types are instantiated per call by
:func:`codec_for`.
"""
```

删除旧函数后，`parse_bool_literal` 后面应直接进入现有的 `_format_type_params` 与 `validate_compare_types`，例如：

```python
def parse_bool_literal(s: str) -> bool:
    u = s.upper()
    if u == "TRUE":
        return True
    if u == "FALSE":
        return False
    raise ValueError(f"invalid bool literal: {s!r}")


def _format_type_params(type_name: str, params: tuple) -> str:
    """Render ``'VARCHAR(10,) [5]'`` style suffix; empty when params empty."""
    if not params:
        return ""
    return f"{list(params)}"
```

不得改写或删除 `validate_compare_types`；它不是待删的旧 `validate_compare`。

- [x] **Step 3: 清理 registry 前后的过时注释**

将 `type_system.py` 中类似“legacy helpers above stay for backward compatibility”的注释替换为明确的 canonical registry 说明：

```python
# TypeCodec registry; parametric codecs are stored as classes and instantiated
# by codec_for().

# Codec implementations. FLOAT is 4-byte single precision; integer width
# selects SMALLINT/INT/BIGINT.
```

保留 `TypeCodec` Protocol、`REGISTRY`、`_ALIAS_MAP`、`lookup`、`codec_for` 和所有现有 codec implementation，不因为删 helper 而重构 registry。

- [x] **Step 4: 更新 row_codec.py 的调用方文档**

把 `encode_row` docstring 中的旧说明：

```python
Callers SHOULD pre-validate types via type_system.py_to_db for strict
 type checking (e.g., reject bool-as-INT, NaN/Inf FLOAT). This module
 performs mechanical encoding only.
```

改为：

```python
Callers SHOULD pre-validate types via
``codec_for(type, params).validate(value)`` for strict type checking
(e.g., reject bool-as-INT, NaN/Inf FLOAT). This module performs mechanical
encoding only.
```

`encode_row` 本身继续使用现有的 `codec_for(typ, params).encode_py(val)`，不要加入 wrapper。

- [x] **Step 5: 运行 H6 目标验证**

```bash
.venv/bin/python -m py_compile src/tinydb/type_system.py src/tinydb/row_codec.py
.venv/bin/python -m pytest tests/unit/test_type_system_registry.py tests/unit/test_type_system_v2.py tests/unit/test_validate_compare_types.py -q
.venv/bin/python - <<'PY'
from tinydb.type_system import CodecError, codec_for

try:
    from tinydb.type_system import py_to_db
except ImportError:
    pass
else:
    raise AssertionError("py_to_db must be removed")

try:
    codec_for("TEXT").validate(123)
except CodecError:
    pass
else:
    raise AssertionError("TEXT codec must reject an integer")
PY
```

Expected：现代 registry/compare 测试全绿；ImportError 检查通过；`codec_for("TEXT").validate(123)` 抛出 `CodecError`。此时旧 `tests/unit/test_type_system.py` 尚未在本 task 删除，完整套件暂不作为本 task 的通过条件。

- [x] **Step 6: 提交 Task 1**

```bash
git add src/tinydb/type_system.py src/tinydb/row_codec.py
git commit -m "refactor(types): remove legacy conversion helpers"
```

---

### Task 2: H7 — 删除 catalog.py 双序列化兼容路径

**Files:**
- Modify: `src/tinydb/catalog.py:27-175`
- Test (验证，不修改): `tests/integration/test_catalog.py`, `tests/integration/test_catalog_overflow.py`, `tests/integration/test_catalog_constraints.py`

- [x] **Step 1: 将 `_load_column` 收紧为 v2 object loader**

用下面的实现替换现有 dual-format 分支：

```python
def _load_column(item) -> Column:
    """Load column from v2 object format produced by Column.to_dict()."""
    if not isinstance(item, dict):
        raise InvalidDatabaseFile(
            f"unrecognized column entry: {item!r} "
            "(expected Column.to_dict() object form; legacy [name, type] arrays "
            "are no longer supported — please migrate to v2 object format)"
        )
    return Column.from_dict(item)
```

不要保留 `isinstance(item, list)`、长度检查或从数组构造 `Column` 的路径。v2 dict 仍通过 `Column.from_dict(item)`，所以 `nullable`、`unique`、`primary_key` 默认值语义不变。

- [x] **Step 2: 删除 `Catalog.from_bytes` 的 mixed-format 检查**

把 `Catalog.from_bytes` 中从 `schema_entries = info["schema"]` 到 `cols = ...` 的逻辑收敛为：

```python
schema_entries = info["schema"]
cols = tuple(_load_column(item) for item in schema_entries)
c.tables[name] = TableInfo(
    name=name,
    columns=cols,
    root_page_id=_dec_int(info["root_page_id"]),
    next_page_id=_dec_int(info["next_page_id"]),
)
```

删除 `kinds = ...`、`len(kinds) > 1` 和 “mixed legacy/new column formats not allowed” 异常；非 dict 项仍由 `_load_column` 给出明确的 v1 migration 错误。

- [x] **Step 3: 收紧 `create_table` schema 签名并移除 list-form 分支**

将签名和方法体改为只物化 Column tuple：

```python
def create_table(
    self,
    name: str,
    schema: tuple[Column, ...],
    root_page_id: int,
    next_page_id: int,
) -> None:
    if name in self.tables:
        raise ValueError(f"table {name!r} already exists")
    cols: tuple[Column, ...] = tuple(schema)
    self.tables[name] = TableInfo(
        name=name,
        columns=cols,
        root_page_id=root_page_id,
        next_page_id=next_page_id,
    )
```

删除 `schema` 上的 legacy 注释、`if schema and isinstance(schema[0], Column)` 条件以及 `[ (name, type), ... ]` 到 `Column` 的生成器。不要修改 `Column` 字段、`to_dict`/`from_dict` 或 `TableInfo.schema` 投影。

- [x] **Step 4: 更新 Column docstring，明确只支持 v2 持久化形式**

将 `Column` 类的持久化说明改为：

```python
@dataclass(frozen=True)
class Column:
    """Column metadata with column-level constraints.

    Persisted as a JSON object produced and consumed by
    ``to_dict``/``from_dict``.
    """
```

删除 legacy catalogs 使用 `[[name, type], ...]` 和 SQL92 defaults 的兼容说明；SQL92 默认值仍由字段默认参数和 `from_dict` 的 `d.get(...)` 保持。

- [x] **Step 5: 运行 H7 直接行为验证**

```bash
.venv/bin/python - <<'PY'
import json

from tinydb.catalog import Catalog, Column
from tinydb.errors import InvalidDatabaseFile

v2 = {"tables": {"t": {"schema": [Column("id", "INT").to_dict()],
                         "root_page_id": "2", "next_page_id": "0"}}}
cat = Catalog.from_bytes(json.dumps(v2).encode())
assert cat.get_table("t").columns == (Column("id", "INT"),)

legacy = {"tables": {"t": {"schema": [["id", "INT"]],
                             "root_page_id": "2", "next_page_id": "0"}}}
try:
    Catalog.from_bytes(json.dumps(legacy).encode())
except InvalidDatabaseFile as exc:
    assert "legacy [name, type] arrays" in str(exc)
else:
    raise AssertionError("legacy array schema must be rejected")
PY
```

Expected：v2 object roundtrip 成功；v1 array schema 抛出 `InvalidDatabaseFile`，错误信息包含迁移提示。由于 Task 3 尚未迁移既有 list-form 测试，catalog 测试文件暂不在本 task 的完整通过条件内。

- [x] **Step 6: 提交 Task 2**

```bash
git add src/tinydb/catalog.py
git commit -m "refactor(catalog): remove legacy schema formats"
```

---

### Task 3: 同步测试与 fixture

**Files:**
- Delete: `tests/unit/test_type_system.py`
- Modify: `tests/unit/test_aggregation_executor.py`
- Modify: `tests/unit/test_validate_compare_types.py`
- Modify: `tests/unit/test_engine_v1_executor.py`
- Modify: `tests/integration/test_catalog.py`
- Modify: `tests/integration/test_catalog_overflow.py`
- Modify: `tests/integration/test_catalog_constraints.py`
- Delete: `tests/fixtures/legacy_mvp_schema.json`
- Delete: `tests/fixtures/mixed_invalid_schema.json`

- [x] **Step 1: 删除只覆盖旧 API 的 test_type_system.py**

删除整文件。不要把旧 helper 的测试迁移到 registry；registry 的既有行为由 `test_type_system_registry.py`、`test_type_system_v2.py` 和集成测试覆盖。

- [x] **Step 2: 将 aggregation 的类型拒绝测试迁移到 codec registry**

在 `tests/unit/test_aggregation_executor.py` 增加 canonical import：

```python
from tinydb.type_system import codec_for
```

将 `test_agg_sum_text_raises` 的旧实现替换为：

```python
@pytest.mark.unit
@pytest.mark.spec_id("REQ-AGG-009-SCN-01")
def test_agg_sum_text_raises():
    """E8: TEXT codec rejects an integer before aggregation receives it."""
    with pytest.raises(TypeError):
        codec_for("TEXT").validate(123)
```

保留 `pytest.raises(TypeError)`：`CodecError` 多继承 `TypeError`，因此测试继续锁定原有边界语义，同时实际调用的是 canonical `validate`。

- [x] **Step 3: 将三个 catalog overflow 测试的 schema 改成 Column tuple**

在 `tests/integration/test_catalog_overflow.py` 的 import 中加入 `Column`：

```python
from tinydb.catalog import Catalog, Column, _pack_chain, _unpack_chain
```

三个 `create_table` 调用分别使用以下形式，不再传入 list of tuples：

```python
cat.create_table(
    f"t{i}",
    (Column(name="id", type="INT"), Column(name="name", type="TEXT")),
    root_page_id=10 + i,
    next_page_id=11 + i,
)

cat.create_table(
    "only",
    (Column(name="id", type="INT"),),
    root_page_id=2,
    next_page_id=3,
)

cat.create_table(
    f"t{i}",
    (Column(name="id", type="INT"),),
    root_page_id=10 + i,
    next_page_id=11 + i,
)
```

- [x] **Step 4: 迁移 test_catalog.py 的所有 create_table 调用**

不仅替换设计中标出的 line 38/54 两处，也要处理 line 22 的变量 schema，因为它同样被传入 `create_table`。目标形态为：

```python
schema = (
    Column(name="id", type="INT"),
    Column(name="name", type="TEXT"),
)
c.create_table("users", schema, root_page_id=2, next_page_id=2)
assert ti.schema == [("id", "INT"), ("name", "TEXT")]

c.create_table(
    "t",
    (Column(name="x", type="INT"),),
    root_page_id=2,
    next_page_id=2,
)

c.create_table(
    "big",
    (Column(name="v", type="INT"),),
    root_page_id=huge,
    next_page_id=huge,
)
```

保留 `TableInfo.schema` 的 tuple-to-list projection 断言，不要把 `TableInfo.schema` API 改成 Column tuple。

- [x] **Step 5: 删除 catalog_constraints.py 的三条 legacy 回归测试**

删除以下完整测试函数：

```python
def test_catalog_loads_legacy_mvp_format(): ...
def test_catalog_legacy_format_nullable_default_true(): ...
def test_catalog_rejects_mixed_old_and_new_columns(): ...
```

实际编辑时删除其完整函数体及 fixture 调用；同时移除不再使用的 import：

```python
from tinydb.errors import InvalidDatabaseFile
```

保留 `new_constraints_schema.json`、v2 roundtrip、constraints persistence 和 nullable insert 行为测试。文件内其余 `create_table` 调用已经使用 `Column` tuple，不要改变其约束字段。

- [x] **Step 6: 清除两个测试模块 docstring 中的旧 API 名称**

`tests/unit/test_validate_compare_types.py` 顶部说明改为只描述现代 API，例如：

```python
"""Unit tests for validate_compare_types and infer_literal_type.

The registry comparison API requires exact type and parameter matches. These
are the canonical checks used by executor.py; legacy module-level conversion
helpers are intentionally not part of this test module.
"""
```

`tests/unit/test_engine_v1_executor.py` 顶部说明中，将：

```python
Note on type mismatch: eval_expr raises ``TypeError`` (preserving MVP
behavior via ``py_to_db``) on direct EqualsExpr calls.
```

改为：

```python
Note on type mismatch: eval_expr raises ``TypeError`` on direct EqualsExpr
calls after the canonical codec/type validation boundary.
```

不要改动两个文件实际测试的 expression semantics。

- [x] **Step 7: 删除两个失效 fixture**

删除：

```bash
git rm tests/fixtures/legacy_mvp_schema.json tests/fixtures/mixed_invalid_schema.json
```

然后确认剩余 loader 只引用 v2 fixture：

```bash
grep -RInE 'legacy_mvp_schema\.json|mixed_invalid_schema\.json' src tests || true
```

Expected：无输出。

- [x] **Step 8: 运行同步后的定向测试和旧 API 引用扫描**

```bash
.venv/bin/python -m pytest \
  tests/unit/test_aggregation_executor.py \
  tests/unit/test_validate_compare_types.py \
  tests/unit/test_engine_v1_executor.py \
  tests/integration/test_catalog.py \
  tests/integration/test_catalog_overflow.py \
  tests/integration/test_catalog_constraints.py -q

if grep -RInE 'encode_int|encode_text|encode_bool|encode_float|decode_int|decode_text|decode_bool|decode_float|py_to_db|db_to_py|validate_compare\b' src tests --exclude-dir=__pycache__; then
    printf '%s\n' 'legacy type-system reference remains' >&2
    exit 1
fi
```

Expected：定向测试全绿；grep 无输出并以成功状态结束。若 `grep` 命中，仅删除过时引用，不得恢复兼容 helper 或新增 wrapper。

- [x] **Step 9: 提交 Task 3**

```bash
git add -A tests
# 确认暂存区只包含本 task 的 7 个测试文件及 2 个 fixture 删除
git diff --cached --stat
git commit -m "test: migrate callers to canonical codecs and catalog schema"
```

---

### Task 4: 同步 type-system-basic 规范

**Files:**
- Modify: `openspec/specs/type-system-basic/spec.md:107-135`
- Reference (已有预制品): `openspec/changes/type-codec-and-catalog-cleanup/specs/type-system-basic/spec.md`

- [x] **Step 1: 用 delta 的 canonical conversion requirement 替换 main spec 旧段落**

把 main spec 中旧的 `py_to_db`/`db_to_py` requirement（从 `### Requirement: Python to DB and DB to Python conversion` 到文件末尾的旧 scenarios）替换为以下内容；其它 literal、binary encoding、decoding 和 strict coercion requirements 不变：

```markdown
### Requirement: Python to DB and DB to Python conversion

The system SHALL provide explicit conversion between Python native objects and DB-typed values via the codec registry. The legacy `py_to_db`/`db_to_py` module-level helpers are removed; canonical entry points are `codec_for(type, params).encode_py(value)` for Python → DB bytes and `codec_for(type, params).decode_bytes(buf, offset)` for DB bytes → Python.

#### Scenario: Convert Python int to INT via codec registry
- **WHEN** converting Python `42` to DB type for an INT column via `codec_for("INT").encode_py(42)`
- **THEN** the function SHALL return bytes `b'\x00\x00\x00\x2a'` (8-byte big-endian)

#### Scenario: Convert Python str to TEXT via codec registry
- **WHEN** converting Python `'alice'` to DB type for a TEXT column via `codec_for("TEXT").encode_py('alice')`
- **THEN** the function SHALL return bytes `b'\x00\x05alice'` (length-prefixed UTF-8)

#### Scenario: Convert Python float to FLOAT via codec registry
- **WHEN** converting Python `2.5` to DB type for a FLOAT column via `codec_for("FLOAT").encode_py(2.5)`
- **THEN** the bytes SHALL equal `struct.pack('>f', 2.5)`

#### Scenario: Convert Python float NaN rejected via codec registry
- **WHEN** converting Python `float('nan')` to DB type for a FLOAT column via `codec_for("FLOAT").encode_py(float('nan'))`
- **THEN** the function SHALL raise `CodecError` with message containing `"NaN not allowed"`

#### Scenario: Convert Python bool to BOOL via codec registry
- **WHEN** converting Python `True` to DB type for a BOOL column via `codec_for("BOOL").encode_py(True)`
- **THEN** the function SHALL return `b'\x01'`

#### Scenario: Convert Python float to INT rejected via codec registry
- **WHEN** converting Python `2.5` to DB type for an INT column via `codec_for("INT").encode_py(2.5)`
- **THEN** the function SHALL raise `CodecError` with a type-mismatch message

#### Scenario: Parametric type (VARCHAR) conversion via codec registry
- **WHEN** converting Python `'hello'` to `VARCHAR(10)` via `codec_for("VARCHAR", (10,)).encode_py('hello')`
- **THEN** the function SHALL return `b'\x00\x05hello'`

#### Scenario: Parametric type VARCHAR length exceeds limit rejected
- **WHEN** converting Python `'x' * 20` to `VARCHAR(10)` via `codec_for("VARCHAR", (10,)).encode_py('x' * 20)`
- **THEN** the function SHALL raise `CodecError` with a message containing `"length"` and `"exceeds"`

#### Scenario: Legacy py_to_db helper removed from public API
- **WHEN** any module attempts to import `py_to_db` from `tinydb.type_system`
- **THEN** the import SHALL raise `ImportError`

#### Scenario: Legacy db_to_py helper removed from public API
- **WHEN** any module attempts to import `db_to_py` from `tinydb.type_system`
- **THEN** the import SHALL raise `ImportError`
```

该段必须与已有 delta 的 requirement/scenario 语义一致；不要在 main spec 中重新引入旧函数契约或新增 migration script 的 requirement。

- [x] **Step 2: 检查规范 diff 只改变 conversion contract**

```bash
git diff --check
git diff -- openspec/specs/type-system-basic/spec.md
grep -n 'codec_for(type, params)\|codec_for("INT")\|ImportError' openspec/specs/type-system-basic/spec.md
```

Expected：无 whitespace error；diff 只替换 Python↔DB conversion requirement；canonical `codec_for` 和两个 ImportError scenario 均出现；main spec 的该段不再描述“function SHALL return encoded bytes”但不指明 registry 入口。

- [x] **Step 3: 提交 Task 4**

```bash
git add openspec/specs/type-system-basic/spec.md
git commit -m "docs(spec): make codec registry the conversion contract"
```

---

### Task 5: 全量验证、verify report 与 archive

**Files:**
- Create: `docs/superpowers/reports/2026-07-21-type-codec-and-catalog-cleanup-verify.md`
- Archive: `openspec/changes/type-codec-and-catalog-cleanup/` → `openspec/changes/archive/2026-07-21-type-codec-and-catalog-cleanup/`
- Verify: all source/test/spec files changed by Tasks 1–4

- [x] **Step 1: 运行全量 pytest 并记录实际 collected 数**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected：所有保留测试通过，且没有 collection/import error。Design Doc §5 约估约 660，`tasks.md` 约估约 709；两处估算与当前参数化测试计数不一致，因此以本命令实际输出为准，验收条件是全绿而不是硬编码某个数量。报告中记录命令输出的 passed/collected 数，并注明相对基线删除的是 `test_type_system.py` 全部用例和 3 个 catalog legacy 回归用例。

- [x] **Step 2: 运行 pyflakes**

```bash
pyflakes src/tinydb/
```

Expected：无输出、退出码 0。重点确认删除 helper 后没有遗留 `struct`/常量/导入错误；若命中未使用符号，只删除由本 change 造成的 dead code，不修改 codec 行为。

- [x] **Step 3: 运行 legacy symbol 和 fixture 引用扫描**

```bash
if grep -rnE 'encode_int|encode_text|encode_bool|encode_float|decode_int|decode_text|decode_bool|decode_float|py_to_db|db_to_py|validate_compare\b' src/tinydb/; then
    exit 1
fi
if grep -rnE 'from tinydb\.type_system import[^(]*(encode_|decode_|py_to_db|db_to_py|validate_compare)' src/ tests/ --exclude-dir=__pycache__; then
    exit 1
fi
if grep -rnE 'legacy_mvp_schema\.json|mixed_invalid_schema\.json' src/ tests/ --exclude-dir=__pycache__; then
    exit 1
fi
```

Expected：三段命令均无输出并成功结束。允许规范 delta/main spec 在文字中提到被移除的 `py_to_db`/`db_to_py`；本扫描限定在 `src/` 与 `tests/`，以验证运行时和测试调用方没有旧引用。

- [x] **Step 4: 运行 coverage 并强制达到 93%**

```bash
.venv/bin/coverage erase
.venv/bin/coverage run --source=src/tinydb -m pytest tests/
.venv/bin/coverage report --include='src/tinydb/*' --fail-under=93
```

Expected：pytest 全绿，`coverage report` 退出码 0 且总覆盖率至少 93%。删除旧测试导致的行数变化不应通过降低 source 范围或排除现有模块来掩盖。

- [x] **Step 5: 创建 verify report，包含 §9 全部证据和 §7 迁移风险**

创建 `docs/superpowers/reports/2026-07-21-type-codec-and-catalog-cleanup-verify.md`，使用以下结构并填入本次命令的真实输出摘要：

```markdown
# type-codec-and-catalog-cleanup Verification Report

- Change: `type-codec-and-catalog-cleanup`
- Base ref: `54874de47807e1473f0a06b5ab761eefe726a145`
- Verification date: `2026-07-21`

## Results

| Check | Command | Result |
|---|---|---|
| Full tests | `.venv/bin/python -m pytest tests/ -q` | PASS；记录实际 passed/collected 数 |
| Lint | `pyflakes src/tinydb/` | PASS；无输出 |
| Legacy source scan | §9 grep patterns | PASS；无命中 |
| Coverage | `coverage run ...` + `coverage report --fail-under=93` | PASS；总覆盖率至少 93% |
| Fixture/reference scan | legacy fixture names | PASS；无命中 |

## Scope evidence

- Removed 11 module-level type conversion/compare helpers and their obsolete tests.
- Kept codec registry, `codec_for`, `CodecError`, `validate_compare_types`, and literal parsers.
- Catalog now reads only `Column.to_dict()` object entries and receives `tuple[Column, ...]` schemas.
- Removed three legacy/mixed catalog tests and two obsolete fixtures.
- Updated row codec and test documentation to reference the canonical codec API.

## Migration and risks

- Existing v2 object-format `.db` files remain readable.
- v1 `[name, type]` array-format catalogs now raise `InvalidDatabaseFile` with a manual migration message.
- Imports of `encode_*`, `decode_*`, `py_to_db`, `db_to_py`, and `validate_compare` are intentionally breaking changes.
- No compatibility shim, deprecation warning, or in-process migration script is included.
```

报告中的测试数量和 coverage 数值必须来自 Step 1/4 输出；不能凭估算填写。报告本身只记录本 change 的验证，不创建 release-note 或 migration script 文件。

- [x] **Step 6: 验证 archive 前的 delta/main 一致性并完成归档**

先确认 Task 4 已把 conversion requirement 合并到 main spec，delta 中没有尚未反映的有效内容：

```bash
git diff --check
git status --short
```

然后按标准 archive 流程执行：

```bash
openspec archive type-codec-and-catalog-cleanup --yes
```

archive 操作必须完成两件事：保留 `openspec/specs/type-system-basic/spec.md` 的 canonical codec requirement，并将 change artifacts 移至 `openspec/changes/archive/2026-07-21-type-codec-and-catalog-cleanup/`。如果 archive 工具报告 delta 已经与 main spec 一致，不要再次复制 requirement；以最终 main spec 只有一份 conversion requirement 为准。归档后检查：

```bash
test -f openspec/changes/archive/2026-07-21-type-codec-and-catalog-cleanup/.comet.yaml
test ! -e openspec/changes/type-codec-and-catalog-cleanup/.comet.yaml
grep -n 'codec_for(type, params)' openspec/specs/type-system-basic/spec.md
```

- [x] **Step 7: 提交 Task 5 的 report 和 archive 结果**

```bash
git add -A docs/superpowers/reports \
  openspec/specs/type-system-basic/spec.md \
  openspec/changes

git diff --cached --check
git commit -m "chore(archive): verify and archive type-codec-and-catalog-cleanup"
```

---

## 设计覆盖与完成条件

| Design Doc 章节 | 计划覆盖 |
|---|---|
| §1 Context | 文件地图与 Task 1/2 的删除边界对应 H6/H7；生产路径继续走 registry。 |
| §3 Decisions | Task 1/2 直接删除、不加 wrapper；Task 3 直接替换测试调用方；Task 5 记录 breaking migration 风险。 |
| §4 Architecture | Task 1 保留 registry 与 parser；Task 2 使用 `_load_column` v2-only 实现和 Column tuple schema；Task 1 更新 row_codec 文档。 |
| §5 Test & Fixture Changes | Task 3 删除/修改 7 个测试文件和 2 个 fixture，Task 5 执行全量验证。 |
| §7 Migration & Risks | Task 2 明确 v1 `InvalidDatabaseFile`；Task 5 report 记录 v2 兼容、旧 import 破坏性变更和无 migration/shim。 |
| §9 Verification Checklist | Task 5 覆盖 pytest、pyflakes、两组 grep、coverage ≥93%、删除项、verify report、spec merge/archive。 |

完成标准是：Task 1–5 各有且仅有一个对应 commit；`src/` 和 `tests/` 没有旧 helper/runtime 引用；全量测试、pyflakes、grep、coverage 全部通过；verify report 存在；archive 目录存在且 active change 目录已移除。
