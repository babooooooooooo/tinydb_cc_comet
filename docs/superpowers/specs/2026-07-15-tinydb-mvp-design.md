---
comet_change: tinydb-mvp
role: technical-design
canonical_spec: openspec
---

# Design: tinydb-mvp — 深度技术设计

> 上游事实源（不要重写）：`openspec/changes/tinydb-mvp/proposal.md`、`design.md`、`specs/*.md`、`tasks.md`。
> 本文档是对上述产物的**深度技术细化**：实现策略、技术风险、测试落地、边界条件、Spec Patch。
> 凡是本文与上游冲突，以本文为准；上游重大变更走 Spec Patch 流程。

---

## 1. 上下文与上游约束

`tinydb-mvp` 是从零构建 Python 嵌入式关系型数据库项目的**第一个里程碑切片**，对应一份精简版端到端方案——单文件 slotted-page 存储、4 类型系统、最小 SQL 子集、Python API。

上游已确认：
- Why：可教学 + 可嵌入使用
- In 范围：DDL（CREATE/DROP TABLE）、DML（INSERT/SELECT/DELETE）、`WHERE col = literal`、INT/TEXT/FLOAT/BOOL、slotted-page 单文件存储、Python API
- Out 范围：ACID、UPDATE、AND/OR、ORDER BY/LIMIT、聚合、B-tree、CLI、扩展类型
- 6 个开放技术问题 Q1-Q6 已在 brainstorming 中确认（详见 §10）

---

## 2. 模块边界与依赖图

```
                            ┌──────────────────┐
                            │  database.py     │  Public API
                            │  Database()      │
                            │  Row             │
                            │  execute(sql)    │
                            └────────┬─────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                ▼                    ▼                    ▼
        ┌─────────────┐      ┌──────────────┐      ┌─────────────────┐
        │ tokenizer.py│      │  parser.py   │      │  executor.py    │
        │  pure       │      │  pure        │      │  stateful       │
        │  tkn → list │      │  tkn → AST   │      │  pgr, cat, type │
        └─────┬───────┘      └──────┬───────┘      └────────┬────────┘
              │                     │                       │
              └─────────┬───────────┘                       │
                        ▼                                   ▼
                  ┌──────────┐                       ┌──────────────┐
                  │ type_    │                       │  storage     │
                  │ system.py│ ◀────────────────────▶│  pgr/cat/    │
                  │  pure    │                       │  slot/codec  │
                  └──────────┘                       └──────────────┘
                                                          │
                                                ┌─────────┴─────────┐
                                                ▼                   ▼
                                          ┌─────────┐          ┌─────────┐
                                          │ pager.py│          │catalog.py│
                                          │ 4KB pgs │          │  JSON    │
                                          │  mmap   │          │  page 1  │
                                          └─────────┘          └─────────┘
```

依赖规则：
- `parser`/`tokenizer`/`type_system`/`row_codec` 是 **pure function 模块**，单测不需要任何 fixture
- `executor` 是 **唯一同时持有 Pager 与 Catalog 的层**；所有 I/O 仅发生在该层
- `database.py` 是唯一对外公开模块；其他模块命名以下划线开头为内部约定

---

## 3. 存储引擎详解

### 3.1 Page 地址与文件头

每个 `.db` 文件：
- **Page 0**：文件头页（仅用前 64 字节，其余保留）
  - `MAGIC: 8B = b'TINYDB\x00'`
  - `SCHEMA_VERSION: u8 = 0x01`
  - 其余 55 字节保留（为后续 schema_version 演进预留）
- **Page 1**：catalog 页（4096 字节完整用于 JSON 编码表元数据）
- **Page 2+**：data pages

**Page size 固定 4096 B**（`PAGE_SIZE` 常量在 `pager.py`）。`u32` 为 page id 类型，最大可寻址 2³² page ≈ 16 TB。

`:memory:` 模式：Pager 持有 `bytearray`，首 8192 B（两个 page）等价 file 模式（header + catalog），后续按需扩展 bytearray。

