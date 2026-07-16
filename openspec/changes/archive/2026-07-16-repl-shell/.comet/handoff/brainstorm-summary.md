# Brainstorm Summary

- Change: repl-shell
- Date: 2026-07-16

## 确认的技术方案

1. **模块形态**：单文件 `src/tinydb/repl.py` ≤350 行，常量 / 历史 / 多行 / dispatcher / SQL / 格式化 / 入口 7 个功能块。
2. **主循环**：`buf` 累积 → `_is_unterminated` 启发式判定 → 闭合后 `_run_sql(db, buf)`。
3. **多行启发式**：SQL-aware — 字符状态机扫 `''` 配对跳过；`--` 行注释 / `/* */` 块注释同样跳过。
4. **多语句反馈**：buffer + `db.execute(buf)` 一次性提交（MVP `Database.execute` 已支持 StatementList）。
5. **元命令**：lstrip 后的 `.` 前缀 → `_handle_meta`；不进入 tokenizer / parser。7 个具体行为在 dispatcher 表中给出。
6. **列宽**：naive `len(str(value))` codepoint 计数；MVP 数据多为 ASCII + BMP 字符可接受；超 30 字符截断加 `…`。
7. **结果分派**：`db.execute(sql)` 无法区分空 SELECT vs DDL/DML（两者均返回 `[]`）—— 因此 `_run_sql` 需在 execute 之前**单独 tokenize + parse peek 最后一个 AST 类型**。AST 是 Select → SELECT 路径（空显示 `(no rows)`, 非空显示表格），否则 `OK`。
8. **CLI**：手动解析 `sys.argv`，支持 `--database <path>` 和位置参数 `<path>` 双模式；默认 `:memory:`。
9. **历史**：readline（Unix）/ 内置 `input()`（Windows ImportError fallback）；`~/.tinydb_history`；OSError 静默。
10. **错误格式**：`ERROR: <Class>: <str(exc)>` 单行；不打印 traceback。

## 关键取舍与风险

| 风险 | 取舍 | Mitigation |
|------|------|-----------|
| `_is_unterminated` 与 `''` 转义 / 注释冲突 | 实现 25 行字符状态机 | 单元测试覆盖 `'o''brien'`、`/* ... ' */` 等场景 |
| CJK/emoji 字符对齐错位 | naive `len()` | 文档声明为已知限制；MVP 文本几乎全 ASCII |
| 多语句时 SELECT 0 行 vs DML 同样返回 `[]` | `_run_sql` 预先 peek AST | 必须不显著影响性能；tokenize+parse 自身 < 1ms |
| spec 描述 `Row(id=1)` 期望与 `Row.__repr__` 实际输出对齐 | MVP `__repr__` 已输出 `Row(id=1)` | 直接 `print(row)` 即可命中 spec |
| subprocess 集成测试偶发（race） | Popen + read1 + timeout 显式控制 | 失败时降级到手动 `smoke.sh` |
| 文件路径含 shell 元字符 | `pathlib.Path` 直读 | 无需 quote 处理 |

## 测试策略

| 测试文件 | 类型 | 覆盖 |
|---------|------|------|
| `tests/unit/test_repl.py` | unit | `_is_unterminated` 全分支、`_format_table` 算法、`_handle_meta` 6 个命令 + 未知、`_run_sql` OK vs table 分派、`_make_prompt` `:memory:` vs path、`--database` 解析 + 位置参数 |
| `tests/integration/test_repl_process.py` | integration | Popen `tinydb-repl` → stdin → stdout 断言 8 场景（基本 CRUD、`(no rows)`、`.tables`、`.schema` DDL、`.read`、`.exit`、多行 INSERT、错误单行） |
| `examples/repl_smoke.sh` | smoke | bash 喂 4 条 SQL 给 tinydb-repl，grep 关键串（人眼 + CI 双用） |

合并覆盖率门槛 ≥85%（与 MVP 持平）。

## Spec Patch

无。open 阶段 spec 已 8 requirement / 26 scenario 齐备；本 design 阶段探查到的细节（empty SELECT vs DDL 同返回 `[]`、Row.__repr__ 实际串、`ParseError` 实际格式、`execute` 返回 list[Row]）都属于实现侧适配，spec 语义不受影响。

## Open Questions Resolved（brainstorming 阶段确认）

| ID | 问题 | 决定 |
|----|------|------|
| Q1 | escape 语义（odd-`'` vs SQL-aware `''` skip） | **SQL-aware skip '' pairs**（用户已确认） |
| Q2 | 列宽计算（byte / naive / wcwidth） | **Naive `len(str(value))`**（用户已确认） |
| Q3 | 交互多语句反馈（buffer+execute vs split-statement） | **Buffer + `db.execute(buf)` 一次性**（用户已确认） |
| Q4 | `.read` 解析（split `;` vs 整体 execute） | **整体给 `_is_unterminated` 循环，复用主路径**（设计一致） |
| Q5 | 空 SELECT vs DML 区分（都返回 `[]`） | **`_run_sql` 内 AST peek 最后语句类型** |
| Q6 | CLI 风格（argparse vs 手写） | **手写（省行数），支持 `--database <path>` 与位置参数双模式** |
| Q7 | 位置参数 `<path>` 与 `--database` 同时支持 | **是，仅 spec 不要求这是 spec 之外的小扩展** |
