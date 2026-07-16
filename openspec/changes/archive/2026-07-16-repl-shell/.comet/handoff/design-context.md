# Comet Design Handoff

- Change: repl-shell
- Phase: design
- Mode: compact
- Context hash: 279b656d88cfc75d421ccbee9599132675b39c5ed6527333f43d54b21eda45c3

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/repl-shell/proposal.md

- Source: openspec/changes/repl-shell/proposal.md
- Lines: 1-34
- SHA256: 03e44ccbf7aa6b75bb6c2924b1f9210065b71cf947b7d21952a2908743e58af7

```md
## Why

tinydb MVP 已交付 `Database.execute(sql)` 编程式 API 与一次性 `examples/demo.py` 演示，但缺少日常开发/调试用的交互式 shell：用户必须编写脚本、读文件、逐次调用 `execute()` 才能跑多条 SQL。

补充一个交互式 REPL，能在终端直接跑 SQL、调出历史、查看 schema、批量加载脚本，显著降低"试一下 / 调一下"循环的成本；并使 tinydb 在工具链上更接近 `sqlite3` / `psql` 的使用体感，而不只是 Python 模块。

## What Changes

- 新增 `src/tinydb/repl.py`：单文件实现交互式 REPL 主循环、元命令解析、多行延续、表格化输出、readline 持久化历史。
- 在 `pyproject.toml` 增加 `[project.scripts]`：`tinydb-repl = "tinydb.repl:main"`。
- 新增 `tests/unit/test_repl.py`：覆盖元命令分发、状态机、表格化、history 文件路径解析等纯逻辑。
- 新增 `tests/integration/test_repl_process.py`：spawn 子进程跑 SQL、验证 stdout/stderr。
- `README.md` 增加 `## REPL` 章节，说明启动方式与元命令。
- **不修改** `src/tinydb/{database,executor,pager,parser,...}.py` 任何现有核心模块。

## Capabilities

### New Capabilities

- `repl-shell`：交互式 REPL 的全部行为契约（启动 / 输入循环 / 元命令 / 多行延续 / 输出格式 / 历史持久化 / 退出语义）。

### Modified Capabilities

无。`tinydb-mvp` 已归档的 4 个 delta spec（`python-api` / `sql-minimal-parser` / `storage-engine` / `type-system-basic`）的需求层级不变；REPL 只是现有 Python API 的包装。

## Impact

- **受影响源码**：`src/tinydb/__init__.py`（新增 `from tinydb.repl import main` 可选导出）、`src/tinydb/repl.py`（新增，单文件）、`pyproject.toml`（新增 `[project.scripts]`）。
- **运行时依赖**：零。`repl.py` 仅用 stdlib（`readline`、`os`、`sys`、`pathlib`、`dataclasses`、`tinydb` 自身）。
- **dev 依赖**：无新增。
- **文件 / 平台行为**：
  - Unix-like：`readline` 直接可用，历史写入 `~/.tinydb_history`。
  - Windows：`readline` 不可用，pyreadline3 不在依赖中 → 回退为内置 `input()`（无 readline / 无历史）。这是已知差异，文档明示。
- **测试夹具**：integration 测试 spawn `tinydb-repl` 子进程，用 `pexpect` / `subprocess` 与 stdin/stdout 交互。
- **行数预算**：`repl.py ≤ 350 行`（相对 MVP 模块稍宽，因为 REPL 涉及多组件协调）。
```

## openspec/changes/repl-shell/design.md

- Source: openspec/changes/repl-shell/design.md
- Lines: 1-108
- SHA256: 237d8f9a3baddf32ca7662d2af9dee334c4b5e57885d0f7a2405d256277be330

[TRUNCATED]

```md
## Context