### 3.2 Slotted Page 布局

每页结构（4096 B）：

```
偏移              字节            字段
──────────────────────────────────────────────
0                 1               page_type     (u8: 0=free, 1=data, 2=overflow)
1                 1               num_slots     (u8: 当前 slot 数，≤ 32)
2                 2               free_offset   (u16: 下一个 free data 起点)
4                 4               overflow_next_page_id (u32: NULL=0xFFFFFFFF)
8                 8               reserved      (u8 × 8)
──────────────────────────────────────────────  ← header 16 B
16                num_slots × 6                  slot directory（从前往后长）
──────────────────────────────────────────────
                  不断被 row_bytes 数据"侵入"     free space 收尾
──────────────────────────────────────────────  ← free_offset 位置
                  数据区（从末尾往前长）
──────────────────────────────────────────────  ← PAGE_SIZE = 4096
```

每个 slot 6 字节：
```
offset: u16  (在 data area 内的字节偏移)
length: u16  (row 数据字节长度)
flags:  u16  (bit 0 = TOMBSTONE; bit 1 = SPILL_START)
```

**约束**：
- `num_slots ≤ 32`（每行 6 B × 32 = 192 B，仅占 header + slot 共 208 B，剩余 3888 B 做 data）
- 当 32 个 slot 满 + 数据区没有空间 → `SlottedPage.insert` raise `PageFull`
- SPILL_START flag 仅在 page_type=1 时有效；page_type=2 时整个 page 都是 overflow data，无 slot directory

### 3.3 Overflow Pages（SPILL_START 行为）

行编码后字节数 > `MAX_INLINE_PAYLOAD`（~3970 B）时启用 spill chain。

**触发条件**：`len(row_bytes) > MAX_INLINE_PAYLOAD`

**写入流程**（在 `Executor._insert_with_overflow(row_bytes, table_info)`）：
1. 计算 `chunks = _split_for_overflow(row_bytes)`：每块 ≤ `MAX_INLINE_PAYLOAD`，第一块用第一页装，后续用 overflow 页链
2. `_try_insert_into_existing_pages(chunks[0], spillage=True)`：插入首页，slot 标记 SPILL_START
3. 对每个剩余 chunk：
   - `_alloc_overflow_page()` 申请新 page（page_type=2）
   - `_write_chunk_to_overflow_page(page, chunk)`：data area 起点（header 后 16 B）直接放 chunk
   - `_link_overflow_chain(prev_page, new_page)`：前一页 `overflow_next_page_id` 指向新页
4. overflow chain 末尾页 `overflow_next_page_id = 0xFFFFFFFF`（NULL_PAGE_ID）

**读取流程**（在 `Executor._read_row(slot, page)` 命中 SPILL_START）：
1. 读首页 slot 数据 = `first_chunk`
2. 跟随首页 `overflow_next_page_id` → 第 2 页，读其 data area 起点连续 `MAX_INLINE_PAYLOAD` 字节（除非该页 `overflow_next_page_id != NULL`）
3. 末尾 page 用 `overflow_next_page_id == NULL_PAGE_ID` 终止（最后一页可能不满 MAX_INLINE_PAYLOAD，read 时读 `PAGE_SIZE - 16` 之后的全部 data 直到末尾）
4. 拼接所有 chunks → `complete_row_bytes`
5. `_catalog.schema` 字段驱动 `row_codec.decode_row(complete_row_bytes, schema)` 还原 Python 值

**删除 SPILL_START 行**：
1. 标记首页 slot TOMBSTONE
2. 遍历 overflow chain，标记每个 overflow page `page_type = 0`（free），加进 Pager 的 free page list

`★ 设计意图 ─────────────────────────────────────`
溢出的全部复杂度封装在 Executor 内部。`SlottedPage` 与 `Pager` 接口对溢出无知——`SlottedPage.insert` 不区分 SPILL_START；`Pager.alloc_page` 返回 `page_type=1`，Executor 自己改 page header。TDD 测试 SlottedPage 时无需涉及 overflow，独立可测。
`─────────────────────────────────────────────────`

