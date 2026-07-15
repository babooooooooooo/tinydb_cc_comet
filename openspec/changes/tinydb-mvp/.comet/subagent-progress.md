# Subagent 驱动开发 — 进度检查点

> 协调状态恢复用；不替代 plan / OpenSpec checkbox。

## 全局配置

- change: `tinydb-mvp`
- plan: `docs/superpowers/plans/2026-07-15-tinydb-mvp.md`
- review_mode: `thorough`（每任务 spec+quality reviewer，最多 2 轮修复；final 完整审查）
- tdd_mode: `tdd`
- isolation: `branch`
- language: `zh-CN`

## 当前 Task

- **plan task**: `### Task 20: Database + Row 类（tasks.md §9.1-9.3）`
- **openspec task**: `9.1-9.3 Database 类 + Row 数据类（替换 __init__.py placeholder per memory/task-20-placeholder.md）`
- **阶段**: `ready_to_dispatch`（Task 19 spec+quality 双审通过 + 6 type-hint MEDIUM + 1 空表 SELECT LOW NITs 全部修复 + plan §6.1 预算 governance 调整）
- **审查-修复轮次**: 0
- **依赖**: Task 19 完成 + Executor 完整（DDL + INSERT + scan + SELECT + DELETE + DRY `_resolve_where` + `_python_type_to_db_type`）+ plan §6.1 预算调整为 350 行

## 累积待办（记录，Task 6 或回归时统一处理）

- **Opportunistic 修缮队列**（非阻塞，可在 Task 9 Executor / Task 21 Overflow / archive 阶段统一补）:
  - Task 3 MINOR: `decode_text` 长度前缀截断分支缺针对性单测
  - Task 4 M2: `decode_bool`/`decode_float` 缺 decode 截断 `ValueError` 单测
  - Task 6 I-1: 补 `test_validate_compare_float_nan_rejected`（FLOAT NaN/Inf 哨兵分支缺单测，13 个探针已验证行为正确）
  - Task 6 I-2: `test_db_to_py_roundtrip_int` 补 `spec_id="REQ-TYPE-001-SCN-19"` + 注释
  - Task 6 I-3: 删除 `test_type_system.py:148` 行内 `import struct as _st`（与 line 2 全局 import 重复）

## 已完成 Task

- **Task 19: Executor — SELECT + DELETE（tasks.md §8.6-8.7）** — ✅ 已勾选（经历 1 轮 spec+quality 双审 + 1 轮 NIT fix + plan §6.1 预算 governance 调整）
  - implementer(DONE_WITH_CONCERNS, TDD RED→GREEN 9 测试, executor.py 311 行) → spec reviewer(✅ APPROVED_WITH_CONCERNS, 0 NEEDS_FIXES, 3 governance 建议) → 协调者 partial governance (plan §6.1 预算调整) → code quality reviewer(✅ APPROVED_WITH_NITS, 6 MEDIUM type-hint + 2 LOW) → fix subagent(DONE, 117 测试零破坏, executor.py 319 行)
  - **C-1 governance**: plan §6.1 executor.py 行数估算 200 → 实际 319 行，超 119 行。协调者决定**调整预算至 350 行**，proposal.md 硬上限 400 不变。**真实影响**: docstring ~80 行 + 防御性错误分支 ~40 行 + `_resolve_where` + `_python_type_to_db_type` helper 抽取贡献；非可压缩浪费，拆分反而增加复杂度。
  - **C-2 fix** (M1-M6 type-hints): commit `969b677` — 6 处 type-hint 补全（`_resolve_where` / `_exec_select` / `_scan_table` / `execute` Union / `_python_type_to_db_type` / 5 处本地变量），便于 Task 20 Row 包装 contract 显式化
  - **C-3 fix** (L1 空表 SELECT): commit `969b677` — `test_select_empty_table_returns_empty_list` 覆盖 SELECT 在空表上返 `[]` 的边界条件
  - 提交链: `58181c4`（实现 + 3 测试 + 311 行）+ `969b677`（6 type-hint + 1 测试 + 8 行）+ 本次（plan+tasks.md 勾选 + §6.1 预算 governance）
  - **Implementer 关键决策**:
    1. **`_resolve_where` helper 抽取**: DRY 消除 SELECT/DELETE 重复 ~15 行；签名 `(stmt_where, schema) -> Optional[tuple[int, str, str, Any]]` 返 4-tuple（idx, type, op, lit）
    2. **`_python_type_to_db_type` 模块级 helper**: 把 Python `bool/int/float/str` 映射到 `BOOL/INT/FLOAT/TEXT`，使错误消息对齐 DB type 名（spec 要求 `INT vs TEXT` 而非 `int vs str`）
    3. **`_exec_delete` 两阶段删除**: 先 `_scan_table` collect `(pid, sid)` to_delete，再 batch `page.delete(sid) + write_page`，最后 `flush()`（仅当 to_delete 非空）；避免 mid-scan state 变化
    4. **`_exec_select` 返回 `list[list]` 不含列名**: Task 20 Row 包装在 `Database.execute` 外层做（plan §9.4 串联）
    5. **WHERE TypeError vs ExecutionError 分工**: literal/列类型不匹配 → `TypeError`（spec §REQ-PARSE-005-SCN-04 强制）；schema-level 错误 → `ExecutionError`（表/列/操作符）
    6. **plan 错误消息矛盾修正**: plan step 1 测试断言 `match="INT vs TEXT"` (DB type) vs plan step 3 示范 `"INT vs str"` (Python type) 矛盾；implementer 按测试断言修正
  - **推迟到 opportunistic 队列**:
    - `_python_type_to_db_type` 是否迁 `type_system.py`（Task 20+ 视需要决定）
    - `_resolve_where` 返回 4-tuple 改 NamedTuple / dataclass 提升可读性
    - 错误消息分散（每个 `_exec_*` 重复 "table X does not exist" 字面量）→ 提取 `_ensure_table_exists(name)` helper
    - `name_to_idx` 每次 SELECT 重建（Task 21 schema-time cache）
    - DELETE 不复用 `_exec_select`（当前两阶段更显式、易调试，保留现状）
    - 跨页 SELECT WHERE 测试未覆盖（建议 Task 20 加 page-spanning test）

