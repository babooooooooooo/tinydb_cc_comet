# Comet Design Handoff

- Change: cli-enhancements
- Phase: design
- Mode: compact
- Context hash: c7116248ed7f55e5b888e59c825dfd693afab9327d36a4ff7680c108297eacab

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/cli-enhancements/proposal.md

- Source: openspec/changes/cli-enhancements/proposal.md
- Lines: 1-55
- SHA256: 60c7aaecf994c3544b6c78d362caa5de0908ef14f8a5b6ba72d07dc7514655a6

```md
# Proposal: CLI 功能增强

## Why

`tinydb-repl` 当前是一个 302 行的最小可用 shell，使用 `input()` 单行读取 + 手工 SQL 终止符判定（`;`）拼接多语句。能力局限：
- 无多行编辑能力：编写长查询（多 JOIN、子查询）只能反复回车续行
- 无语法高亮：黑白字符流阅读吃力
- 无行编辑：无法用方向键/Emacs 快捷键（Ctrl-A/E/K 等）修改已输入内容
- 无高级 meta 命令：无法直接查看 SQL 执行计划（`.explain`）、索引元数据、统计信息、计时
- 无输出格式切换：所有结果只能以表格渲染

随着聚合（`tinydb-aggregation`）、JOIN（`2026-07-24-join-query`）、并发控制（`concurrency-control`）等能力上线，REPL 已无法满足开发者调试、运维巡检、benchmark 等使用场景。

## What Changes

新增 `cli-enhancements` capability：

1. **多行编辑**：基于 `prompt_toolkit` 提供真多行输入 + 跨行编辑 + 自动括号闭合
2. **语法高亮**：基于 `pygments` 在 REPL 输入时实时高亮 SQL token（关键字、字符串、数字、运算符、注释）
3. **行编辑**：依赖 `prompt_toolkit` 自带 Emacs/Vi 键绑定；提供历史搜索（↑/↓ Ctrl-R）
4. **新 meta 命令**：
   - `.explain <SQL>`：执行并展示 `LogicalPlan` 树（来自 `plan.format_plan()`）
   - `.indexes [table]`：列出数据库中所有索引（BTree 索引元数据）
   - `.stats`：表/行数/页数/空闲页数/WAL 大小
   - `.timer on|off`：执行计时（毫秒输出在结果下方）
   - `.format csv|json|table`：切换结果输出格式
5. **现有命令增强**：`.help` 自动列出全部 meta 命令与新快捷键
6. **依赖管理**：`pygments>=2.18`、`prompt_toolkit>=3.0` 加入 `pyproject.toml` 的 `dependencies`

## Capabilities

### New Capabilities

- `cli-enhancements` — 多行/高亮/行编辑 + 新 meta 命令 + 输出格式切换

### Modified Capabilities

无

## Impact

- **新增运行时依赖**：`pygments`、`prompt_toolkit`（stdlib 之外的第一个非测试依赖；与 `concurrency-control` 无关）
- **代码迁移**：`repl.py` 由 302 行扩展至 ~700-900 行；考虑拆分为多个模块（`_repl_io.py`、`_repl_meta.py`、`_repl_format.py`）保持单文件 ≤ 800 行
- **CLI 入口兼容**：现有 `tinydb-repl` 入口参数（`--database PATH`）不变；环境变量 `PYTHONSTARTUP` 可加载用户 `.tinydb_init.py`
- **历史文件**：保留现有 `~/.tinydb_history` 路径；prompt_toolkit 的历史格式需配置兼容
- **测试策略**：单元测试（meta 命令解析）+ 集成测试（prompt_toolkit Patch_stdout/Patch_stdin + stdin 注入 SQL 片段）
- **不破坏现有行为**：所有现有 meta 命令（`.exit`/`.quit`/`.help`/`.tables`/`.schema`/`.read`）保持兼容
- **可选降级**：当 `prompt_toolkit` 不可导入时，REPL 退化为单行 `input()` 模式（保持 stdlib-only fallback，行为类似 Task 1 的 `_HAS_FCNTL` 处理）

## Out of Scope

- 远程/网络 REPL（SSH 通道、Web UI）
- SQL 自动补全（候选表/列提示）—— 留作 follow-up
- 持久化配置（`.tinydbrc`）—— 留作 follow-up
- Vi 模式之外的自定义键绑定 —— 留作 follow-up
- `tinydb-repl` 之外的 CLI 子命令（`tinydb dump`、`tinydb import` 等）—— 留作独立 change
```