### 3.4 Row 编码格式

每行 = `null_bitmap + length_prefixed_values`：

```
[null_bitmap]    ceil(col_count / 8) 字节，bit i 表示 col[i] 是否 NULL（LSB-first：column 0 → byte 0 bit 0，column 1 → byte 0 bit 1）
[values...]      每列变长编码：
   INT    ：固定 8 B 大端
   FLOAT  ：固定 8 B IEEE 754 大端
   BOOL   ：固定 1 B (0/1)
   TEXT   ：2 B 长度前缀 + UTF-8 字节
```

`null_bitmap` 长度公式：`bitmap_len = (col_count + 7) // 8` 例如 5 列 → 1 B；9 列 → 2 B。

**Row 总字节数估算**（用于判断是否 spill）：
- INT 列：1 B (bitmap) + 8 B = 9 B/列
- FLOAT：1+8 = 9 B/列
- BOOL：1+1 = 2 B/列
- TEXT：1 B bitmap + 2 B 长度前缀 + 实际 UTF-8 字节

`MAX_INLINE_PAYLOAD = 4096 - 16 (header) - 6*N (slots, N=当前 slot 数 ≤ 32) - bitmap_len` —— 留 6 B 用于未来额外 slot 扩展。

实际上限 ≈ `4096 - 16 - 192 - 8 = 3880 B`，再减 row 自身 bitmap ≈ 3872 B。MVP 设定 `MAX_INLINE_PAYLOAD = 3800`（保留缓冲）。

### 3.5 Catalog 页编码

**page 1 内容 = `json.dumps(catalog_dict).encode() + b'\x00' * padding_to_4096`**

`catalog_dict` 格式：
```json
{
  "tables": {
    "users": {
      "schema": [
        {"name": "id",   "type": "INT",  "nullable": false},
        {"name": "name", "type": "TEXT", "nullable": true}
      ],
      "root_page_id": 2,
      "next_page_id": 3
    }
  }
}
```

**JSON int 精度问题（Spec Patch R8）**：
- INT 列若 schema 中字段名包含可能超过 2⁵³ 的值 → 在 JSON 中**用 quoted string 编码**
- MVP 阶段 schema column names 都是 Python 标识符（短），不会超 2⁵³；但 Catalog 持久化 schema 字典时，所有 INT 字段都用 str 序列化以防御

实际实现层：`catalog.py::_encode_int_field(v) -> str` 总是把 `int` 用 `str()` 包成 JSON 字符串；`decode` 路径反向 `int(_decode_int_field(s))` 转回。

### 3.6 错误映射

| 模块 | 抛出 | tinydb 重导出 |
|------|------|--------------|
| tokenizer | `TokenError(line, col, msg)` | `tinydb.errors.TokenError` |
| parser | `ParseError(line, col, msg)` | `tinydb.errors.ParseError` |
| executor | `KeyError` (no such table), `TypeError` (coercion), `ValueError` (overflow/encoding) | `tinydb.errors.ExecutionError`, `TypeError`, `ValueError`（保留 stdlib） |
| pager | `InvalidDatabaseFile`, `UnsupportedSchemaVersion` | 同名重导出 |
| slotted_page | `PageFull` | `tinydb.errors.PageFull` |
| catalog | `CatalogFull` | `tinydb.errors.CatalogFull` |

所有自定义异常继承 `tinydb.errors.TinydbError(Exception)` 基类，便于 `except TinydbError:` 一网打尽。

---

## 4. 类型系统详解

模块 `type_system.py`，纯 stdlib，`struct` 用于打包，`dataclasses` 用于 AST。

### 4.1 编码函数签名