- **Task 18: Executor — INSERT + scan helper（tasks.md §8.4-8.5）** — ✅ 已勾选（经历 1 轮 spec+quality 双审 + 1 轮 NIT fix + governance run→execute 修复）
  - implementer(DONE, TDD RED→GREEN 6 测试, executor.py 190 行) → spec reviewer(⚠️ APPROVED_WITH_CONCERNS, 0 NEEDS_FIXES, 3 governance follow-ups) → 协调者 governance fix run→execute → code quality reviewer(✅ APPROVED_WITH_NITS, 1 HIGH-1 + 2 MEDIUM + 3 LOW) → fix subagent(DONE, 113 测试零破坏, executor.py 190 行)
  - **C-1 governance**: commit `a2fad94` — spec.md + tasks.md + design.md + handoff + context 中 `Executor.run(stmt)` → `Executor.execute(stmt)` 同步（5 文件 8 行）。**真实影响**: Task 17 implementer 沿用 plan 写法用 `execute` 而 spec/tasks 写 `run`，Task 19 implementer 可能困惑；协调者拍板"code wins"更新 spec/tasks 跟随代码（避免代码 churn）
  - **C-2 fix** (HIGH-1): commit `cb093f6` — 类型验证错误信息包含列名 `raise ExecutionError(f"column {_name}: {e}") from e`，便于多列 INSERT 调试
  - **C-3 fix** (MEDIUM-1): `typed` → `validated` 重命名（声明 + append + encode_row 调用 3 处），消除命名误导（实际值未转换）
  - **C-4 fix** (MEDIUM-2): 关键函数补 type annotations — `execute(self, stmt: object)` + `_insert_row_into_chain(self, ti: TableInfo, row_bytes: bytes) -> int` + `_scan_table(self, ti: TableInfo) -> list[tuple[int, list, int]]` + `from tinydb.catalog import Catalog, TableInfo`
  - 提交链: `60f81cc`（实现 + 2 测试）+ `a2fad94`（run→execute governance）+ `cb093f6`（3 NIT fix）+ 本次（plan+tasks.md 勾选）
  - **Implementer 关键决策**:
    1. **35 inserts 替代 plan 的 25**：plan §step 1 假设 200 字节 row 误导；实际 INT row ~9 字节，受 `MAX_SLOTS=32` 限制 → 35 行触发 slot 溢出 → 第二页 alloc
    2. **`pid += 1` 线性探测（MVP 简化）**: docstring L137-140 明示 `next_page_id == tail` 不变量；`Pager.alloc_page()` 保证单调递增，chain 连续
    3. **`_scan_table` 返 3-tuple `(slot_id, values, pid)` 而非 plan 写 2-tuple**: Task 19 DELETE 需要 `pid+sid` tombstone，SELECT 也需要 pid 投影
    4. **模块顶部 import 优于 plan lazy import**: PEP 8 偏好，运行时开销相同
    5. **`py_to_db` 仅副作用校验**: valid types 返 encoded bytes 丢弃
    6. **MVP 列名简化（plan §Task 18:2441 明示）**: `INSERT INTO t(col)` 中 col 被忽略，按 schema 顺序插入
  - **推迟到 opportunistic 队列**:
    - LOW-1 NamedTuple `ScannedRow` 提升类型可读性（+9 行 vs ~0 收益）
    - LOW-2 测试断言加消息
    - LOW-3 tuple unpacking 测试 `r[1]` → `for _, vals, _ in rows`
    - spec.md "Row CRUD executor operations" 加 MVP column-list-ignored 显式声明
    - plan §Task 18 注释 25 → 35 修正（防止后续 implementer 重复错误）

