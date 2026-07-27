# Tasks: CLI 功能增强

## 1. 依赖与构建配置

- [x] 1.1 在 `pyproject.toml` 的 `[project] dependencies` 中加入 `pygments>=2.18` 和 `prompt_toolkit>=3.0.0`
- [x] 1.2 运行 `pip install -e .` 验证依赖可解析
- [x] 1.3 在 `pyproject.toml` 的 `[project.optional-dependencies]` 添加 `repl = ["pygments>=2.18", "prompt_toolkit>=3.0.0"]` 以允许可选安装（推荐做法）

## 2. 输入/输出层 `_repl_io.py`

- [x] 2.1 创建 `src/tinydb/_repl_io.py`：定义 `ReplIO` 类，封装 `prompt_toolkit.PromptSession` + `PygmentsLexer` + `FileHistory` — commit `859b2b8` (`_repl_io.py:108-183` `PromptToolkitReplIO` + `ReplIOProtocol`)
- [x] 2.2 在模块顶层 `try/except ImportError`：当 `prompt_toolkit` 不可导入时，提供 `_FallbackIO`（基于 `input()` 的退化实现） — commit `859b2b8` (`_repl_io.py:23-34` 软导入 + `_repl_io.py:187-219` `FallbackReplIO`)
- [x] 2.3 实现多行检测：扫描缓冲区中是否有未闭合的引号、括号、`;`；未终止则使用 `CONTINUATION_PROMPT` — commit `859b2b8` (`_repl_io.py:60-105` `_is_unterminated()` + `FallbackReplIO.read_statement` 累积逻辑)
- [x] 2.4 实现历史加载与保存：`~/.tinydb_history`，与现有 readline 路径兼容 — commit `859b2b8` (`_repl_io.py:136-138` `FileHistory` + `FallbackReplIO._history` 内存历史 + `_repl_io.py:200-203` `save_history` 协议方法)
- [x] 2.5 实现 `NO_COLOR` 环境变量与 `TERM=dumb` 检测；不支持时禁用颜色 token — commit `859b2b8` (`_repl_io.py:46-52` `_color_enabled()`)

> **Recorded deviations** (follow-ups for verify stage):
> 1. **Fallback `;` policy differs from design doc** — 当前 fallback 要求显式 `;` 终止符（已 inline-文档）；design doc 仅描述 `_is_unterminated` 终止判定。两者语义保持兼容（fallback 是 layer-2 限制），但需要 design doc amend 跟进。
> 2. **History file permissions on existing files** — `touch(mode=0o600)` 仅在新建时生效；既有 `~/.tinydb_history` 不被收紧权限。安全 follow-up，不阻塞 Task 3。
> 3. **22 tests + 822/1 baseline verified** by implementer `a09f7abeac6419968` (commit `859b2b8`) and externally reviewed by `acf8dcbc32a81401c` (APPROVE + deferrable MEDIUM/LOW); coordinator-side spot-check `822 passed, 1 skipped in 56.25s` post-venv-repin.

## 3. 结果格式化 `_repl_format.py`

- [x] 3.1 创建 `src/tinydb/_repl_format.py`：定义 `format_rows(rows: list[Row], fmt: str) -> str` 分发函数 — commit `2fd2d34`
- [x] 3.2 实现 `table` 格式（迁移现有 `_format_table` 逻辑） — commit `2fd2d34` (`_repl_format.py:34-58` byte-compatible with `repl._format_table`)
- [x] 3.3 实现 `csv` 格式（`csv.writer` + `StringIO`，RFC 4180） — commit `2fd2d34` (`_repl_format.py:61-69`)
- [x] 3.4 实现 `json` 格式（`json.dumps` 数组；Row 字段名映射） — commit `2fd2d34` (`_repl_format.py:72-82`，含 `default=str` fallback)
- [x] 3.5 添加单元测试 `tests/unit/test_repl_format.py` 覆盖三种格式的快照对比 — commit `2fd2d34` (8 tests, 全部 PASS)

> **Recorded deviations** (follow-ups for verify stage):
> 1. **plan §3.1 vs §3.3 contradiction** — `test_format_unknown_raises_value_error` 原本用 `format_rows([], "markdown")` 期望 ValueError，但 plan §3.3 impl 先短路空 rows 返回 `(no rows)`，fmt 永不被检查。两段都"逐字"符合 plan 但语义互斥。Coordinator 裁定：修改 test 为 `format_rows(sample_rows, "markdown")`（non-empty rows），与 design "empty → (no rows), unknown fmt → ValueError" 独立行为一致；impl 不变。Plan amend 留待 verify 阶段。
> 2. **LOW · docstring typo** — `_repl_format.py:21` 有缺空格的小笔误 "(no rows)'.fmt ∈ ..."（plan §3.3 逐字），独立 chore fix。
> 3. **Plan-staleness LOW**: `src/tinydb/repl.py` 仍含旧 `_format_table`（lines 103-128）。Task 3 仅创建新模块；repl.py 的旧实现由 Task 5 整合阶段处理（待派发）。
> 4. **8 tests + 822/1 baseline verified** by implementer `ab049ccc1715b24e5` (commit `2fd2d34`) and externally reviewed by `a902b95a5105de16c` (APPROVED_WITH_NOTES — NEXT=CHECK_OFF_AND_NEXT; LOW findings only).