```python
def encode_int(value: int) -> bytes:                 # 8 B big-endian signed
def decode_int(buf: bytes, offset: int) -> tuple[int, int]:  # returns (value, new_offset)
def encode_text(value: str) -> bytes                 # 2 B length + UTF-8
def decode_text(buf: bytes, offset: int) -> tuple[str, int]
def encode_bool(value: bool) -> bytes                 # 1 B
def decode_bool(buf: bytes, offset: int) -> tuple[bool, int]
def encode_float(value: float) -> bytes               # 8 B struct '>d'
def decode_float(buf: bytes, offset: int) -> tuple[float, int]
```

每对 `(encode, decode)` 必须 roundtrip 严格验证；hypothesis 属性测试也会生成边界值再 roundtrip 检查。

### 4.2 严格类型守卫

`type_system.py::validate_compare(col_value: bytes, col_type: str, lit_value: bytes, lit_type: str) -> None`：
- 如果 `col_type != lit_type`：`raise TypeError("type mismatch: {col_type} vs {lit_type}")`
- 如果 `lit_type == 'FLOAT'` 且 `lit_value` 编码代表 inf/NaN：`raise ValueError("FLOAT inf/NaN not allowed")`

Py ↔ DB 转换 `py_to_db(value, target_type)`：
- `int → INT`：直接调 `encode_int`
- `str → TEXT`：直接调 `encode_text`
- `bool → BOOL`：直接调 `encode_bool`
- `float → FLOAT`：`isnan/isinf` 检查 raise ValueError → `encode_float`
- `float → INT`：raise `TypeError`
- 反向同理

### 4.3 字面量层（tokenizer 调用）

tokenizer 字面量解析时直接拒绝：
```python
def parse_float_literal(s: str) -> float:
    v = float(s)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"FLOAT inf/NaN not allowed: {s!r}")
    return v
```

既不让 inf/NaN 进入 SQL 字面量层（即使 SQL 写了 `NaN`，tokenizer 就 raise）。

---

## 5. SQL 解析详解

### 5.1 Token 类型

```python
@dataclass
class Token:
    type: Literal['KEYWORD', 'IDENT', 'INT', 'FLOAT', 'TEXT', 'BOOL', 'PUNCT', 'EOF']
    value: Any           # str | int | float | bool | None
    line: int            # 1-indexed
    col: int             # 1-indexed
```

KEYWORD 列表（大小写不敏感归一化）：`CREATE TABLE DROP INSERT INTO VALUES SELECT FROM WHERE AND OR TRUE FALSE INT TEXT FLOAT BOOL`

### 5.2 AST 节点

```python
@dataclass
class StatementList:
    statements: list[ASTNode]
    line: int; col: int

@dataclass
class CreateTable:
    name: str
    columns: list[tuple[str, str, bool]]  # (name, type, nullable) — nullable=MVP 总是 True
    line: int; col: int

@dataclass
class DropTable:
    name: str
    line: int; col: int

@dataclass
class Insert:
    table: str
    columns: list[str]
    values: list[list[Any]]   # 一或多个 row
    line: int; col: int

@dataclass
class Select:
    table: str
    columns: list[str]        # ['*'] 或列名列表
    where: tuple[str, str, Any] | None   # (col_name, '=', literal)
    line: int; col: int

@dataclass
class Delete:
    table: str
    where: tuple[str, str, Any] | None
    line: int; col: int
```

### 5.3 解析错误

所有解析错误 raise `ParseError(line, col, message)`：
- 未知 token：`"unexpected {tok.type} {tok.value!r} at line {line}, col {col}"`
- WHERE 操作符非 `=`：`"operator {op} not supported; MVP supports only ="`
- 重复列名：`"duplicate column {name}"`
- 不支持类型：`"type {type_name} not supported in MVP"`
- 列数不匹配：`"value count {n} does not match column count {m}"`

带 `(line, col)` 属性以便 API 层重新 raise 为 `tinydb.errors.ParseError` 时保留位置。

---

## 6. Executor 详解

### 6.1 入口