## openspec/changes/cli-enhancements/design.md

- Source: openspec/changes/cli-enhancements/design.md
- Lines: 1-207
- SHA256: f89076c431c7dcca5794f9872f28e01bb6407bfa5a24e0710fab2139c00f1800

[TRUNCATED]

```md
# Design: CLI 功能增强（高层架构）

> 本文件给出 cli-enhancements change 的高层架构决策。深度技术细化（API 形状、边界条件、测试策略）将在 Design Doc（design 阶段）中给出。

## 架构总览

```
                       ┌─────────────────────────────────┐
                       │        tinydb-repl 入口          │
                       │     src/tinydb/repl.py (main)   │
                       └────────────────┬────────────────┘
                                        │
                  ┌─────────────────────┼─────────────────────┐
                  ▼                     ▼                     ▼
        ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
        │   _repl_io.py    │  │  _repl_meta.py   │  │  _repl_format.py │
        │  (输入/输出层)    │  │   (meta 命令)    │  │  (结果格式化)    │
        │ prompt_toolkit   │  │ .explain/.indexes│  │ table/csv/json   │
        │ pygments lexer   │  │ .stats/.timer/   │  │                  │
        │ fallback: input()│  │  .format/.help   │  │                  │
        └──────────────────┘  └──────────────────┘  └──────────────────┘
                                        │
                                        ▼
                            ┌──────────────────────┐
                            │   Database (现有)    │
                            │   plan.py (现有)     │
                            └──────────────────────┘
```

## 关键决策（D1-D8）

### D1. 输入层技术选型

| 选项 | 优劣 |
|------|------|
| A. `prompt_toolkit` (Recommended) | 完整多行/行编辑/历史/语法高亮生态；标准选择；引入新依赖 |
| B. 自研 `readline` + `pygments` | 零新依赖；但多行编辑体验差 |
| C. `pyrepl` | 纯 Python；少用；社区支持弱 |

**决策**：选 A `prompt_toolkit>=3.0`，通过 `try/except ImportError` 提供 stdlib `input()` fallback（与 `_HAS_FCNTL` 处理对称）

### D2. 语法高亮实现

基于 `pygments.lexers.sql.SqlLexer` + `prompt_toolkit.lexers.PygmentsLexer` 桥接。

- 实时高亮（每个 keystroke 后重渲染）
- 自定义 token 颜色：仅在终端支持 256 色时启用（`TERM != "dumb"` 检测）
- 关闭方式：`NO_COLOR=1` 环境变量 / `.color off` meta 命令

### D3. meta 命令分发

统一命令注册表：

```python
META_COMMANDS: dict[str, Callable[[list[str], Database], None]] = {
    "explain": _cmd_explain,
    "indexes": _cmd_indexes,
    "stats": _cmd_stats,
    "timer": _cmd_timer,
    "format": _cmd_format,
    # ... 现有 .exit / .help / .tables / .schema / .read
}
```

`._handle_meta()` 改为查表分发；新命令只需注册。

### D4. `.explain` 输出

调用 `db.explain_plan(sql)` 获取 `LogicalPlan`；用 `plan.format_plan(plan)` 渲染为缩进树；附 EXPLAIN 头部信息（estimated cost、table count、index usage）。

**注意**：`.explain` 不执行查询（避免误操作）；若需执行 + 计划，使用 `.explain ANALYZE`（可选，out of scope for v1）

### D5. `.indexes` 输出

遍历 `Catalog.indexes` + `IndexManager` 状态；按 `table.column` 列出 BTree root_page_id、key 数量估算、唯一性。

### D6. `.stats` 输出

聚合：
- 表数量（`len(catalog.tables)`）

```

Full source: openspec/changes/cli-enhancements/design.md

## openspec/changes/cli-enhancements/tasks.md