- **Task 17: Executor — DDL（CREATE/DROP）（tasks.md §8.1-8.3）** — ✅ 已勾选（经历 1 轮 spec+quality 双审 + 1 轮 review-fix + SCN governance 修复）
  - implementer(DONE, TDD RED→GREEN 2 测试, executor.py 107 行) → spec reviewer(⚠️ APPROVED_WITH_CONCERNS, 唯一红字 SCN-02 冲突 + SCN-04 无 spec 锚) → 协调者 governance fix → code quality reviewer(❌ NEEDS_FIXES, HIGH-1 test helper 冗余持久化 + MEDIUM-2 DDL 错误分支零覆盖 + MEDIUM-3 类型注解不全) → fix subagent(DONE, 4 测试, executor.py 109 行)
  - **SCN governance**: commit `2ab4138` — 扩展 `specs/storage-engine/spec.md` §"Catalog at page 1" 添加 Executor-driven SCN-04 (CREATE persists across reopen) + SCN-05 (DROP removes across reopen)；test_executor.py 标签 SCN-02 → SCN-04 + SCN-04 → SCN-05；plan §Task 17 line 2284/2293 同步。**真实影响**: test_catalog.py 因 `test_catalog_empty_roundtrip` 偏移 +1（SCN-02 实指 spec SCN-01 register），Task 17 plan 沿用旧映射导致冲突；用 spec 扩展而非标签重排，**保持 Task 12 历史报告不变**。
  - **C-1 fix**（reviewer HIGH-1）: commit `8ee22e3` — 测试 helper `_exec` 移除末尾 `pager.write_page(1, cat.to_bytes()) + pager.flush()`，让 SCN-04/05 持久化路径**真实**由 Executor 内部覆盖而非 helper 屏蔽
  - **C-2 fix**（reviewer MEDIUM-2）: 追加 `test_create_duplicate_table_raises` (SCN-04) + `test_drop_missing_table_raises` (SCN-05)，覆盖 DDL 两条 ExecutionError 防御分支
  - **MEDIUM-3 fix**: `__init__(pager: Pager, catalog: Catalog) -> None` + `execute(stmt) -> list` + 5 `_exec_*` 方法加 `-> list` 返回注解 + import Pager/Catalog 显式类型
  - 提交链: `0dd1ef7`（实现 + 2 测试）+ `2ab4138`（SCN governance）+ `8ee22e3`（review fix + 2 测试 + 类型注解）+ 本次（plan+tasks.md 勾选）
  - **Implementer 关键决策**:
    1. **Catalog 重复防御式 if-check** 而非 try/except ValueError（plan 示范写法），意图清晰
    2. **drop_table leak page**（注释明示 "Task 21 will reclaim"），保持 MVP 行为
    3. **DDL 返回 `[]`** 对齐 Database.execute `list[Row]` 契约，避免 Task 20 类型分支
    4. **占位用 NotImplementedError** 而非 plan 示范的 `...` (Ellipsis) — Task 18/19 接续 implementer 第一行替换即获清晰失败信号
    5. **dispatch 兜底 raise ExecutionError**(plan 未明示，低成本加固)，含 stmt 类型名便于诊断
  - **接受的 MINOR**: docstring "type hints deferred to hardening pass" 已撤回（MEDIUM-3 全部补齐）