**当前状态：**
- tinydb MVP 已交付：`src/tinydb/database.py` 提供 `Database.execute(sql) -> list[Row]`；用户通过 Python 脚本或 REPL（如 `python3 -i`）调用。
- `examples/demo.py` 是 35 行一次性脚本，硬编码 3 条 INSERT + 2 条 SELECT + 1 条 DELETE，跑完即退出。
- 终端交互场景缺失：每次想试一条 SQL 都得改脚本重启，调试体验差。

**约束：**
- 零运行时依赖（项目 `dependencies = []` 是 MVP 的硬约束）。
- 不修改 `database.py` / `executor.py` / `parser.py` 等核心模块。
- 跨平台：Unix-like + Windows（readline 行为差异）。
- 教学定位：代码可读性优于花哨技巧。

**利益相关方：**
- 直接用户：tinydb 开发者（写 MVP 的同学）、教学场景的讲师与学生。
- 间接用户：希望快速验证 SQL 语法的潜在贡献者。

## Goals / Non-Goals

**Goals：**
- 通过 `tinydb-repl` console script 启动 REPL，pip install 后即可用。
- 单文件 `src/tinydb/repl.py`（≤ 350 行）实现完整 REPL。
- 复用现有 `Database.execute(sql)`：REPL 不重做 SQL 解析/执行，只是交互壳。
- 元命令：`.exit` / `.quit` / `.help` / `.tables` / `.schema <name>` / `.read <file>`。
- 多行延续：检测未闭合的单引号 `'...` 或左括号 `(...`，进入续行模式（`...>` prompt）直到闭合。
- 持久化历史：Unix 上 `~/.tinydb_history`；Windows 上 fallback 到内存。
- 表格化输出：SELECT 结果按列名对齐（auto-fit，列宽 cap 30）。

**Non-Goals：**
- 不实现语法高亮 / 自动补全（需 prompt_toolkit，破 zero-dep）。
- 不实现 `.mode` 输出格式切换 / `.import` / `.dump` / `.export`。
- 不修改 SQL 语法或执行器行为。
- 不引入 REPL 远程协议 / 多用户 / 协作。
- 不做 Windows readline fallback 完整模拟（pyreadline3 仍是用户自选依赖）。

## Decisions

### D1. REPL 复用 `Database.execute`，不在 REPL 内做 token 累积

- **方案 A（采纳）**：REPL 持一个 `Database` 实例；逐行读入；用启发式判断"语句未闭合"则 append 到 buffer；闭合后一次性 `db.execute(buffer)`。
- **方案 B（拒绝）**：REPL 内 tokenize → 自己管理 `StatementList` 流式执行。
- **理由**：MVP 的 `Database.execute` 已支持 `StatementList`（多 `;` 分隔），增量调用就够。重复实现解析器会偏离"REPL 是壳"的定位。

### D2. 多行延续启发式：未闭合 `'` 或 `(` 触发续行

- **方案 A（采纳）**：扫描当前 buffer，若 `' ` 数为奇数 或 `(` 数 > `)` 数，进入 `...>` prompt 继续累加，直到闭合或 `.exit`。
- **方案 B（拒绝）**：复用 tokenizer 在 readline 之外流式 token 化。
- **理由**：方案 A 简洁（20 行实现），对 MVP SQL 子集足够；边界场景（注释含 `'`、转义 `''`）由 tokenizer 在 execute 阶段处理，REPL 不重复。

### D3. 元命令分发：`.` 前缀 → 内部 dispatcher，**不进** tokenizer

- **方案 A（采纳）**：行首剥离前导空白，若以 `.` 开头则走 `_handle_meta()`；否则走 SQL 路径。
- **方案 B（拒绝）**：把 `.tables` 也塞进 SQL 语法，扩展 parser。
- **理由**：元命令不是 SQL；放进 parser 会污染 AST 形状。剥离后 SQL 路径保持纯净。

### D4. `.schema <name>` 输出用回向生成的 DDL 字符串（基于 catalog JSON）