```python
class Executor:
    def __init__(self, pager: Pager, catalog: Catalog): ...
    
    def execute(self, stmt: ASTNode) -> list[Row]:
        """Dispatch AST → method, return list[Row] (uniform)"""
        method = {
            CreateTable: self._exec_create_table,
            DropTable:   self._exec_drop_table,
            Insert:      self._exec_insert,
            Select:      self._exec_select,
            Delete:      self._exec_delete,
        }[type(stmt)]
        return method(stmt)
```

### 6.2 DDL 流程

`_exec_create_table(stmt)`：
1. 校验 name 不与已有表重名（`catalog.get_table(name) is None`）
2. 分配 root page：`root_id = self.pager.alloc_page()`
3. 注册到 catalog：`catalog.create_table(name, schema, root_id)`
4. 落 catalog 到 page 1：`catalog_persist_to_page(self.pager)`
5. 返回 `[]`

`_exec_drop_table(stmt)`：
1. `catalog.get_table(name)` 不存在 → raise `ExecutionError("table {name} does not exist")`
2. 把 root_id 加入 free page list（best-effort）
3. `catalog.drop_table(name)` → 写回 page 1

### 6.3 INSERT 流程（含溢出）

`_exec_insert(stmt)`：
1. 拿到 schema：`schema = catalog.get_table(stmt.table).schema`
2. 对 stmt.values 的每一行：
   - 检查列数匹配
   - 类型校验：`py_to_db(value, target_type)` 一一对应（任何 mismatch raise TypeError）
   - 编码：`row_bytes = row_codec.encode_row(typed_values, schema)`
   - **是否溢出分支**：
     - `len(row_bytes) <= MAX_INLINE_PAYLOAD` → 简单 insert（见下）
     - `len(row_bytes) > MAX_INLINE_PAYLOAD` → `_insert_with_overflow(...)`

**简单 insert**（非溢出）：
1. `locate_table_pages(table)` 从 `table.root_page_id` 起遍历 chain（next_page_id 由 spill 块维护，data pages 单链）
2. 对每个 data page 调 `slotted_page.insert(row_bytes)`，成功则返回
3. 若全部 PageFull → `pager.alloc_page()`，新页 page_type=1，加入 table chain

### 6.4 SELECT 流程

`_exec_select(stmt)`：
1. 拿到 schema；类型校验 `stmt.where`（若有）
2. 遍历 table 所有 data pages，对每页所有非 TOMBSTONE slot 读出 row_bytes：
   - 若 SPILL_START：跟随溢出链拼成完整 row_bytes
   - 否则直接读 slot length
3. `row_codec.decode_row(bytes, schema)` → Python 值列表
4. WHERE 过滤：`value_at_col(col_name) == lit_value`（strict 类型）
5. 投影：`*` 全部 / 列名列表子集
6. 包成 `Row(values, columns)` 返回 list

### 6.5 DELETE 流程

`_exec_delete(stmt)`：
1. SELECT-like 扫描找到目标 row 的 (page_id, slot_id)
2. 对每个目标：
   - `slotted_page.delete(slot_id)` 标 TOMBSTONE
   - 若 SPILL_START：跟随溢出链把每个 overflow page `page_type` 改 `0`，加入 free list
3. 返回 `[]`

### 6.6 Row 类

```python
@dataclass
class Row:
    values: list
    columns: list[str]
    
    def __getattr__(self, name):
        if name in self.columns:
            return self.values[self.columns.index(name)]
        raise AttributeError(name)
    
    def __iter__(self):
        return iter(self.values)
    
    def __repr__(self):
        parts = ", ".join(f"{c}={v!r}" for c, v in zip(self.columns, self.values))
        return f"Row({parts})"
    
    def __eq__(self, other):
        return isinstance(other, Row) and self.columns == other.columns and self.values == other.values
```

---

## 7. Python API 详解

