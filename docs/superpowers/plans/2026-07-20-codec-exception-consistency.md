---
change: codec-exception-consistency
design-doc: docs/superpowers/specs/2026-07-20-codec-exception-consistency-design.md
base-ref: 15518f4b35a747652ffea922b3c26484c27086e5
---

# codec-exception-consistency 实施计划

**目标**：修复 `type-codec-and-catalog-cleanup` 评审中发现的 6 个 CONFIRMED 代码评审问题——codec 异常一致性（F2/F3/F6）与 catalog 严格校验（F1/F4/F5）。

**架构**：4 个文件编辑 + 1 处陈旧注释删除。所有 6 个修复都是外科手术式的,没有架构性变更。F3+F6 合并到一个重构(`encode_py` → `self.validate()`)。

**技术栈**：仅 Python 3.10+ 标准库。不增加新的外部依赖。

---

## Phase 1：RED 测试(先写失败的测试)

### Task 1.1：`_IntCodec.encode_py` 溢出 → `CodecError` 的 RED 测试
文件：`tests/unit/test_type_system_v2.py`

```python
def test_int_codec_encode_py_overflow_raises_codec_error():
    """F3+F6 重构后,encode_py 应为超出范围的整数抛出 CodecError(而非
    OverflowError),与 _IntCodec.validate 契约一致。"""
    codec = lookup("INT")
    with pytest.raises(CodecError, match="INT out of range"):
        codec.encode_py(2**31)
```

### Task 1.2：`_VarcharCodec` 溢出 → `CodecError` 的 RED 测试
文件：`tests/unit/test_type_system_v2.py`

```python
def test_varchar_codec_overflow_raises_codec_error():
    """F2 修复后,长度越界应抛 CodecError(而非 TypeError)。"""
    codec = codec_for("VARCHAR", (10,))
    with pytest.raises(CodecError, match="length 11 exceeds max"):
        codec.encode_py("x" * 11)
```

### Task 1.3：`_CharCodec` 溢出 → `CodecError` 的 RED 测试
文件：`tests/unit/test_type_system_v2.py`

```python
def test_char_codec_overflow_raises_codec_error():
    codec = codec_for("CHAR", (5,))
    with pytest.raises(CodecError, match="length 6 exceeds max"):
        codec.encode_py("x" * 6)
```

### Task 1.4：拒绝 2-tuple 形式的 RED 测试(在 `tests/integration/test_catalog.py`)
文件：`tests/integration/test_catalog.py`

```python
@pytest.mark.integration
def test_create_table_rejects_legacy_2tuple_with_type_error(tmp_path):
    """F1：传入遗留 [name, type] 2-tuple 时应抛 TypeError,
    不是被 tuple(schema) 静默接受。"""
    c = Catalog()
    with pytest.raises(TypeError, match="create_table expects Column"):
        c.create_table("t", [("id", "INT")], root_page_id=2, next_page_id=2)


@pytest.mark.integration
def test_create_table_rejects_string_iterable(tmp_path):
    """F1：传入裸字符串 iterable 时应抛 TypeError,
    防止 tuple(schema) 把字符串 split 成单字符。"""
    c = Catalog()
    with pytest.raises(TypeError, match="create_table expects Column"):
        c.create_table("t", "INT", root_page_id=2, next_page_id=2)
```

### Task 1.5：拒绝非 dict 非 list 的 RED 测试
文件：`tests/unit/test_catalog_type_params.py`

```python
def test_load_column_rejects_non_dict_non_list_with_generic_message():
    """F4 修复后,非 list 非 dict 输入(int/None/str)应得到通用
    'expected Column.to_dict() object form' 提示,
    而非误导性的遗留形式提示。"""
    with pytest.raises(InvalidDatabaseFile, match="expected Column.to_dict"):
        _load_column(42)
    with pytest.raises(InvalidDatabaseFile, match="expected Column.to_dict"):
        _load_column(None)
    # 同时断言没有错误地泄漏遗留提示：
    with pytest.raises(InvalidDatabaseFile) as excinfo:
        _load_column(42)
    assert "legacy [name, type] arrays" not in str(excinfo.value)
```