- **方案 A（采纳）**：从 `Database.catalog.tables[name].schema` 读取，格式化成 `CREATE TABLE name(col1 TYPE1, col2 TYPE2);`。
- **方案 B（拒绝）**：直接打印 catalog JSON 原文。
- **理由**：DDL 形式对用户更友好，与 `CREATE TABLE` 输入对称。

### D5. 持久化历史用 `~/.tinydb_history`（Unix）；Windows 静默 fallback

- **方案 A（采纳）**：Unix 上 `readline.read_history_file()` / `write_history_file()`；Windows 上捕获 `ImportError`，回退到内置 `input()`，每次启动无历史但功能可用。
- **方案 B（拒绝）**：Windows 上要求 `pip install pyreadline3`。
- **理由**：不破 zero-dep 原则；Windows 用户本来就在降级体验中（无 Unix terminal line discipline），能跑即可。

### D6. 表格化输出：auto-fit 列宽，cap 30 字符

- **方案 A（采纳）**：列宽 = min(max(len(header), max(value)), 30)。超长值截断加 `…`。空结果集输出 `(no rows)`。
- **方案 B（拒绝）**：固定宽度 20。
- **理由**：MVP 表通常列少（≤ 5）、值短；auto-fit 比固定宽更易读；30 cap 防止单 cell 撑爆终端。

### D7. 错误显示：`ERROR: <ExceptionClass>: <message>` 单行格式

- **方案 A（采纳）**：捕获 `TinydbError` / `Exception`，打印单行；不打印 traceback（除非 `.debug on`）。
- **方案 B（拒绝）**：完整 traceback。
- **理由**：REPL 用户要快速看到"哪条 SQL 错"，traceback 是噪音。详细错误由 `--debug` flag 触发。

### D8. `__init__.py` 不强制导出 `repl` 模块

```

Full source: openspec/changes/repl-shell/design.md

## openspec/changes/repl-shell/tasks.md

- Source: openspec/changes/repl-shell/tasks.md
- Lines: 1-65
- SHA256: ea922247cebdded335f4bbb68c3ad062b5c05efaf21a2f72970d53be1cf783ae

```md
# Tasks: repl-shell

> **实施起点**：基于 `tinydb-mvp` 已归档的核心模块（`Database.execute`、`Row`、`Catalog`）实现 REPL 壳层；不修改任何 MVP 模块。
> **TDD 模式**：任务 7.1 / 7.2 采用 TDD（RED → GREEN）；其余任务按实现 → 测试增量验证。
> **预算红线**：`src/tinydb/repl.py ≤ 350 行`；超出需拆分子包（`repl/` 子目录）。

## 1. 骨架与入口

- [ ] 1.1 创建 `src/tinydb/repl.py`：模块级 docstring + `def main() -> int:` 入口签名（暂返回 0）
- [ ] 1.2 `pyproject.toml` 添加 `[project.scripts]`：`tinydb-repl = "tinydb.repl:main"`
- [ ] 1.3 验证安装：`pip install -e ".[dev]"` 后 `tinydb-repl --help` 能找到入口

## 2. 主循环与 SQL 路径

- [ ] 2.1 实现 `_make_prompt(db) -> str`：当前数据库路径（`:memory:` 时显示 `:memory:`）
- [ ] 2.2 实现 `_read_one_statement() -> str | None`：单行读入；EOF 返回 `None`
- [ ] 2.3 实现 `_run_sql(db, sql) -> None`：调用 `db.execute(sql)`，打印 `Row(...)` repr 或 `(no rows)`；捕 `Exception` 打印 `ERROR: <Class>: <msg>` 单行
- [ ] 2.4 `main()` 串接 prompt → 读 → execute → 循环；EOF/Ctrl-D 正常退出码 0

## 3. 多行延续

- [ ] 3.1 实现 `_is_unterminated(buf: str) -> bool`：扫描 `'` 数为奇 或 `(` > `)` → True
- [ ] 3.2 `main()` 改为 buffer 累积：未终止则进入 `...>` 续行 prompt，继续 read；终止则执行并清空 buffer

