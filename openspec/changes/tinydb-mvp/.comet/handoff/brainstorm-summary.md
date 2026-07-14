# Brainstorm Summary

- Change: tinydb-mvp
- Date: 2026-07-15
- Mode: 设计阶段增量检查点（用于压缩恢复），非 Design Doc

---

## 已确认的设计方案（用于 Design Doc 落地）

### Q1 - Q4（来自 design.md Open Questions）

| # | 问题 | 确认 |
|---|------|------|
| Q1 | Catalog page 1 编码格式 | **JSON**（可读 + stdlib 友好） |
| Q2 | FLOAT inf/NaN 行为 | **raise ValueError**（strict mode 风格） |
| Q3 | Row.__repr__ 格式 | **`Row(id=1, name='alice')`**（仿 dataclass） |
| Q4 | execute() 返回类型 | **统一 list[Row]**（DDL/DML 也返回 []） |

### Q5 - Q6（来自 brainstorming 第二批）

| # | 问题 | 确认 |
|---|------|------|
| Q5 | TDD pytest 颗粒度 | **Per-scenario 1:1**（spec Scenario ↔ pytest 函数，红-绿颗粒最细） |
| Q6 | 单行超 4KB 行为 | **只 spill overflow 行**（普通 page full → executor alloc 新页；单行超大 → 跨页 overflow chain） |

### Q6 spill 设计的实施细节

- MAX_INLINE_PAYLOAD = PAGE_SIZE - HEADER_SIZE - SLOT_DIR_OVERHEAD ≈ 3500 bytes
- SlottedPage 内部不感知 spill —— 它的 `insert` 行为不变
- 在 Executor 层引入 `insert_row_with_overflow_handling(row_bytes)`：
  1. 尝试 slotted_page.insert；若 size <= MAX_INLINE_PAYLOAD，正常插入
  2. 若 size > MAX_INLINE_PAYLOAD → 切分多块
     - 第一块写入当前 page（slot 标记为 spill-start）
     - 后续块链式写入新分配的 overflow pages
- Page header 新增字段 `overflow_next_page_id`（仅当 slot 为 spill-start 时有意义，初始为 NULL_PAGE_ID）
- Overflow page 没有 slot directory，只存纯 chunk data + 链向下一个 overflow page
- Overflow 末端的 page `overflow_next_page_id = NULL_PAGE_ID` 标志链表终止

---

## Spec Patch 候选（需回写 OpenSpec delta spec）

待 brainstorm 完成后、用户批准 Design Doc 时一并回写：

1. **storage-engine spec** 新增 Requirement: `Overflow row splits across pages`
   - Scenario: Insert large text row that fills >3500 bytes
   - Scenario: Read back spans multiple pages (logical row reconstruction)
   - Scenario: Delete spill-start slot also frees overflow chain

2. **type-system-basic spec** 现有 Scenario "Parse NaN/Inf" + "Reject NaN in float" 已经覆盖（已确认 raise ValueError），无需 Spec Patch

3. **python-api spec** 现有 Scenario "Row __repr__" 已写好（`Row(id=1, name='alice')`），无需 Spec Patch

4. **catalog encoding** 现有 Scenario "Catalog at page 1" 未指定 JSON vs binary → Spec Patch:
   - 现有 Scenario "Register new table updates catalog" 加上：使用 `json.dumps` 编码（schema encoding 失败 raise TypeError）

---

## 测试策略（test_pyramid + TDD）

- **Unit**: 每个 spec Scenario → 一个 pytest 函数（per-scenario 1:1）
- **Integration**: tokenizer → parser → executor → pager → slotted_page 链路 e2e（每 capability 一个 integration 套件）
- **E2E (golden SQL)**: tests/e2e/sql/*.sql + *.expected.txt，验证 SQL 字符串级行为
- **Property (hypothesis)**:
  - `test_storage_invariants`: 随机 INSERT/DELETE 序列 → 扫描结果必须 ≡ Python 镜像维护的逻辑视图
  - `test_parser_robustness`: 随机字符串输入 → 至多抛 ParseError/TokenError，不能抛系统异常
  - hypothesis seed 固定为 `seed=20260715`，CI 友好

---

## 模块边界（Design Doc 重点）

| 层 | 模块 | 入接口 | 出接口 | 依赖 |
|----|------|--------|--------|------|
| API | `database.py` | `Database(path)`, `execute(sql)` | `Database.execute -> list[Row]` | parser, executor, errors |
| 解析 | `tokenizer.py` | `tokenize(sql: str)` | `list[Token]` | type_system 字面量 |
| 解析 | `parser.py` | `parse(tokens)` | `ASTNode` (StatementList) | tokenizer |
| 执行 | `executor.py` | `Executor(pager, catalog)`, `run(ast)` | `list[Row]` | pager, catalog, row_codec, type_system |
| 存储 | `pager.py` | `Pager(path)`, `read_page/write_page/alloc_page` | `Pager` 实例 | stdlib only |
| 存储 | `slotted_page.py` | `from_bytes`/`to_bytes`/`insert`/`delete`/`update`/`get` | `SlottedPage` 实例 | - |
| 存储 | `catalog.py` | `Catalog.from_bytes/to_bytes/lookup/register/drop` | `Catalog` 实例 | json, type_system |
| 存储 | `row_codec.py` | `encode_row(values, schema)` / `decode_row(bytes, schema)` | `bytes` | type_system |
| 类型 | `type_system.py` | `encode_X/decode_X/py_to_db/db_to_py` | bytes / Python 值 | stdlib only |
| 错误 | `errors.py` | `class ParseError/ExecutionError/...` | exception classes | - |

---

## 风险与权衡（与 design.md 同步）

- **R7 溢出页复杂度**：[Risk] 溢出链表读写代码引入 bug → [Mitigation] 用 property-based 测试随机生成 1KB-10KB 行反复 INSERT/DELETE/SELECT 验证流；包含失败时强制 `RowCorruptedError` 检查 footer
- **R8 Catalog JSON 精度**：[Risk] JSON int 丢精度（如 64-bit INT 转 Python float）→ [Mitigation] Catalog INT 用 string 序列化（约定）；**Spec Patch**：storage-engine spec 添加 scenario "Catalog INT schema field encoded as JSON string"
- **R9 Per-scenario 测试文件膨胀**：[Risk] ~93 个测试维护成本增加 → [Mitigation] 用 `pytest.mark.spec_id` 标记与 spec Scenario ID 对应，便于反向追踪