```python
# tinydb/database.py
class Database:
    def __init__(self, path: str | PathLike | Literal[':memory:'] = ':memory:'):
        """非 ACID，无 crash safety。path=':memory:' 时数据不写盘。"""
        self.pager = Pager(path)
        self.catalog = Catalog.from_bytes(self.pager.read_page(1))
        self.executor = Executor(self.pager, self.catalog)
    
    def execute(self, sql: str) -> list[Row]:
        tokens = tokenize(sql)
        statements = parse(tokens)
        results = []
        for stmt in statements.statements:
            rows = self.executor.execute(stmt)
            if isinstance(rows, list):
                results.extend(rows)  # multi-statement 返回最后一个 SELECT 的 rows
        return results
    
    def __enter__(self): return self
    def __exit__(self, *a):
        self.pager.flush(); self.pager.close()
    
    def close(self):
        self.pager.flush(); self.pager.close()
```

**错误映射**：
```python
try:
    return self.executor.execute(stmt)
except ParseError as e:
    raise tinydb.errors.ParseError(e.line, e.col, e.msg) from e
except KeyError as e:
    raise tinydb.errors.ExecutionError(f"table referenced does not exist") from e
```

`Database` 不暴露 `begin/commit/rollback`，文档明示 MVP 不支持事务。

---

## 8. 测试策略（4 层金字塔落地）

### 8.1 Per-Scenario Pytest

每个 spec Scenario 对应一个 pytest 函数，命名 `test_<scenario_name_snake_case>`，且加 `@pytest.mark.spec_id("REQ-<capability>-<num>-SCN-<num>")`。

例：
```python
# tests/unit/test_storage_engine.py
@pytest.mark.spec_id("REQ-STORAGE-001-SCN-01")
def test_create_new_db_writes_magic_header(tmp_path):
    db_path = tmp_path / "test.db"
    Database(str(db_path)).close()
    with open(db_path, 'rb') as f:
        magic = f.read(8)
    assert magic == b'TINYDB\x00'
```

4 spec files 的 93 个 Scenario → 93 个 test 函数。

### 8.2 Integration 套件

每 capability 一组：
- `test_parser_executor_roundtrip.py`：tokenize → parse → execute，验证集成
- `test_storage_page_chain.py`：catalog + pager + slotted_page，验证多页管理
- `test_full_sql_lifecycle.py`：端到端 CREATE → INSERT → SELECT → DELETE → 重新 open 验证持久化

### 8.3 E2E Golden SQL

```
tests/e2e/sql/
├── happy_path/
│   ├── 01_create_insert_select.sql
│   ├── 01_create_insert_select.expected.txt
│   ├── 02_multi_table.sql
│   ├── 02_multi_table.expected.txt
│   └── ...
├── error_cases/
│   ├── 01_unknown_table.sql
│   ├── 01_unknown_table.expected.txt
│   └── ...
```

每 `.sql` 由 `tests/e2e/conftest.py::run_sql(db, sql_file, expected_file)` 跑：
1. 拆分 SQL 文件语句（按 `;` 分割）
2. 对每个 stmt 调 `db.execute(stmt)`，收集 stdout/stderr
3. 与 expected.txt 字节对比

MVP 计划 12-15 个 golden file。

### 8.4 Property-Based Tests

`tests/property/test_storage_invariants.py`：
```python
from hypothesis import given, settings, seed
import hypothesis.strategies as st

@seed(20260715)
@settings(max_examples=200)
@given(st.lists(st.tuples(st.sampled_from(['INSERT', 'DELETE']), st.text(max_size=200)), max_size=50))
def test_storage_scan_equals_python_mirror(operations):
    db = Database(':memory:')
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    mirror: dict[tuple, None] = {}
    for op, payload in operations:
        if op == 'INSERT':
            db.execute(f"INSERT INTO t VALUES (1, '{payload}')")
            mirror[(1, payload)] = None
        elif op == 'DELETE' and mirror:
            db.execute("DELETE FROM t WHERE id = 1")
            mirror.clear()
    rows = db.execute("SELECT * FROM t")
    assert sorted([(r.id, r.name) for r in rows]) == sorted(mirror.keys())
```

`tests/property/test_parser_robustness.py`：hypothesis 生成随机字符串，`tokenize(parse(x))` 至多抛 `TokenError` / `ParseError`，不能抛未捕获的系统异常。

