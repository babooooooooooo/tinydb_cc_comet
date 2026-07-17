# Tasks: tinydb-constraints

> **TDD 模式**：每个任务遵循"红 → 绿 → 重构"。

## 1. Catalog Schema 升级

- [x] 1.1 编写 `tests/unit/test_catalog_column.py::test_column_dataclass_*`，红
- [x] 1.2 在 `src/tinydb/catalog.py` 定义 `Column` dataclass（name / type / nullable / unique / primary_key）
- [x] 1.3 升级 `TableInfo` 使用 `tuple[Column, ...]`；`from_bytes` / `to_bytes` 序列化包含新字段
- [x] 1.4 编写 `test_catalog_roundtrip_with_constraints`，绿（约束往返一致）

## 2. Parser：列约束

- [x] 2.1 编写 `tests/unit/test_constraints_parser.py::test_create_table_primary_key_unique_not_null`，红
- [x] 2.2 在 `parser.py::parse_create_table` 接入 `NOT NULL` / `UNIQUE` / `PRIMARY KEY` 子句链
- [x] 2.3 在 `tokenizer.py` 增加 `PRIMARY` / `KEY` / `NOT` / `NULL`（限 column_def 上下文）；`UNIQUE` 不需要新增关键字（已存在 keyword 表）

## 3. Parser：`NULL` 字面量（INSERT 上下文）

- [x] 3.1 编写 `test_insert_accepts_null_literal_when_column_nullable`，红
- [x] 3.2 在 INSERT 解析路径识别 `NULL` 字面量为 `Literal(None)`（限 INSERT VALUES 上下文）
- [x] 3.3 编写 `test_insert_rejects_null_for_pk`，红（PK 列写 NULL 时 executor 抛错，由 Task 5 覆盖）

## 4. Executor：INSERT 校验顺序

- [ ] 4.1 编写 `tests/unit/test_constraints_executor.py::test_insert_rejects_null_on_not_null`，红
- [ ] 4.2 在 `execute_insert` 加 NOT NULL 校验（在类型校验后落盘前）
- [ ] 4.3 编写 `test_insert_rejects_duplicate_unique_key`，红
- [ ] 4.4 实现 UNIQUE 单列 + 复合键校验（全表扫描）
- [ ] 4.5 编写 `test_insert_rejects_duplicate_primary_key`，红（PRIMARY KEY 走同一路径）
- [ ] 4.6 实现 PRIMARY KEY 等价 NOT NULL + UNIQUE 合并检查（executor 内对 PK 列同时跑 null 和 unique 校验）

## 5. 异常类型

- [x] 5.1 编写 `test_constraint_violation_includes_kind_column_value`，红
- [x] 5.2 在 `errors.py` 新增 `ConstraintViolation(kind, column=None, columns=None, value)` 继承 `ExecutionError`
- [ ] 5.3 在 REPL/CLI 路径上把 `ConstraintViolation` 渲染为单行 `ERROR: ConstraintViolation(kind=..., column=...)`

## 6. 兼容性

- [ ] 6.1 编写 fixture：MVP 旧版 `.db`（无约束 schema）反序列化路径不能爆
- [x] 6.2 编写 `test_catalog_old_file_migration_loads_with_nullable_default_true`，绿
- [ ] 6.3 验证：MVP 234 个测试 + engine-v1 后续测试全部继续通过

## 7. 性能与回归

- [ ] 7.1 计时 fixture：n=1000 行 INSERT 全部通过 O(n) UNIQUE 校验总耗时 < 100ms
- [ ] 7.2 模块行数回归：`parser.py ≤ 750`、`executor.py ≤ 620`、`catalog.py ≤ 130`
- [ ] 7.3 覆盖率 ≥ 90% across project；新代码 100%
- [ ] 7.4 `docs/MVP_LIMITATIONS.md` 增补：本 change 交付后 O(n) UNIQUE 校验仍生效；索引化留 `tinydb-engine-v2`