---

## Phase 2：GREEN 修复(应用最小代码使 RED 测试通过)

### Task 2.1：F3+F6 — `_IntCodec.encode_py` 委托给 `validate`
文件：`src/tinydb/type_system.py`

将:
```python
def encode_py(self, value):
    if not isinstance(value, int) or isinstance(value, bool):
        raise CodecError(f"expected int for {self.name}, got {type(value).__name__}")
    fmt, lo, hi = self._spec
    if not (lo <= value < hi):
        raise OverflowError(f"{self.name} out of range: {value}")
    return struct.pack(fmt, value)
```

改为:
```python
def encode_py(self, value):
    self.validate(value)
    fmt, _, _ = self._spec
    return struct.pack(fmt, value)
```

### Task 2.2：F3+F6 — `_FloatCodec.encode_py` 委托给 `validate`
与 2.1 同 pattern。

### Task 2.3：更新现有 Int 溢出测试
文件：`tests/unit/test_type_system_v2.py`
将 `test_int_codec_overflow_raises` 中的 `pytest.raises(OverflowError)` 改为 `pytest.raises(CodecError)`。

### Task 2.4：F2 — `_VarcharCodec._check` 抛 `CodecError`
将 `raise TypeError(...)` 改为 `raise CodecError(...)`。

### Task 2.5：F2 — `_CharCodec.encode_py` 抛 `CodecError`
同上 pattern。

### Task 2.6：更新 VARCHAR/CHAR 长度越界测试
将 `pytest.raises(TypeError)` 改为 `pytest.raises(CodecError)`。

### Task 2.7：F1 — `Catalog.create_table` 加 isinstance 守卫
在 `tuple(schema)` 之前增加校验。

### Task 2.8：F4 — `_load_column` 拆分错误信息
拆分为 list-form(遗留提示) 与 non-dict non-list(通用提示) 两条路径。

### Task 2.9：F5 — 删除陈旧的 section-divider 注释
删除 `src/tinydb/type_system.py` 原 line 127 和 line 174 各 1 行注释。

---

## Phase 3：验证

### Task 3.1：运行完整测试套件
```bash
.venv/bin/pytest tests/ -q --no-cov
```
预期：689 tests pass(683 baseline + 6 个新 RED→GREEN)。

### Task 3.2：pyflakes
```bash
.venv/bin/python -m pyflakes src/tinydb/
```
预期：clean(exit 0,无输出)。

### Task 3.3：覆盖率
```bash
.venv/bin/pytest tests/ -q --cov=src/tinydb --cov-report=term --no-header
```
预期：≥ 93%(持平或上升)。

---

## 提交计划

1. `fix(catalog): _load_column splits errors by input type (F4)`
2. `refactor(codec): encode_py delegates to self.validate and surfaces CodecError (F2+F3+F6)`
3. `test(catalog): create_table rejects non-Column inputs (F1 RED)`
4. `chore(type_system): remove stale legacy-helpers breadcrumb comments (F5)`

注：实际执行期间 F1 的 GREEN 修复与 F4 提交一起由并发子 agent 落地,因此 commits 顺序大致为 `0251b81` → `cf065c4` → `393dc6e` → `6d48ce1`。

## 风险

参见 `docs/superpowers/specs/2026-07-20-codec-exception-consistency-design.md` 的 R1/R2/R3 及提案文档。

主要风险:`encode_py` 公共 API 异常类型变更(`OverflowError`/`TypeError` → `CodecError`)。
`CodecError` 多继承 `TypeError, ValueError, OverflowError`,因此所有现有
`except (TypeError, ValueError, OverflowError)` 的捕获点仍能命中(已在 `parser.py:598, 994, 1021`、`tokenizer.py:85, 112`、`btree.py:185, 235` 等处验证)。无回归。