### 8.5 覆盖率目标

`pyproject.toml` 设置 `pytest --cov-fail-under=85`：
- 类型系统 ≥ 95%
- 解析器 ≥ 90%
- 存储引擎 ≥ 90%
- Executor ≥ 90%
- Database API ≥ 85%

---

## 9. Spec Patch 清单（待回写 OpenSpec delta spec）

实施前必须回写：

### Patch 1：`specs/storage-engine/spec.md`

**ADDED Requirements**：

#### Requirement: Overflow row spans multiple pages
The system SHALL allow rows whose encoded size exceeds MAX_INLINE_PAYLOAD by storing them across a chain of pages, where the first page slot is marked SPILL_START and points to subsequent overflow pages containing remaining chunks.

**Scenarios**：
- Insert row larger than MAX_INLINE_PAYLOAD spills
- Read spill-start reconstructs full row
- Delete spill-start frees overflow chain

**MODIFIED Scenario**（防 JSON int 精度）：
- `#### Scenario: Catalog schema encoded as JSON with INT-as-string` — 把现有 "Register new table updates catalog" Scenario 拆为两个，新 Scenario 强调 INT 字段的 JSON string 编码约定

### Patch 2：`specs/python-api/spec.md`

无（已含 Row.__repr__ Scenario）

### Patch 3：`specs/type-system-basic/spec.md`

无（已含 inf/NaN raise Scenario）

### Patch 4：`specs/sql-minimal-parser/spec.md`

无（已含所有 MVP 语句的 Scenario）

---

## 10. 已解决的 Open Questions

| # | 问题 | 答案 |
|---|------|------|
| Q1 | Catalog 编码 | JSON |
| Q2 | FLOAT inf/NaN | raise ValueError |
| Q3 | Row __repr__ | `Row(id=1, name='alice')` |
| Q4 | execute 返回 | 统一 list[Row] |
| Q5 | TDD 颗粒度 | Per-scenario 1:1 |
| Q6 | 溢出策略 | 仅 spill overflow 行 |

---

## 11. 风险与缓解（合并 R1-R9）

| # | 风险 | 缓解 |
|---|------|------|
| R1 | mmap + 进程崩溃数据丢失 | docstring 明示 "non-ACID, no crash safety"；MVP 不承担 |
| R2 | 4KB page 对超大行 | Overflow chain 处理（已采用 Q6） |
| R3 | 单页 catalog 表数限制 | MVP 演示量级够；`CatalogFull` 清晰错误 |
| R4 | 解析器无错误恢复 | 报错清晰；recovery ROI 低 |
| R5 | strict mode 拒绝 `'5'` 比较 INT | README "MVP Limitations" 段说明 |
| R6 | linear scan only | 性能在 engine-v2 用 B-tree 解决 |
| R7 | 溢出链表读写 bug | property test 随机 1KB-10KB 行反复 INSERT/SELECT/DELETE；溢出链断 → `RowCorruptedError` |
| R8 | Catalog JSON int 精度 | INT 在 catalog JSON 用 string 序列化（Spec Patch §9） |
| R9 | ~93 个 per-scenario 测试维护 | `pytest.mark.spec_id` 标记，CI 可按 spec 节点选跑 |

---

## 12. 不在本 Design 范围

- 任何 ACID 语义（WAL / shadow paging）
- UPDATE 语句及其实现
- WHERE AND/OR/IN/LIKE
- ORDER BY / LIMIT / OFFSET
- 聚合 COUNT/SUM/AVG + GROUP BY
- B-tree / 哈希索引
- PRIMARY KEY / NOT NULL / UNIQUE 列约束
- VARCHAR / CHAR / DECIMAL / DATE / TIME / TIMESTAMP / SMALLINT / BIGINT
- CLI / REPL
- 并发安全
- JOIN / 视图 / 触发器 / ALTER TABLE / 外键

以上留 `tinydb-acid` 与 `tinydb-engine-v2` 后续 change。