## 4. 元命令分发

- [ ] 4.1 实现 `_handle_meta(line: str, db) -> bool`：行首 `.` 进入；返回 True 表示已处理（不进 SQL 路径），False 表示不是元命令
- [ ] 4.2 实现 `.exit` / `.quit`：抛 `_ExitRepl` 内部异常让 main 退出
- [ ] 4.3 实现 `.help`：打印元命令清单与快捷键
- [ ] 4.4 实现 `.tables`：遍历 `db.catalog.tables` 输出表名（一行一个）
- [ ] 4.5 实现 `.schema <name>`：从 `db.catalog.get_table(name)` 读 schema，格式化成 `CREATE TABLE name(c1 T1, c2 T2);` 输出；未知表名报 `ERROR: no such table: <name>`
- [ ] 4.6 实现 `.read <path>`：读文件按 `;` 切分，逐条 `_run_sql`；文件不存在报 `ERROR: cannot read file: <path>`

## 5. 历史持久化

- [ ] 5.1 实现 `_setup_history() -> None`：尝试 `import readline`；成功则 `read_history_file(os.path.expanduser('~/.tinydb_history'))`（文件不存在不报错）
- [ ] 5.2 实现 `_save_history() -> None`：Unix 路径下 `write_history_file`，catch `OSError` 静默（磁盘满 / 权限不足）
- [ ] 5.3 Windows ImportError 时静默 fallback；`main()` 跳过 readline 调用，直接用内置 `input()`

## 6. 输出格式

- [ ] 6.1 实现 `_format_table(rows: list[Row]) -> str`：列宽 = `min(max(len(h), max(len(str(v)))), 30)`；header + `---` 分隔 + 行；超长值截断加 `…`
- [ ] 6.2 在 `_run_sql` 中：若结果是 `list[Row]` 且非空，调用 `_format_table`；空结果显示 `(no rows)`
- [ ] 6.3 单条错误格式：`ERROR: ParseError: line 1, col 5: ...`（与现有 exception 形状对齐）

## 7. 测试

- [ ] 7.1 `tests/unit/test_repl.py`：覆盖元命令分发、schema 回向格式化、表格化列宽算法、多行未终止判断、history 路径展开
- [ ] 7.2 `tests/integration/test_repl_process.py`：`subprocess.Popen(['tinydb-repl', tmp_db])`，stdin 喂 SQL → 读 stdout 断言；至少 4 场景：基本 CRUD、`.tables`、`.read`、EOF 退出码 0

## 8. CLI 旗标

- [ ] 8.1 `main(argv: list[str] | None = None)` 支持 `--database <path>` flag；默认 `:memory:`
- [ ] 8.2 单元测试：`--database` 解析 + 路径扩展到 `Path` 校验存在性

## 9. 文档

- [ ] 9.1 `README.md` 增加 `## REPL` 章节：启动示例（`tinydb-repl` / `tinydb-repl data.db`）、元命令清单、历史文件位置
- [ ] 9.2 `examples/repl_smoke.sh`：bash 脚本，`printf` 喂 4 条 SQL 给 `tinydb-repl` 并 grep 关键输出（人眼/CI smoke 用）

## 10. 行数审计与最终自检

- [ ] 10.1 `wc -l src/tinydb/repl.py` ≤ 350；超出则拆 `repl/` 子包
- [ ] 10.2 `pytest --cov=tinydb --cov-report=term-missing --cov-fail-under=85` 全绿，coverage 不退化（保持 ≥ 90%）
- [ ] 10.3 `openspec validate repl-shell --strict` PASS
- [ ] 10.4 `tinydb-repl data.db < smoke.sql` 与手工交互输出一致
```

## openspec/changes/repl-shell/specs/repl-shell/spec.md

- Source: openspec/changes/repl-shell/specs/repl-shell/spec.md
- Lines: 1-162
- SHA256: 72c63e073c0dc9ec6eda826552412aa743c4c5842180f5fc70cd1c7cc8293114

[TRUNCATED]

```md
## ADDED Requirements