- **Task 16: Parser — INSERT / SELECT / DELETE + StatementList（tasks.md §7.6-7.9）** — ✅ 已勾选（经历 1 轮 review + C-1/C-2 governance commits）
  - implementer(DONE_WITH_CONCERNS, TDD RED→GREEN, 16 测试, 369 行) → thorough reviewer(⚠️ APPROVED_WITH_CONCERNS, 0 Critical, **3 Important I-1/I-2/I-3 + 4 Minor M-1..M-4**)
  - **I-1 (M-3 AST 泛型收紧)** — docstring 注释妥协，运行时仍是裸 `list` / `Optional[tuple]`；实际 type hints 推迟到 Task 21 hardening pass 或 v2 比较运算符扩展窗口
  - **C-1 governance** (I-2): commit `305ebfd` — 扩展 `tokenizer.py:125` PUNCT 集从 `(),;=*` → `(),;=*<>` + 新增 `test_tokenize_punctuation_comparison_ops` 回归测试 + SCN-04 测试改回端到端 `tokenize()` 链路 + tasks.md §6.6 punctuation 列表同步。**真实影响**: spec §REQ-PARSE-005-SCN-04 要求解析 `WHERE id > 1` 并 raise ParseError，但 tokenize 阶段会因 `>` 不在 PUNCT 集而 raise TokenError；实施者用 pre-built Token 绕过 tokenizer 跑测试。**修复后端到端验证链路恢复**。
  - **C-2 governance** (I-3): commit `496080f` — 修 `spec.md:127` 多语句示例从 `INSERT INTO t VALUES (1)` → `INSERT INTO t(id) VALUES (1)` + plan §Task 16 line 2110 + test_parser.py SCN-02 注释清理。**真实影响**: spec 自相矛盾（line 127 示例无列名 vs line 67 REQ-PARSE-004 grammar 要求有列名）。
  - 提交链: `4ebef33`（实现 + 11 测试 + M-3/M-4/M-5 已处理）+ `305ebfd`（C-1 governance）+ `496080f`（C-2 governance）+ `a4ced35`（plan+tasks.md 勾选）
  - **Implementer 关键设计**:
    1. **WHERE helper 抽取干净**: `_parse_where(self) -> Optional[tuple]` (parser.py:336-358) 消除 SELECT/DELETE 重复逻辑
    2. **5-keyword if-elif dispatch**: CREATE → DROP → INSERT → SELECT → DELETE 顺序排列；剩余 KEYWORD 走精确 `f"unexpected keyword {kw}"` 兜底（无残留 `"X not supported yet"`）
    3. **AST 字段 docstring 注释**: `# list[list[Any]]` / `# Optional[tuple[str, str, Any]]` Python 运行时不强校验，但提供 contract 说明
    4. **错误消息统一**: `"expected X"` / `"duplicate column X"` / `"value count mismatch: got N, expected M"` / `"operator X not supported; MVP supports only ="`
  - **Reviewer 25 探针** 全过（含 SCN-04 pre-built token 验证 + multi-statement 位置 + 兜底 KEYWORD 起始 + purity 双调用一致性）
  - 16/16 parser + 107/107 全量（106 + C-1 回归测试 1）
  - Opportunistic 队列追加:
    - M-1 (行数 369 vs plan 期望 ≤ 250 +47%, 硬上限 600 内)
    - M-2 (SELECT no-FROM 错误位置指向 EOF 而非 `id` 后)
    - M-3 (无列名 INSERT 错误消息措辞)
    - M-4 (parse_statement_list 无 `;` 分隔也允许多语句, 但 spec 未禁)
    - I-1/M-3 (AST 实际 type hints 推迟到 hardening pass)
    - C-1/C-2 同类潜在问题: plan §Task 18/19/20/27 (executor + database API + e2e) 含 `INSERT INTO t VALUES (1)` 无列名示例，需后续 governance