## 4. meta 命令注册表 `_repl_meta.py`

- [x] 4.1 创建 `src/tinydb/_repl_meta.py`：定义 `META_COMMANDS: dict[str, Callable]` 与分发函数 `handle_meta(line: str, db: Database) -> bool` — commit `1324a83` (`_repl_meta.py:1-307` 12 meta commands; ReplState dataclass; handle_meta dispatcher; MetaCommand dataclass; _cmd_* handlers pure-Python via state)
- [x] 4.2 迁移现有 meta 命令（`.exit` / `.quit` / `.help` / `.tables` / `.schema` / `.read`）到注册表 — commit `1324a83` (6 commands migrated; old `repl.py` block removed at Task 5 991f3e7)
- [x] 4.3 实现 `.explain <sql>`：调用 `db.explain_plan(sql)` + `plan.format_plan(plan)` 渲染 — commit `1324a83` (`_cmd_explain wraps db.explain_plan in try/except → friendly error`)
- [x] 4.4 实现 `.indexes [table]`：遍历 `catalog.indexes` + `IndexManager` 输出 BTree 元数据 — commit `1324a83` (`_cmd_indexes drives IndexManager.all_indexes()` (new) — single contract point)
- [x] 4.5 实现 `.stats`：聚合表数/行数/页数/空闲页数/WAL 大小 — commit `1324a83` (`_cmd_stats aggregates Tables/Rows/Pages/Free pages/WAL via catalog + pager + WAL stat`)
- [x] 4.6 实现 `.timer on|off`：切换模块级 `_TIMER_ENABLED` — commit `1324a83` (`_cmd_timer toggles state.timer_enabled`)
- [x] 4.7 实现 `.format <table|csv|json>`：切换模块级 `_OUTPUT_FORMAT` — commit `1324a83` (`_cmd_format toggles state.output_format`)
- [x] 4.8 实现 `.color on|off`：切换 ANSI 颜色输出 — commit `1324a83` (`_cmd_color toggles state.color_enabled`; 实际 lexer set 在 Task 5 Round 2 fix `66c86b2` 完成)
- [x] 4.9 更新 `.help` 输出以反映所有新命令 — commit `1324a83` (`_cmd_help 列出所有 12 commands + 用法`)

> **Recorded deviations** (follow-ups for verify stage):
> 1. **`_cmd_indexes` `keys≈?` 占位** — 当前实现无条件打印 `keys≈?`;public contract 期望估算键数。要么添加真实估算 (遍历 BTree 节点) 要么记录 deviation。Task 4 scope 不涵盖 BTree 遍历,建议 verify 阶段 follow-up。
> 2. **`handle_meta` 使用 `lstrip('.')` 导致 `..exit`/`...quit` 退出** — line 284 `lstrip('.')` 移除所有前导点;`..exit` 解析为 `.exit` 然后退出。修复:只移除单个前导点或 split(' ', 1) 后验证首段。Minor edge case,verify follow-up。
> 3. **`rest.split()` 破坏 `.read` 含空格路径** — `_cmd_read` 用 `rest.split()[0]` 取路径;`/path/with space/script.sql` 会被截断为 `/path/with`。修复:用 `shlex.split(rest)[0]` 或整段 rest 作 path (推荐 shlex)。Verify follow-up。
> 4. **`_cmd_stats` 静默吞掉 per-table COUNT 异常** — line 190 在 try/except 内调用 db.execute("SELECT COUNT(*)") 失败时静默忽略;Rows 可能 underreport。修复:warn stderr + 计入 "unknown"。Verify follow-up。
> 5. **`.explain` `rest.split()` 规范化字符串字面量空白** — `.explain SELECT * FROM t WHERE name = 'a b'` 中 `'a b'` 的空白被 split 规范化;轻微字符串字面量破坏。修复:与 #3 同样使用 shlex。Verify follow-up。

## 5. 整合与 REPL 主循环

