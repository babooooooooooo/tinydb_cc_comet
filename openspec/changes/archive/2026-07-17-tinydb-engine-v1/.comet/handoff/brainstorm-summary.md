# Brainstorm Summary

- Change: tinydb-engine-v1
- Date: 2026-07-16

## 上下文

OpenSpec 已落 `proposal.md` + `design.md` + `tasks.md` 三件套。用户在主会话确认 batch 1 共 6 个 change 切分；本 change 是 batch 1 第一项，scope 完全锁定。本 design 阶段不重写 proposal/spec，只做实现级细化（AST 节点、语法、算法、错误处理、测试矩阵）。

OpenSpec handoff：

- 设计交接包：`openspec/changes/tinydb-engine-v1/.comet/handoff/design-context.md`
- 机器索引：`openspec/changes/tinydb-engine-v1/.comet/handoff/design-context.json`

## 确认的技术方案（来自 open-stage design.md，D1-D5 已锁定）

### D1 — WHERE 用表达式树，不用 boolean flags

- **AST 新增** `AndExpr / OrExpr / NotExpr` 节点，`where` 字段类型从 `Optional[tuple]` 升级为 `Optional[Expr]`。
- **评估器** `eval_expr(expr, row, schema) -> bool`，递归 AND/OR/NOT。
- **理由**：boolean flag 路线（`and_flag / or_flag`）在引入 NOT 后立刻退化；表达式树与 MVP 的 `EqualsExpr` 同构，可一致地扩展到 LIMIT/OFFSET 之后阶段的子查询/IN 列表。
- **关键 trade-off**：表达式树 vs boolean flags — 表达式树多一个 dataclass，但 evaluator 是纯递归，可被单测全表驱动；boolean flags 路径只需 1 行 if，但把语义信息压扁，未来难扩展。
- **失败回退**：若 NOT 优先级问题争议 → 改 Pratt parser；本 change 范围内不引入。

### D2 — UPDATE 无事务，in-place + delete/insert fallback

- **路径**：`scan → filter by eval_expr → apply sets → encode → try `SlottedPage.update` → fallback `delete + insert`（同扫描内进行）`。
- **正确性**：fallback 期间写入到原 slot + 新 slot，可能造成 `num_slots > MAX_SLOTS` 但 MVP 不阻止；fallback 写完后单页 flush，crash 中途状态由 `tinydb-acid` 单独兜底。
- **受影响行数**：返回 `[]`（与现有 DML 协议一致），受影响行数通过 stdout/e2e 计数而不是 SELECT 副作用呈现（spec REQ-DML-008 已规定）。
- **理由**：本 change 不与 ACID 耦合；引入事务会推迟所有上游 capability。

### D3 — 表达式 strict type 不放松

- **WHERE 右值**：literal 必须与列类型严格一致；类型不匹配抛 `ExecutionError("type mismatch: X vs Y")`。
- **SET 右值**：literal 必须与目标列类型严格一致。
- **理由**：与 MVP strict typing 一致；任何隐式转换（如 INT→TEXT 拼接、`'1'`→1）都推迟到 `tinydb-types`。

### D4 — Python stable sort + slot_id 次键

- **算法**：`sorted(rows, key=lambda r: sort_key(r, items, schema))`，`sort_key` 返回元组 `(value, slot_id)`，slot_id 保证稳定，DESC 取负。
- **复杂度**：O(n log n)，n ≤ 10k 行（MVP scope 内可接受；索引推迟到 `tinydb-engine-v2`）。
- **理由**：Python `sorted` 已稳定；自实现排序会引入 bug；归并排序对 10k 行不必要。

### D5 — ORDER BY 解析后只读

- **AST freeze**：`Select` dataclass `frozen=True`，`order_by` 字段在解析后不再 lazy rewrite。
- **理由**：可变性会引入并发与可重入问题；frozen 与现有 MVP AST 风格一致。
- **隐含后果**：`executor.py` 不可对 `Select.order_by` 做 in-place 调整（即使是为了下推优化）。

## 关键取舍与风险

| 风险 | 取舍 | Mitigation |
|------|------|-----------|
| **R1** 解析器扩展破坏现有 SELECT/INSERT/DELETE 路径 | 增加节点 + 关键字而不改 dispatcher | MVP 既有 234 个测试必须全绿（tasks §8.1） |
| **R2** UPDATE 撞 PageFull 时 delete/insert 中途崩溃 | 不引入事务 | 文档披露到 `docs/MVP_LIMITATIONS.md`（change 不实施，仅文档）；`tinydb-acid` 单独兜底 |
| **R3** AND/OR 短路语义错 | 严格按"AND 遇 False 即返回，OR 遇 True 即返回" | 单元测试显式构造 `False OR (eval raises)`、`True AND (eval raises)`，验证 raise 不发生 |
| **R4** 关键字加入后旧脚本 `update` 列名变关键字 | tokenizer 已识别 IDENT；只要不再用作列名即 OK | 单元测试显式覆盖 `CREATE TABLE t(update INT)` 拒绝；e2e golden 加 1 条负面 |
| **R5** ORDER BY 类型不可比（FLOAT vs INT） | 列类型必须严格一致；走 strict type 系统 | `validate_compare` 已存在；评估器优先 `db_to_py` 后比 |
| **R6** LIMIT/OFFSET 与负数/0 | 显式校验非负；OFFSET 单独存在也允许（语义：跳过前 N） | 解析时抛 ParseError；executor 端再次校验 |
| **R7** UPDATE 跨页 fallback 时 catalog next_page_id 与已分配的 page 不一致 | 复用现有 `_insert_row_into_chain`，catalog flush 仅在 PageFull+chain 推进时发生 | 与 INSERT 路径同源；行为一致 |
| **R8** 排序时 None 排尾 + BOOL falsy 不算 NULL | strict type 路径下不会出现 None | evaluator 在 strict type 失败前 early return；显式断言 test_order_by_no_nulls |
| **R9** 模块行数膨胀 | parser ≤ 750、executor ≤ 520 | tasks §8.3 显式 grep 行数 |