- **Task 15: Parser — AST 节点 + parse() 入口 + CREATE/DROP（tasks.md §7.1-7.5）** — ✅ 已勾选（经历 1 轮 review-fix + 协调者 DELETE keyword fix）
  - implementer(DONE, TDD RED→GREEN, 5 测试, 213 行, ≤ 600 预算 35% 使用率) → thorough reviewer(⚠️ APPROVED_WITH_CONCERNS, 0 Critical, **1 Important I-1 tokenizer.KEYWORDS 缺 DELETE**, 5 Minor M-1..M-5)
  - **协调者 DELETE fix** (Important I-1): commit `d235ace` — tokenizer.KEYWORDS 集合添加 `"DELETE"` + 新增 `test_tokenize_delete_keyword` 回归测试 (REQ-PARSE-001-SCN-15)。诊断: plan §Task 13 Step 1 KEYWORDS 模板也漏 "DELETE"（不是 implementer 漏），spec §REQ-PARSE-005 明确要求 DELETE 解析。修复后 `tokenize("DELETE FROM t")` 正确 emit KEYWORD("DELETE") + KEYWORD("FROM")
  - 提交链: `0005040`（实现 + 5 测试）+ `d235ace`（协调者 DELETE fix + 回归测试）+ `58fc4f5`（plan+tasks.md 勾选）
  - **Implementer 关键设计**:
    1. **if-elif dispatch** 而非 dict：`parse_statement` 仅 dispatch CREATE/DROP，其他 KEYWORD raise `ParseError("X not supported yet")` —— 避免 Task 16 之前 AttributeError 漏出（Task 16 只需加 if 分支）
    2. **Insert/Select/Delete dataclass 骨架完整**：Task 16 可直接加 `_parse_*` 方法而不必改 dataclass 形状
    3. **EOF 兜底显式化**：`type EOF not supported in MVP` 而非 IndexError
    4. **空 columns 防御**：`CREATE TABLE t()` 显式 raise `"expected column name"`
    5. **错误位置精确指向出错 token**：`peek().line/col` 取值（不用 `self.i` 偏移）
  - **Reviewer 探针 20 个全过**（含 multi-statement StatementList + VARCHAR(10) col=19 + CreateTable line/col 指向 CREATE + 纯函数验证 20 + 多次调用一致性 16）
  - **接受的 MINOR**:
    - M-1: trailing comma 消息措辞（spec 未禁，可推迟）
    - M-2: `;;` 双分号 显式 raise（defensive 设计）
    - M-3: AST 字段泛型收紧（Task 16 implementer 处理：`Insert.values: list[list[Any]]` + `Select.where: Optional[tuple[str, str, Any]]`）
    - M-4: "X not supported yet" 兜底 TODO 注释（Task 16 implementer 移除）
    - M-5: SCN-06 multi-statement 测试（Task 16 一起加，Task 15 探针覆盖）
  - 5/5 parser + 95/95 全量（89 baseline + Task 14 的 2 + Task 15 的 5 + tokenizer DELETE 回归 1）
  - Opportunistic 队列追加: M-3 AST 字段泛型收紧 (Task 16 必做) / M-4 兜底分支清理 (Task 16 必做) / M-5 SCN-06 multi-statement 测试 (Task 16 必做)

- **Task 14: Tokenizer — 字面量层 + NaN/Inf 拒绝（tasks.md §6.4-6.5）** — ✅ 已勾选（经历 1 轮 review-fix + 协调者 SCN fix）
  - implementer(DONE_WITH_CONCERNS, 124→131 行, +2 测试) → thorough reviewer(⚠️ APPROVED_WITH_CONCERNS, 0 Critical, **1 Important: spec_id SCN-03 → SCN-02 误标**)
  - 协调者 SCN fix: commit `72b36f7` — `tests/unit/test_tokenizer.py:147` SCN-03→SCN-02 + `plan §Task 14 line 1812` 一致性同步。诊断: type-system spec line 15 映射 SCN-02 为 negative integer（test_type_system.py:96 已绑定 `test_parse_int_literal_negative`），line 19 映射 SCN-03 为 decimal float
  - 提交链: `47cc777`（实现 + 2 测试）+ `72b36f7`（协调者 SCN fix）+ `34fb560`（plan+tasks.md 勾选）
  - **Implementer Concern**: `tokenize("-7 + 3.14")` 在 `+` 处 raise TokenError —— 经 reviewer 探针 10/11 验证属于 spec-correct 行为（punctuation 集仅 `( ) , ; = *`，`-` 独立字符在 MVP 中 raise）
  - **Reviewer 关键发现（Important）**: `test_tokenize_int_negative` 误标 `REQ-TYPE-001-SCN-03`，按 type-system spec 应为 SCN-02。**协调者决策**：1 行 trivial 修复，独立 commit，不阻塞 Task 15 启动
  - **Reviewer 18 探针验证**（含 C-1 doubled-quote 回归 + 词法边界 `nanometer`/`infinite`/`infinity_value` → IDENT + 大小写不敏感 NaN/Inf/Infinity 拒绝）全过；row_tokenizer 层契约稳定
  - 接受的 MINOR: NaN/Inf 分支 `tokens.append(FLOAT)` 结构上 dead（accept，注释足够）+ 测试覆盖薄（2 happy 测试，建议补 negative float / Inf 拒绝 / lexical boundary / col tracking 后续 hardening pass）
  - 16/16 tokenizer + 89/89 全量
  - Opportunistic 队列追加: Task 14 hardening 测试套（negative float + Inf 拒绝 + lexical boundary + col tracking on negatives）