- Source: openspec/changes/cli-enhancements/tasks.md
- Lines: 1-72
- SHA256: 8b5e879046d7dc12630e9a34c3bfc8b06eee267c311a7f58a89a8f62b9196eb8

```md
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

- [ ] 4.1 创建 `src/tinydb/_repl_meta.py`：定义 `META_COMMANDS: dict[str, Callable]` 与分发函数 `handle_meta(line: str, db: Database) -> bool`
- [ ] 4.2 迁移现有 meta 命令（`.exit` / `.quit` / `.help` / `.tables` / `.schema` / `.read`）到注册表
- [ ] 4.3 实现 `.explain <sql>`：调用 `db.explain_plan(sql)` + `plan.format_plan(plan)` 渲染
- [ ] 4.4 实现 `.indexes [table]`：遍历 `catalog.indexes` + `IndexManager` 输出 BTree 元数据
- [ ] 4.5 实现 `.stats`：聚合表数/行数/页数/空闲页数/WAL 大小
- [ ] 4.6 实现 `.timer on|off`：切换模块级 `_TIMER_ENABLED`
- [ ] 4.7 实现 `.format <table|csv|json>`：切换模块级 `_OUTPUT_FORMAT`
- [ ] 4.8 实现 `.color on|off`：切换 ANSI 颜色输出
- [ ] 4.9 更新 `.help` 输出以反映所有新命令

## 5. 整合与 REPL 主循环

- [ ] 5.1 重构 `src/tinydb/repl.py`：将 main / `_interactive_loop` 瘦身；调用 `_repl_io` / `_repl_meta` / `_repl_format`
- [ ] 5.2 整合计时：`.timer on` 时，在 `_run_sql` 结果后追加 `Time: X.XXX ms`
- [ ] 5.3 整合输出格式：`.format` 切换后，下次查询按新格式输出
- [ ] 5.4 在 `repl.py` 入口处输出首次启动提示（"`.help` for commands, `.timer on` for timing"）

## 6. 集成测试

- [ ] 6.1 创建 `tests/integration/test_repl_io_prompt_toolkit.py`：使用 `PromptSession` Patch + stdin 注入 SQL 片段；断言执行成功
- [ ] 6.2 创建 `tests/integration/test_repl_multiline.py`：跨 5 行 SELECT 查询
- [ ] 6.3 创建 `tests/integration/test_repl_color_off.py`：`NO_COLOR=1` 环境下不输出 ANSI 颜色码
- [ ] 6.4 创建 `tests/integration/test_repl_fallback.py`：monkey-patch `prompt_toolkit` 不可导入；REPL 退化为 `input()` 模式
- [ ] 6.5 创建 `tests/integration/test_repl_meta_commands.py`：所有 meta 命令的端到端行为

## 7. 文档

- [ ] 7.1 更新 `README.md`：新增 "REPL" 章节，列出多行/高亮/行编辑与新 meta 命令
- [ ] 7.2 在 `docs/superpowers/specs/` 下新建 `cli-enhancements.md`，汇总 spec 中的公开契约
- [ ] 7.3 更新 `CHANGELOG.md`：新增 `cli-enhancements` 条目

## 8. 最终验证

- [ ] 8.1 运行 `pytest` 完整套件，确认通过且覆盖率 ≥ 92%（保持基线）
- [ ] 8.2 手动冒烟：启动 `tinydb-repl --database /tmp/test.db`，验证多行 / 高亮 / 行编辑 / 新 meta 命令
- [ ] 8.3 验证回退路径：临时移除 `prompt_toolkit` 包，确认 REPL 仍可用（退化模式）
```

## openspec/changes/cli-enhancements/specs/cli-enhancements/spec.md

- Source: openspec/changes/cli-enhancements/specs/cli-enhancements/spec.md
- Lines: 1-142
- SHA256: f49170a4d0aa26ba5efc01f9a648c3a7354e9a50ce67b34f65bcec00c20fc416

[TRUNCATED]