## 测试策略

| 测试文件 | 类型 | 覆盖 |
|---------|------|------|
| `tests/unit/test_engine_v1_parser.py` | unit | AST roundtrip、关键字一对一测、AND/OR/NOT 优先级真值表、UPDATE SET 多列、ORDER BY 多键、LIMIT/OFFSET 边界 |
| `tests/unit/test_engine_v1_executor.py` | unit | `eval_expr` 真值表、AND/OR 短路、UPDATE in-place vs grow fallback、sort 稳定、sort key tuple、DESC 取负、LIMIT 0/OFFSET > rows |
| `tests/integration/test_engine_v1.py` | integration | UPDATE 端到端（无 WHERE / 有 WHERE / 复合 WHERE）、UPDATE 增长碰撞 page chain、SELECT 链式 ORDER+LIMIT+OFFSET、复合 WHERE 多页跨 slot、REOPEN 持久化 UPDATE 后状态 |
| `tests/e2e/sql/engine_v1/*.sql` | e2e golden | 12 条新增 SQL（UPDATE basic、UPDATE 多列、UPDATE 复合 WHERE、SELECT 复合 WHERE、ORDER BY 单键 ASC/DESC、ORDER BY 多键、LIMIT/OFFSET 链式、UPDATE 持久化、UPDATE 错误列名） |

合并覆盖率门槛 ≥90%（项目级）；变更模块（parser + executor + tokenizer）100%。

## Spec Patch

无。open 阶段 spec 已覆盖三个新 capability：`sql-update-statement`、`sql-where-combinators`、`sql-select-order-limit`，scenario 与验收点齐备。本设计阶段探查到的细节（排序 None 排尾、UPDATE 返回 `[]` 与受影响行数呈现路径、short-circuit evaluator 实现）都属于实现侧细化，不改 spec 语义。

如 build 阶段发现 spec 验收场景遗漏（例如 UPDATE 跨页 fallback 的具体字节布局），由 build 阶段回写 Spec Patch。

## Open Questions Resolved（brainstorming 阶段确认）

| ID | 问题 | 决定 |
|----|------|------|
| Q1 | 表达式树 vs boolean flags | **D1：表达式树**（open-stage design.md 已确认） |
| Q2 | UPDATE 失败回滚策略 | **D2：无事务 + in-place + delete/insert fallback**（open-stage design.md 已确认） |
| Q3 | 表达式类型严格性 | **D3：strict type 不放松**（open-stage design.md 已确认） |
| Q4 | ORDER BY 排序算法 | **D4：Python stable sort + slot_id 次键**（open-stage design.md 已确认） |
| Q5 | ORDER BY 解析后只读 | **D5：AST frozen，executor 不重写**（open-stage design.md 已确认） |
| Q6 | SELECT chain 顺序（filter → order → offset → limit） | **filter → order → offset → limit**（先排后切，避免 offset 跳过无意义数据） |
| Q7 | SET 右值是否允许表达式 | **本 change 不允许**（与 `tinydb-engine-v1` scope 不冲突；语义推迟到 `tinydb-engine-v2`） |
| Q8 | WHERE 中是否允许 AND/OR/NOT 与 = 混合而不带括号 | **允许**：优先级表显式 OR < AND < NOT < primary < comparison |
| Q9 | UPDATE 是否允许无 SET 子句 | **不允许**：parser 显式要求至少一个 SET assign |
| Q10 | LIMIT 0 / OFFSET 0 / OFFSET > 行数 | **允许**：LIMIT 0 返回 `[]`；OFFSET > 行数返回 `[]`；OFFSET 0 等同不偏移 |
| Q11 | executor `where` 字段向后兼容 | **MVP 旧 `where` tuple 路径在 build 阶段被替换为 `eval_expr`**：parser 升级后 executor 必须同步升级 |

## 下一步

1. 创建实现级 Design Doc：`docs/superpowers/specs/2026-07-16-tinydb-engine-v1-design.md`
2. 更新 `.comet.yaml` 的 `design_doc` 字段
3. 重新生成 handoff 并运行 design guard → 推进 phase 至 build