- **Task 13: Tokenizer — identifier / keyword / punctuation（tasks.md §6.1-6.3 + §6.6 + §6.7）** — ✅ 已勾选（经历 1 轮 review-fix-re-review + 2 个协调者 commit）
  - implementer #1(DONE, 4 passed, 122 行) → thorough reviewer(❌ NEEDS_FIXES, **C-1 doubled-quote 双重解码 bug** + I-1 测试覆盖不足 + I-2 死 import + M-1 末尾换行 + M-2 design.md 命名漂移)
  - **fix implementer #1** (被 token 上限中断): 完成 10 个新测试 (test_tokenizer.py 142 行)，但未修改实现
  - **fix implementer #2** (haiku 接续): commit `3a069d8` — 修复 doubled-quote 双重解码 + 移除 `parse_bool_literal` 死 import。**关键修复**: scanner 移除 `buf` 缓冲，`raw = sql[i:j+1]` 直接切片，`parse_text_literal` 作为唯一折叠入口
  - **协调者补充**: commit `19827b3` — SCN-13 测试期望 bug 修复（line==3 → line==2，因为输入只有一个 `\n`）+ design.md:45 `tokenizer.scan` → `tokenizer.tokenize` 命名漂移修复（M-2）；commit `b1841b4` — M-1 末尾换行修复
  - re-reviewer(⚠️ APPROVED_WITH_CONCERNS, C-1 + I-1 + I-2 + SCN-13 + M-2 全部 ADDRESSED, 仅 M-1 残留) → 协调者补 M-1 后 Task 13 完整出关
  - 提交链: `5c5e300`（实现）+ `3a069d8`（Fix 1+3 haiku）+ `19827b3`（协调者 SCN-13 + design）+ `b1841b4`（协调者 M-1） + 本次（plan+tasks.md 勾选）
  - **关键治理决策**: C-1 doubled-quote bug 走"标准 implementer 修复循环"（与 Task 11 C-1 spec patch 路径不同）。**Task 11 C-1 是 spec 治理问题 → 协调者 spec patch 路径**；**Task 13 C-1 是实现数据正确性问题 → implementer 修复循环路径**。两条路径并存，按问题性质选择
  - **token 上限事件**: fix implementer #1 中断时未保留 commit，但测试文件已写盘 142 行。fix implementer #2 (haiku) 接续完成 Fix 1+3，体现了 subagent-driven-development 的 fresh-subagent-per-task 韧性（不依赖上下文继承）
  - 接受的 MINOR: 全部 0 残留（reviewer 后续 M-1 已修复）
  - Opportunistic 队列追加: 无（Task 13 完整出关）
  - 14/14 row_codec-style tests + 87/87 全量

- **Task 12: Catalog — JSON 持久化（tasks.md §5.1-5.5）** — ✅ 已勾选
  - implementer(DONE, TDD RED→GREEN 10 passed, 207 行) → thorough reviewer(⚠️ APPROVED_WITH_CONCERNS, 0 Critical, 0 Important, 4 Minor 全部可推迟)
  - 提交: `d9751f7`（实现）+ `ec88133`（plan+tasks.md 勾选）
  - **关键决策**: implementer 拒绝 plan 的"Slot.offset 改绝对 page 偏移"建议，保留 Task 9 相对语义 + 新增 `data_offset` 仅用于 free-space accounting。Reviewer 确认这是 **correct decision**（避免 dispatch prompt 中提示的 plan 自相矛盾陷阱）
  - **M-1 文档漂移** (reviewer 发现): design.md §3.4 写 `MAX_INLINE_PAYLOAD=3800`，实现 = 4078 = `PAGE_SIZE - 18`。需在 archive 阶段对齐
  - 接受的 MINOR: 207 行 vs plan ≤ 150 (+38%, docstring-heavy) / tombstone 复用 + row 更短缺 unit test (probe 已验证) / `Slot.flags` 非 IntFlag (Task 9 末态 MINOR 遗留)
  - Opportunistic 队列追加: M-1 design.md 文档对齐 / M-2 补 `test_reuse_tombstone_smaller_row` / M-3 Slot.flags IntFlag 升级

- **Task 9: SlottedPage 框架 + 序列化（tasks.md §4.1-4.3）** — ✅ 已勾选
  - implementer(DONE, TDD RED→GREEN 2 passed, 135 行) → thorough reviewer(⚠️ APPROVED_WITH_CONCERNS, 0 Critical, 2 Important + 3 Minor)
  - 提交: `709f5f0`（实现）+ `f67e8f0`（plan+tasks.md 勾选）
  - **关键 plan 缺陷处理**: plan `from_bytes` 公式对空 page 算错（`4096 - max(16, 16) = 4080`，应为 0）。implementer 采用方案 A：末尾 2 字节 BE 存 `data_len` 标记 → 精确 roundtrip，accept
  - 接受的 MINOR: 行数 135 ≤ 150 / `MAX_SLOTS=32` 未强制 / `Slot.flags` 非 IntFlag / `get` 无 corrupt 边界校验 / 测试只 2 条
  - **重要 I-1/I-2 移交 Task 10**: `free_offset` 应反映真实可用空间起点（加 `_free_space()` helper）+ 提取 `MAX_INLINE_PAYLOAD = 4078` 常量 —— Task 10 完整 CRUD 必做
  - **Slot.offset 语义**: 相对 self.data 偏移（非绝对 page 偏移），Task 10 不会引发二次返工