```md
# cli-enhancements

## ADDED Requirements

### Requirement: Interactive REPL provides multi-line input

The tinydb-repl shell SHALL accept multi-line SQL statements terminated by a semicolon. While the input buffer is unterminated (no closing `;`, unmatched quote, or unmatched parenthesis), the REPL MUST display a continuation prompt and accumulate subsequent lines into the same statement.

#### Scenario: Statement spanning multiple lines executes as one query
- **WHEN** a user enters a SELECT statement across three lines, the third ending with `;`
- **THEN** the REPL MUST execute the entire statement as a single query
- **AND** the continuation prompt MUST appear on lines 2 and 3

#### Scenario: Unclosed quote triggers continuation prompt
- **WHEN** a user enters `SELECT 'unterminated` (no closing quote) and presses Enter
- **THEN** the REPL MUST display the continuation prompt on the next line

#### Scenario: Empty line at continuation prompt cancels statement
- **WHEN** a user enters an unterminated statement, then presses Enter on an empty line followed by Ctrl-C
- **THEN** the REPL MUST discard the buffered input and return to the primary prompt

### Requirement: SQL syntax highlighting during input

When the terminal supports ANSI colors (TERM not "dumb", NO_COLOR not set), the REPL SHALL highlight SQL tokens in real time as the user types. Keywords, strings, numbers, operators, and comments SHALL receive distinct visual styling via pygments.

#### Scenario: SQL keywords render in color
- **WHEN** a user types `SELECT * FROM users` in a color-supporting terminal
- **THEN** the REPL MUST render the SQL with at least the SELECT and FROM keywords visually distinguished from identifiers and operators

#### Scenario: NO_COLOR environment disables highlighting
- **WHEN** the `NO_COLOR` environment variable is set to `1`
- **THEN** the REPL MUST NOT emit any ANSI color escape codes in its input or output

#### Scenario: TERM=dumb disables highlighting
- **WHEN** the `TERM` environment variable is `dumb`
- **THEN** the REPL MUST NOT emit ANSI color escape codes

### Requirement: Line editing with Emacs keybindings

The REPL SHALL provide readline-style line editing capabilities including: move to start of line (Ctrl-A), move to end of line (Ctrl-E), delete to end (Ctrl-K), delete word backward (Ctrl-W), and history navigation (up/down arrows).

#### Scenario: Ctrl-A moves cursor to line start
- **WHEN** the user has typed `SELECT * FROM` and presses Ctrl-A
- **THEN** the cursor MUST position at the beginning of the line

#### Scenario: Up arrow recalls previous statement
- **WHEN** the user has previously executed `SELECT 1` and presses Up arrow on an empty prompt
- **THEN** the REPL MUST display `SELECT 1` as the current input

### Requirement: Meta command .explain displays query plan

The REPL SHALL support `.explain <sql>` which parses the SQL into a `LogicalPlan` and renders it as a tree without executing the query. The output MUST use `plan.format_plan()` to produce indented node output.

#### Scenario: .explain SELECT displays plan tree
- **WHEN** a user enters `.explain SELECT * FROM users WHERE age > 18`
- **THEN** the REPL MUST output the LogicalPlan tree (Scan → Filter → Project) without executing the query
- **AND** the output MUST NOT include result rows

#### Scenario: .explain with invalid SQL shows parse error
- **WHEN** a user enters `.explain SELECT FROMM users` (invalid)
- **THEN** the REPL MUST display the parse error message (not a Python traceback)

### Requirement: Meta command .indexes lists index metadata

The REPL SHALL support `.indexes [table]` which lists all indexes in the database. With no argument, all indexes are listed. With a table name, only indexes for that table are listed.

#### Scenario: .indexes lists all indexes
- **WHEN** a user enters `.indexes`
- **THEN** the REPL MUST list each index as `<table>.<column>` with BTree root_page_id and estimated key count

#### Scenario: .indexes users shows only indexes on users
- **WHEN** a user enters `.indexes users`
- **THEN** the REPL MUST list only indexes whose table is `users`

### Requirement: Meta command .stats shows database statistics

The REPL SHALL support `.stats` which displays table count, total row count, page count, free page count, and WAL file size.

#### Scenario: .stats on empty database shows zeros
- **WHEN** a user opens a fresh database and enters `.stats`

```

Full source: openspec/changes/cli-enhancements/specs/cli-enhancements/spec.md
