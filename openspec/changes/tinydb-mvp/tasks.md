# Tasks: tinydb-mvp

> **实施起点**：所有任务基于 `proposal.md` + `design.md` + `specs/*.md` 已确认产物。
> **TDD 模式**：全任务采用 Test-Driven Development（`tdd_mode: tdd`），每个任务遵循 "红→绿→重构" 循环。
> **预算红线**：模块行数上限见 `proposal.md` "Impact" 段落；任何任务实施后超过 = 违反 MVP 教学定位，需拆分子任务。

## 1. 项目骨架与配置

- [x] 1.1 创建 `src/tinydb/` 与 `tests/` 目录，编写 `pyproject.toml`（声明 `tinydb` 包名、零运行时依赖、dev 依赖 `pytest>=7`、`hypothesis>=6`）
- [x] 1.2 编写 `src/tinydb/__init__.py`，导出 `Database`、`Row`、`__version__ = "0.1.0"` 与异常类 `ParseError`、`ExecutionError`、`InvalidDatabaseFile`、`UnsupportedSchemaVersion`
- [x] 1.3 编写 `README.md`（说明 MVP 范围、非 ACID 警示、快速开始示例）
- [x] 1.4 编写 `pytest.ini`（或 `pyproject.toml` 中 `[tool.pytest.ini_options]`），启用 strict markers
- [x] 1.5 创建空模块占位文件：`type_system.py`、`pager.py`、`slotted_page.py`、`catalog.py`、`tokenizer.py`、`parser.py`、`executor.py`、`database.py`、`errors.py`，每个写好 docstring 声明模块职责

## 2. 类型系统（spec: type-system-basic）

- [ ] 2.1 编写 `tests/unit/test_type_system.py`，红：覆盖 `specs/type-system-basic/spec.md` 中所有 Scenario 用例
- [x] 2.2 实现 `type_system.py::encode_int / decode_int`（8-byte big-endian），绿：跑通 INT roundtrip + OverflowError
- [x] 2.3 实现 `type_system.py::encode_text / decode_text`（length-prefixed UTF-8），绿：跑通 TEXT roundtrip + UnicodeEncodeError
- [x] 2.4 实现 `type_system.py::encode_bool / decode_bool`（1 字节 0/1），绿：跑通 BOOL roundtrip
- [x] 2.5 实现 `type_system.py::encode_float / decode_float`（`struct.pack('>d', v)`），绿：跑通 FLOAT roundtrip
- [x] 2.6 在 `tokenizer.py` 中实现 4 个字面量识别（`parse_int_literal`、`parse_float_literal`、`parse_text_literal`、`parse_bool_literal`），绿：跑通 4 类型字面量解析 + NaN/Inf 拒绝
- [x] 2.7 实现 `type_system.py::py_to_db(value, column_type)` 与 `db_to_py(bytes, column_type)`，绿：跑通所有 Python ↔ DB 转换用例（含 float NaN 拒绝、float→INT 拒绝）
- [x] 2.8 实现 `type_system.py::validate_compare(col_value, lit_value)` 用于 executor 严格类型守卫，绿：跑通 strict type coercion rejection 用例

## 3. 存储引擎 · Pager 层（spec: storage-engine, file/page 部分）

- [x] 3.1 编写 `tests/integration/test_pager.py`，红：覆盖文件创建、magic 校验、版本校验、`:memory:` 模式、page alloc/read/write 行为
- [x] 3.2 实现 `pager.py::Pager` 类，接受 path 或 `:memory:`，初始化时按需 open 或 create
- [x] 3.3 实现 page 0 文件头：`MAGIC = b'TINYDB\x00\x01'` + `SCHEMA_VERSION = 0x01`；open 已有文件时强制 magic 校验，绿：跑通 magic / version 异常用例
- [ ] 3.4 实现 `_path_for(page_id)` 与 `read_page(page_id)` / `write_page(page_id, bytes)` / `alloc_page()`，文件 backed 模式用 `mmap`，`:memory:` 用 bytearray 模拟，绿：跑通 page addressing 用例
- [ ] 3.5 实现 `Pager.close()` 释放 mmap 与文件句柄；`Database.__exit__` 中调用

## 4. 存储引擎 · Slotted Page + 行编码（spec: storage-engine, page layout 部分）