- **Task 8: Pager alloc / read / write / close（tasks.md §3.4-3.5）** — ✅ 已勾选（经历 1 轮 review-fix 循环）
  - implementer #1(DONE, 4 passed, 156 行) → thorough reviewer(❌ NEEDS_FIXES：C-1 `_next_page_id` 在 reopen 时未 reseed → silent overwrite)
  - implementer #2(修复 DONE, +2 reopen-monotonic + read_page(1) 测试, 12 passed) → re-reviewer(✅ FIX_VERIFIED, 8/8 验收项)
  - 提交: `f086b9f`（首版）+ `4bfc4d6`（修复: reseed + 预分配 page 1 + close try/finally）+ `eba64fa`（plan+tasks.md 勾选）
  - **关键教训** (continuing from Task 7): review-fix 循环是质量底线，连续 2 轮 NEEDS_FIXES（Task 7 异常类 + Task 8 状态一致）都触发了 — 实施者倾向于"spec 字面 + 测试驱动"风格，但 spec 字面外的隐含前提（异常层级、page_id 单调性 across reopen）需要 reviewer 主动探查
  - 接受的 MINOR: 行数 156 vs plan ≤ 100（+56%）/ plan vs 实现偏差（:memory: 用 dict 而非 bytearray, 为 Task 21 free-page 预留）/ M-3-8
  - Opportunistic: 修正 2 个 Task 7 既有测试 `page_count == 1` → `== 2`（因 fix 2 预分配 page 1），属必然语义一致化

- **Task 7: Pager 文件头 magic + version + :memory:（tasks.md §3.1-3.3）** — ✅ 已勾选（经历 1 轮 review-fix 循环）
  - implementer #1(DONE, TDD RED→GREEN 5 passed, 101 行) → thorough reviewer(❌ NEEDS_FIXES：异常类错用 `DatabaseError(Exception)` 而非 spec 要求的 `InvalidDatabaseFile`/`UnsupportedSchemaVersion`)
  - implementer #2(修复 DONE, +1 bad schema version 测试, 6 passed) → re-reviewer(✅ FIX_VERIFIED, 6/6 验收项)
  - 提交: `35e9e90`（首版）+ `6d92cb2`（修复: 异常类对齐 spec + 异常层级）+ `0b785e1`（plan+tasks.md 勾选）
  - **关键教训**: 上一轮 Task 6 我用"选项 A 接受 MINOR"决策（行数偏差）是对的（可推迟），但 Task 7 是**真实 spec 偏差**（异常类不在层级内）—— 阻塞性问题必须走 review-fix 循环，不能用"接受偏差"绕过。**subagent-driven-development 的 review-fix 循环是质量底线**
  - 接受的 MINOR: 行数 99 vs plan 80 / `self._path` 私有 vs plan `self.path` 公开 / plan `MAGIC = b"TINYDB\x00"` (7B) vs 实现 `b'TINYDB\x00\x01'` (8B) — 实现版本与 spec 一致，plan 应更新（archive 阶段）
  - Opportunistic 修缮（M-3 from Task 7 review）：同步 plan §Task 7 reference 的 `MAGIC` 字面值与 spec + 实现一致