- [x] 5.1 重构 `src/tinydb/repl.py`：将 main / `_interactive_loop` 瘦身；调用 `_repl_io` / `_repl_meta` / `_repl_format` — commit `991f3e7` (repl.py 163 行；thin wrapper,导入 `_format_table`/`format_rows`/`ReplState`/`handle_meta`/`PromptToolkitReplIO`/`FallbackReplIO`/`ReplIOProtocol`/`_HAS_PROMPT_TOOLKIT`/`_color_enabled`/`_is_unterminated`)
- [x] 5.2 整合计时：`.timer on` 时，在 `_run_sql` 结果后追加 `Time: X.XXX ms` — commit `991f3e7` (`_run_sql` 内 `if state.timer_enabled: ... print(f"Time: {elapsed_ms:.3f} ms")`)
- [x] 5.3 整合输出格式：`.format` 切换后，下次查询按新格式输出 — commit `991f3e7` (`_run_sql` 调 `format_rows(rows, state.output_format)`,table/csv/json 三路)
- [x] 5.4 在 `repl.py` 入口处输出首次启动提示（"`.help` for commands, `.timer on` for timing"）— commit `991f3e7` (`main()` 在 _interactive_loop 之前 print 启动提示)
- [x] 5.5 Round 2 fix — 4 HIGH + 1 MEDIUM reviewer findings — commit `66c86b2`:
  - HIGH 1 — `FallbackReplIO.read_statement` meta special-case (`.` 开头行立即返回)
  - HIGH 2 — `PromptToolkitReplIO.set_color(bool)` 重建 session 保留 history; `_cmd_color` 调用 `io.set_color`
  - HIGH 3 — `.read` 调 `_run_sql(db, stmt, state)` 而非 bare `_run_sql_from_meta` (honors format/timer/color)
  - HIGH 4 — `main()` 检测 `not sys.stdin.isatty()` BEFORE IO 选择,强制 `FallbackReplIO` (无警告); `__main__` block 加入供 non-tty subprocess 测试
  - MEDIUM 5 — `PromptToolkitReplIO.add_history()` 改为 no-op (PromptSession.prompt() auto-appends); `_interactive_loop` 仅在 `isinstance(io, FallbackReplIO)` 时调 `add_history`
  - 20 new tests pass (test_repl_{fallback_meta,color_lexer,read,non_tty,history}.py)
  - 全套: 905 pass + 1 skip (从 885+1 +20 新增,basis 保留)

> **Recorded deviations** (follow-ups for verify stage):
> 1. **`_format_table([])` 修复** — implementer 在 `_repl_format.py:36-37` 加 `if not rows: return "(no rows)"`,否则 `rows[0].columns` 会 IndexError。原 `repl._format_table` 行为兼容,Task 3 迁移时遗漏该空行分支(plan §3.2 "字节级一致" 隐含假设 non-empty)。属于 Task 5 关联修复,独立 deviation。
> 2. **`tests/unit/test_repl.py` 整体重写** — 472 行原版重写为 237 行(commit `991f3e7`),覆盖新 API(`_run_sql(state)`/`_interactive_loop(db, io, state)`/`ReplState`)。原版覆盖 `_format_table` 直接行为(已在 _repl_format 测试中覆盖),新版聚焦 repl.py 整合层。
> 3. **Path resolution 警告** — 全局 PATH `/home/lz/.local/bin/tinydb-repl` 优先于 worktree-local `.venv/bin/tinydb-repl`。手动冒烟测试(§8.2)需用绝对路径或显式 prepend PATH。已在 README/CLI doc 中标注。
> 4. **Round 2 测试 shared queue fix** — `test_repl_io_prompt_toolkit.py` `FakeSession` 现在 `prompt()` 时 auto-append 模仿真实 prompt_toolkit; sessions 共享一个 queue 让 `set_color` 不重放已消费输入。同样 fix 在 `test_repl_meta_commands.py` 应用。
> 5. **`FakeIO` history assertion relaxed** — `test_repl.py` `FakeIO` history 断言改为 `[]` (loop 仅 `FallbackReplIO` 记录); `test_repl_io.py` 断言翻转反映 add_history no-op。
> 6. **87 REPL tests pass + 836 full suite + 1 skip** by implementer `ab4babd84c39bc347` (commit `991f3e7`); coverage 92.39%.
> 7. **Round 2 fix: 20 new tests + 905/1 full suite** by fix agent `a0417bad874715867` (commit `66c86b2`); no new deviation added.

## 6. 集成测试