- [ ] 4.1 编写 `tests/unit/test_slotted_page.py`，红：覆盖 insert / update / tombstone / slot reuse / null bitmap / page full
- [ ] 4.2 实现 `slotted_page.py::SlottedPage` 数据类，持有 `page_id`、`num_slots`、`free_offset`、`slots: list[Slot]`、`data: bytearray`
- [ ] 4.3 实现 `SlottedPage.from_bytes(page_id, bytes)` 与 `to_bytes()` 序列化格式，绿：跑通 roundtrip
- [ ] 4.4 实现 `SlottedPage.insert(row_bytes)`：tombstone 优先复用，否则 append 到末尾；返回 slot id；满则 raise `PageFull`，绿：跑通 insert 用例
- [ ] 4.5 实现 `SlottedPage.delete(slot_id)`：标记 tombstone（offset=0xFFFF），绿：跑通 tombstone 用例
- [ ] 4.6 实现 `SlottedPage.update(slot_id, row_bytes)`：同长或更短则在原位覆盖，否则 raise，绿：跑通 in-place update 用例
- [ ] 4.7 实现 `SlottedPage.get(slot_id)`：返回解码后的字节或 None（tombstone）
- [ ] 4.8 在 `type_system.py` 或新文件 `row_codec.py` 中实现 `encode_row(values, schema)` 与 `decode_row(bytes, schema)`，含 null bitmap，绿：跑通 row encoding 用例

## 5. 存储引擎 · Catalog（spec: storage-engine, catalog 部分）

- [ ] 5.1 编写 `tests/integration/test_catalog.py`，红：覆盖 register / lookup / persist across reopen / drop
- [ ] 5.2 实现 `catalog.py::Catalog` 数据类：`tables: dict[name, TableInfo]`，`TableInfo = (schema, root_page_id, next_page_id)`
- [ ] 5.3 实现 `Catalog.from_bytes(page1_bytes)` / `to_bytes()`：序列化为 JSON（候选 Q1，MVP 优先 JSON）
- [ ] 5.4 在 Pager 中预留 page 1 给 catalog，新增表时 alloc 一个 page 作为 root_page，落盘在 page 1
- [ ] 5.5 实现 `Catalog.create_table(name, schema)` / `drop_table(name)` / `get_table(name)`，绿：跑通 catalog 用例

## 6. SQL · Tokenizer（spec: sql-minimal-parser tokenizer 部分）

- [ ] 6.1 编写 `tests/unit/test_tokenizer.py`，红：覆盖 identifier / keyword / int / float / text literal（含 doubled single-quote）/ boolean / punctuation / position tracking / TokenError
- [ ] 6.2 实现 `tokenizer.py::tokenize(sql)` 主循环：跳过空白、跟踪 line/col、按字符分类（alpha→identifier or keyword、digit→number、'→text、字母 T/F→bool）
- [ ] 6.3 实现关键字字典（CREATE / TABLE / DROP / INSERT / INTO / VALUES / SELECT / FROM / WHERE / TRUE / FALSE / INT / TEXT / FLOAT / BOOL），绿：跑通 keyword 大小写不敏感用例
- [ ] 6.4 实现 integer / float / text literal 三种字面量解析，text 含 doubled-quote 转义，绿：跑通字面量用例
- [ ] 6.5 实现 boolean literal（识别 TRUE / FALSE token），连接到 type_system 的字面量拒绝逻辑，绿：跑通 bool literal
- [ ] 6.6 实现 punctuation（`( ) , ; = *`），绿：跑通 punctuation 用例
- [ ] 6.7 错误路径：`TokenError(line, col, message)`，绿：跑通 `@` 报 TokenError 用例

## 7. SQL · Parser（spec: sql-minimal-parser parser 部分）

- [ ] 7.1 编写 `tests/unit/test_parser.py`，红：覆盖 5 个语句的 AST 形状、column 重复、类型不支持、count mismatch、未支持操作符、ParseError 携带位置、StatementList 多语句
- [ ] 7.2 实现 `parser.py::parse(tokens)` 主入口：循环解析语句，分号分隔，返回 `StatementList`
- [ ] 7.3 定义 AST 数据类（`StatementList`、`CreateTable`、`DropTable`、`Insert`、`Select`、`Delete`），所有节点带 `line`、`col`
- [ ] 7.4 实现 `parse_create_table`：识别 `CREATE TABLE name (col TYPE, ...)`；重复列名检测；不支持类型 raise ParseError，绿：跑通 CreateTable 用例
- [ ] 7.5 实现 `parse_drop_table`：识别 `DROP TABLE name`；缺失表名 raise ParseError，绿：跑通 DropTable 用例
- [ ] 7.6 实现 `parse_insert`：识别 `INSERT INTO name (cols) VALUES (row), (row)`；列数不匹配 raise ParseError，绿：跑通 Insert 用例
- [ ] 7.7 实现 `parse_select`：识别 `SELECT * | cols FROM name [WHERE col = lit]`；不支持操作符 raise ParseError；缺失 FROM raise ParseError，绿：跑通 Select 用例
- [ ] 7.8 实现 `parse_delete`：识别 `DELETE FROM name [WHERE col = lit]`；WHERE 可选，绿：跑通 Delete 用例
- [ ] 7.9 解析器纯函数性质（同输入两次结果一致），绿

