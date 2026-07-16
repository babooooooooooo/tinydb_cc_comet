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