- [x] 6.1 创建 `tests/integration/test_repl_io_prompt_toolkit.py`：使用 `PromptSession` Patch + stdin 注入 SQL 片段；断言执行成功 — commit `8760107` (5 tests)
- [x] 6.2 创建 `tests/integration/test_repl_multiline.py`：跨 5 行 SELECT 查询 — commit `8760107` (5 tests)
- [x] 6.3 创建 `tests/integration/test_repl_color_off.py`：`NO_COLOR=1` 环境下不输出 ANSI 颜色码 — commit `8760107` (8 tests)
- [x] 6.4 创建 `tests/integration/test_repl_fallback.py`：monkey-patch `prompt_toolkit` 不可导入；REPL 退化为 `input()` 模式 — commit `8760107` (6 tests)
- [x] 6.5 创建 `tests/integration/test_repl_meta_commands.py`：所有 meta 命令的端到端行为 — commit `8760107` (25 tests)

> **Recorded deviations** (follow-ups for verify stage):
> 1. **FallbackReplIO 无法在 `_interactive_loop` 内提供 meta 命令** — fallback 适配器要求显式 `;` 终止符（已记录于 tasks.md §2 deviation #1），meta 命令从不以 `;` 收尾，无法通过 fallback loop 抵达。`test_repl_meta_commands.py` 用 `PromptToolkitReplIO` + patched `PromptSession` 端到端驱动 meta 命令（匹配真实 CLI 行为）；`test_repl_fallback.py` 限定 fallback loop 仅测试 SQL 路径。与既有 `test_repl_process.py` 子进程套件同样的限制。
> 2. **Fallback 跨行带引号字符串** — `FallbackReplIO._buf` 以 `line + "\n"` 累积，单引号字符串跨两行时保留字面 `\n`。`test_fallback_multiline_quote_spanning_lines` 分别断言两半内容而非连接字符串。test docstring 已 inline 说明。
> 3. **Plan §6.1 `test_meta_commands_end_to_end` 测试形态调整** — plan 的合并测试将 `.indexes`/`.stats`/`.format`/`.timer`/`.explain` 直接通过 `FallbackReplIO` + `input()` 注入，由于 deviation #1 不可行。Implementer 拆为 25 个 per-command focused tests，全部使用 `PromptToolkitReplIO`（meta 命令唯一可达路径）。覆盖等价或更强。
> 4. **49 tests pass + 836+1 baseline preserved → 885+1 total** by implementer `ad0606739bc6fb356` (commit `8760107`); review pending.

## 7. 文档

- [x] 7.1 更新 `README.md`：新增 "REPL" 章节，列出多行/高亮/行编辑与新 meta 命令 — commit `5628f3f` (README.md +48/-14; 替换原 5 行 legacy meta 表; 子章节: input/highlighting/editing + 12-command meta table + color/output behavior + NO_COLOR/TERM=dumb)
- [x] 7.2 在 `docs/superpowers/specs/` 下新建 `cli-enhancements.md`，汇总 spec 中的公开契约 — commit `5628f3f` (新文件 216 行; 覆盖 `ReplIOProtocol` + `PromptToolkitReplIO` (含 `set_color` 备注) + `FallbackReplIO` + `ReplState` 字段表 + 12 meta commands + 多行终止规则 + 输出格式契约 + color env vars + 可选依赖兼容性)
- [x] 7.3 更新 `CHANGELOG.md`：新增 `cli-enhancements` 条目 — commit `5628f3f` (新文件 32 行; `Unreleased` 块: `tinydb-repl` 多行/高亮/行编辑 + 12 meta commands + 三种输出格式 + `.timer`/`.color` + 软回退)

> **Recorded deviations** (follow-ups for verify stage):
> 1. **CHANGELOG.md 新建** — 原文件不存在,与 plan §7.3 "如存在则更新" 不同; Task 7 implementer 选择新建 + 初始条目 (与 CC §8.3 deviation 同样的判断: task prompt 显式要求 if-absent-create)。Plan amend 留待 verify 阶段。
> 2. **Pre-existing uncommitted changes in CLI worktree** — implementer 在 `_repl_io.py`/`_repl_meta.py`/`repl.py`/`test_repl_io_prompt_toolkit.py` 观察到未提交改动(Task 5 Round 2 fix 进度),按 docs-only 约束未触碰,记录在 subagent-progress。
> 3. **`set_color` 契约描述** — spec 描述 `set_color(enabled: bool)`,实现 `_repl_io.py:164` 确认存在。docs 与 code 一致(Round 2 fix 已加此 setter);如 fix agent 最终未加,docs 与 impl 会失同步,verify 阶段需检查。

## 8. 最终验证

- [ ] 8.1 运行 `pytest` 完整套件，确认通过且覆盖率 ≥ 92%（保持基线）
- [ ] 8.2 手动冒烟：启动 `tinydb-repl --database /tmp/test.db`，验证多行 / 高亮 / 行编辑 / 新 meta 命令
- [ ] 8.3 验证回退路径：临时移除 `prompt_toolkit` 包，确认 REPL 仍可用（退化模式）