## 8. Executor（spec 跨 storage-engine row CRUD + sql-minimal-parser parse-then-execute）

- [ ] 8.1 编写 `tests/integration/test_executor.py`，红：覆盖 DDL/DML 在真 storage 上的完整流程、PageFull 时新页分配、tombstone 过滤、严格类型守卫在 execute 层抛 TypeError
- [ ] 8.2 实现 `executor.py::Executor(pager, catalog)` 类，入口 `run(stmt) -> list[Row]`
- [ ] 8.3 实现 `Executor._exec_create_table` / `_exec_drop_table`，落 catalog + alloc/dealloc root page
- [ ] 8.4 实现 `Executor._exec_insert`：定位表 root page，扫描到有空槽的页（满则 alloc 新页），调用 slotted_page.insert + row_codec.encode_row
- [ ] 8.5 实现线性扫描 helper：迭代表所有 data pages，过滤 tombstone，返回解码后的 `(slot_id, decoded_row)` 列表
- [ ] 8.6 实现 `Executor._exec_select`：扫描 + 类型校验 WHERE + 投影列；WHERE 类型不匹配 raise TypeError
- [ ] 8.7 实现 `Executor._exec_delete`：扫描 → 匹配 WHERE → 标记 tombstone

## 9. Python API（spec: python-api）

- [ ] 9.1 编写 `tests/integration/test_database_api.py`，红：覆盖 file-backed 持久化、`:memory:` 不写盘、context manager、execute 行为（SELECT/DDL/DML/multi-statement）、ParseError / ExecutionError 传播、Row 属性访问 / 迭代 / repr / 等价
- [ ] 9.2 实现 `database.py::Database` 类：`__init__(path)`、`__enter__`、`__exit__`、`close()`、`execute(sql)`
- [ ] 9.3 实现 `Row` 数据类：`__init__(values, columns)`、`__getattr__`、`__iter__`、`__repr__`、`__eq__`、`__iter__` schema 顺序
- [ ] 9.4 在 `Database.execute` 内串联：tokenize → parse → executor.run → 包装为 list[Row] 或 []
- [ ] 9.5 错误映射：parser 抛 `ParseError` 时重新 raise 为 `tinydb.errors.ParseError`（保持兼容）；executor 抛 `KeyError(no such table)` 等转为 `tinydb.errors.ExecutionError`
- [ ] 9.6 MVP 限定保证：`Database` 类**不实现** `begin` / `commit` / `rollback`（留给 tinydb-acid），并在 docstring 明确声明

## 10. 端到端 SQL 测试集 + 属性测试

- [ ] 10.1 创建 `tests/e2e/sql/` 目录与 golden 文件：编写 12-15 个 `.sql` + 对应 `.expected.txt` 的 SQL 场景（CREATE / INSERT / SELECT * / SELECT cols / SELECT WHERE / DELETE 全表 / DELETE WHERE / 多语句 / 错误用例各一例）
- [ ] 10.2 实现 `tests/e2e/conftest.py` 提供 `run_sql(db, sql_file)` helper，对比 stdout / stderr 与 golden 文件
- [ ] 10.3 编写 `tests/property/test_storage_invariants.py`：用 hypothesis 生成随机 INSERT/DELETE 序列，断言"扫描结果 == 由 Python 镜像维护的逻辑视图"
- [ ] 10.4 编写 `tests/property/test_parser_robustness.py`：用 hypothesis 生成随机字符串输入，断言 tokenizer / parser 不抛未捕获异常（可抛 ParseError / TokenError，但不能误抛系统异常）

## 11. 文档与可演示脚本

- [ ] 11.1 在 README 中补充"模块导览"段，链接每个模块并标注预期行数
- [ ] 11.2 编写 `examples/demo.py`：从打开 → 建表 → 插入 → 查 → 关闭的端到端最小演示，README "快速开始" 段引用此脚本
- [ ] 11.3 编写 `docs/MVP_LIMITATIONS.md`：列出 MVP 已知约束（非 ACID、单进程、固定 4KB 页、单页 catalog、strict 类型等）

## 12. 验收前检查

- [ ] 12.1 全测试套件通过（`pytest`），覆盖率 ≥ 85%（`pytest --cov=tinydb --cov-report=term-missing`）
- [ ] 12.2 行数审计：grep 行数与 proposal Impact 段模块预算上限对照，每个模块不超过预算
- [ ] 12.3 运行 `openspec validate tinydb-mvp --strict` 应通过
- [ ] 12.4 运行 `examples/demo.py`，人眼确认输出符合预期
- [ ] 12.5 把 `open guard --apply` 至通过，进入 design 阶段前的最后清场（由 /comet-build 调度承担）