### Requirement: REPL provides interactive SQL loop

The REPL MUST start with a `tinydb>` prompt, accept SQL input line by line, and execute each completed statement through the existing `Database.execute(sql)` API. SELECT results MUST be printed; DDL/DML MUST print `OK`. Empty result sets MUST display `(no rows)`. The REPL MUST exit with status code 0 on EOF (Ctrl-D) or `.exit` / `.quit`.

#### Scenario: Basic CRUD round-trip

- **WHEN** user enters `CREATE TABLE t(id INT);` then `INSERT INTO t(id) VALUES (1);` then `SELECT * FROM t;` at the prompt
- **THEN** REPL prints `OK` after CREATE, `OK` after INSERT, and one row (`Row(id=1)`) after SELECT

#### Scenario: Empty result message

- **WHEN** user enters `SELECT * FROM empty_table;` against a table with no rows
- **THEN** REPL prints `(no rows)`

#### Scenario: Exit on Ctrl-D

- **WHEN** user sends EOF (Ctrl-D) on stdin
- **THEN** REPL exits with status code 0

#### Scenario: Exit on .exit

- **WHEN** user enters `.exit`
- **THEN** REPL exits with status code 0

#### Scenario: Exit on .quit

- **WHEN** user enters `.quit`
- **THEN** REPL exits with status code 0

### Requirement: REPL supports multi-line SQL continuation

When the current input buffer contains an unterminated single-quoted string (odd count of `'`) or unmatched open parentheses (`(` count greater than `)` count), the REPL MUST enter continuation mode displaying a `...>` prompt, accumulate further input until the buffer is balanced, and execute only the final complete statement(s).

#### Scenario: Multi-line INSERT with VALUES spanning lines

- **WHEN** user enters `INSERT INTO t(id, name) VALUES (` then on next line `  1, 'alice');`
- **THEN** REPL shows `...>` after the first line, executes the combined statement on the second line, and prints `OK`

#### Scenario: Multi-line text literal

- **WHEN** user enters `INSERT INTO t(name) VALUES ('alice` then on next line ` smith');`
- **THEN** REPL waits for closing quote, executes the combined statement, prints `OK`

### Requirement: REPL exposes meta-commands

Lines starting with `.` MUST be dispatched to meta-command handlers and MUST NOT be passed to the SQL parser. Meta-commands MUST support: `.exit` / `.quit`, `.help`, `.tables`, `.schema <name>`, `.read <path>`. Unknown meta-commands MUST print `ERROR: unknown command: <name>`.

#### Scenario: .help lists meta-commands

- **WHEN** user enters `.help`
- **THEN** REPL prints a list including `.exit`, `.quit`, `.help`, `.tables`, `.schema`, `.read`

#### Scenario: .tables lists table names

- **WHEN** user has created tables `users` and `orders` then enters `.tables`
- **THEN** REPL prints `users` and `orders` (one per line, in any order)

#### Scenario: .schema prints CREATE TABLE statement

- **WHEN** user has created `users(id INT, name TEXT)` then enters `.schema users`
- **THEN** REPL prints `CREATE TABLE users(id INT, name TEXT);`

#### Scenario: .schema unknown table

- **WHEN** user enters `.schema ghost`
- **THEN** REPL prints `ERROR: no such table: ghost`

#### Scenario: .read executes SQL file

- **WHEN** user creates a file `seed.sql` containing `CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1);` then enters `.read seed.sql`
- **THEN** REPL executes both statements, prints `OK` for each, and returns to the prompt

#### Scenario: .read missing file

- **WHEN** user enters `.read nope.sql` and the file does not exist
- **THEN** REPL prints `ERROR: cannot read file: nope.sql`

#### Scenario: Unknown meta-command

```

Full source: openspec/changes/repl-shell/specs/repl-shell/spec.md