- **Task 11: row_codec — encode_row / decode_row + null bitmap（tasks.md §4.8）** — ✅ 已勾选（经历 1 轮 review-fix 循环 + 协调者 C-1 spec patch）
  - implementer(DONE, TDD RED→GREEN 4 passed, 60 行, LSB-first) → thorough reviewer(⚠️ APPROVED_WITH_CONCERNS, **1 Critical C-1 + 3 Important I-1/I-2/I-3 + 7 Minor**)
  - **C-1 spec patch** (协调者直接处理): commit `5b45801` — 5 文件 LSB-first 替换（spec.md + design.md + plan.md encode/decode formula + plan commit msg + .comet context/handoff）。诊断: spec 自相矛盾（line 73 "MSB-first" + line 82 期望 0x02 是 LSB-first 行为）；plan 同样自相矛盾（plan code MSB-first 公式 + plan test 0x02）。修复路径选择 A: 对齐 spec 到 LSB-first 而非改实现
  - **fix implementer** (I-1/I-2/I-3 + M-6): commit `7e31609` — 9 个新测试（SCN-05..13 含 bonus col_count=0）+ decode_row/encode_row docstring 加固（I-1 ValueError 契约分层 + I-3 类型校验转给 py_to_db）+ M-6 spec_id 冲突修复（roundtrip_all_populated SCN-03→SCN-04）。69 行 + 124 测试行。13/13 row_codec + 69/69 全量
  - **re-reviewer**: ✅ APPROVED — C-1 patch 干净、I-1/I-2/I-3 全数到位、M-6 根除、函数体未变、行数预算内、零回归
  - 提交链: `d9cb0a9`（实现）+ `5b45801`（C-1 spec patch 协调者）+ `7e31609`（fix implementer）+ 本次（plan+tasks.md 勾选）
  - **关键治理决策**: Critical spec gap 走 "协调者 spec patch" 而非 "implementer 修改实现" 路径。比 Task 7 异常类 NEEDS_FIXES 路径（reviewer flag → implementer 修）更合规：plan test + 实现已稳定对齐 LSB-first，回归到 MSB-first 的成本远高于 spec patch。Opportunistic 队列追加: C-1 spec patch 闭环 (本任务)
  - 接受的 MINOR: 7 个 M-1..M-7 (行数 / `_TYPE_SIZES` YAGNI / 错误消息细节改进 / assert→raise ValueError / spec_id 模板问题 / commit msg LSB-first / extra bytes 静默忽略) 全部可推迟到 archive

- **Task 10: SlottedPage insert/delete/update/get（tasks.md §4.4-4.7）** — ✅ 已勾选
  - implementer(DONE_WITH_CONCERNS, TDD RED→GREEN 31 passed, type_system.py 84→129 行) → thorough reviewer(⚠️ APPROVED_WITH_CONCERNS, 0 Critical, 3 修缮 4 Minor)
  - 提交: `81064c5`（实现）+ `eb64a38`（plan+tasks.md 勾选）
  - 接受的 MINOR: 行数超 29 行 (129 vs ≤ 100, < 150 硬上限) / FLOAT NaN/Inf 单测缺 / `test_db_to_py_roundtrip_int` 缺 spec_id / 行内 `import struct` 重复
  - 协调者决策：选 reviewer **选项 A**，3 个修缮累积到 opportunistic 列表（见下），不阻塞 Task 7；理由：修缮成本 < 10 分钟但 TDD 循环成本高，将在 Task 9 (Executor) 集成测试阶段一并补

- **Task 5: Type System 字面量解析 + NaN/Inf 拒绝（tasks.md §2.6）** — ✅ 已勾选
  - implementer(DONE, TDD RED→GREEN 22 passed, type_system.py 84 行) → thorough reviewer(✅ APPROVED, 0 Critical/Important, 5 Minor 全部可推迟)
  - 提交: `c63d9f1`（实现）+ `4b7dc97`（plan+tasks.md 勾选）
  - 接受的 MINOR: M1-5（int_literal 缺显式无效测试 / 空 text literal 缺测 / 缺 @pytest.mark.unit 类别 / 未用 hypothesis / bool 字面量前后空格 fail-fast）
  - 文档漂移提醒（reviewer 指出）：tasks.md §2.6 写"在 tokenizer.py 实现"，plan + design 写"type_system.py"，实现跟 plan；待 archive 阶段统一对齐

- **Task 4: Type System BOOL+FLOAT 编解码（tasks.md §2.4-2.5）** — ✅ 已勾选
  - implementer(DONE, TDD RED→GREEN 14 passed, 55 行) → thorough reviewer(APPROVED_WITH_CONCERNS)
  - 提交: `1b78b67`
  - 接受的 MINOR: M1 `buf[offset]!=0` 可读性; M2 缺截断测试(见累积待办); M3 import 合并
- **Task 3: TEXT 编解码（§2.3）** — ✅ `1cefc44`（reviewer APPROVED_WITH_CONCERNS；MINOR 见累积待办）
- **Task 2: INT 编解码（§2.2）** — ✅ `7d9401c`（reviewer APPROVED）
- **Task 1: 项目骨架（§1）** — ✅ `7842098`+`87ef1ef`+`31db9df`（遗留 Task 20 须替换 __init__ placeholder）

## 待办 Task（plan 顺序）

Task 1(修复中) → 2 INT codec → 3 TEXT → 4 BOOL+FLOAT → 5 Tokenizer字面量 → 6 py_to_db/db_to_py/validate_compare
→ 7-8 Pager → 9-10 SlottedPage → 11 RowCodec → 12 Catalog → 13-14 Tokenizer → 15-16 Parser → 17-19 Executor
→ 20 Database+Row（须替换 __init__.py placeholder）→ 21 Overflow Chain → 22-27 Property/E2E/Integration → 28 文档 → 29-33 验收/verify
