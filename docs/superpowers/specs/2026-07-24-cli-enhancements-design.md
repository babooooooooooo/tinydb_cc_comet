---
comet_change: cli-enhancements
role: technical-design
canonical_spec: openspec
---

# CLI 功能增强技术设计（CLI Enhancements Design）

> 本设计文档是 `cli-enhancements` change 的深度技术细化。OpenSpec 高层架构在 `openspec/changes/cli-enhancements/design.md`；本文档给出实现细节、边界条件、测试策略与缓解方案。

## Context（背景）

`tinydb-repl` 当前是 302 行的 stdlib-only shell（`src/tinydb/repl.py`），使用 `input()` 单行读取 + 手工 SQL 终止符检测。能力局限：
- 无多行编辑：长查询只能反复回车
- 无语法高亮：黑白字符流阅读吃力
- 无行编辑：无法用方向键/Emacs 快捷键修改已输入内容
- 无高级 meta 命令：无法查看 LogicalPlan、索引元数据、统计信息、执行计时
- 无输出格式切换：所有结果只能以表格渲染

随着聚合、JOIN、并发控制等能力上线，REPL 已无法满足开发者调试、运维巡检、benchmark 等场景。

OpenSpec change `cli-enhancements` 新增：
- 多行编辑（基于 `prompt_toolkit>=3.0`）
- 语法高亮（基于 `pygments>=2.18`，`PygmentsLexer` 桥接）
- 行编辑（依赖 `prompt_toolkit` Emacs/Vi 键绑定）
- 新 meta 命令：`.explain` / `.indexes` / `.stats` / `.timer` / `.format` / `.color`
- 输出格式：`table` / `csv` / `json`
- 持久化历史（`~/.tinydb_history`，通过 `prompt_toolkit.FileHistory` 接管）
- 优雅降级（`prompt_toolkit` 不可导入时退回 stdlib `input()` 模式）

## Goals / Non-Goals

**Goals（目标）：**
- 多行 SQL 输入 + 跨行编辑 + 自动括号闭合（prompt_toolkit 默认行为）
- SQL 实时语法高亮（关键字/字符串/数字/运算符/注释）
- Emacs 键绑定 + 历史搜索（Ctrl-R）
- 6 个新 meta 命令覆盖调试/巡检/计时/格式化
- 输出格式 `table` / `csv` / `json` 可切换
- 持久化历史跨 session 保留
- stdlib-only fallback（`prompt_toolkit` 不可用时）
- 单文件 ≤ 800 行（拆分 `repl.py` 至 4 个模块）

**Non-Goals：**
- Windows 平台完整测试（focus Linux/macOS）
- 远程/网络 REPL（SSH/Web UI）
- SQL 自动补全（候选表/列提示）
- `.tinydbrc` 持久化配置
- `.explain ANALYZE`（执行 + 真实耗时）
- `tinydb dump` / `tinydb import` 等其他 CLI 子命令

## Architecture

### 新增模块清单

```
src/tinydb/repl.py             [KEEP] main + 入口 + _interactive_loop (~150 行)
src/tinydb/_repl_io.py         [NEW] ~250 行：ReplIO + FallbackIO + 历史
src/tinydb/_repl_meta.py       [NEW] ~300 行：META_COMMANDS 注册表 + 命令实现
src/tinydb/_repl_format.py     [NEW] ~150 行：table/csv/json 格式化
tests/unit/test_repl_meta.py   [NEW] ~150 行：meta 命令分发
tests/unit/test_repl_format.py [NEW] ~100 行：格式化
tests/integration/test_repl_io.py [NEW] ~200 行：prompt_toolkit 端到端 + fallback
```

### 依赖更新

`pyproject.toml`：

```toml
[project]
dependencies = [
    "pygments>=2.18",
    "prompt_toolkit>=3.0.0",
]
```

## Module Spec: `_repl_io.py`

### 类层次

```python
class ReplIOProtocol(Protocol):
    """REPL I/O 抽象接口，便于测试 mock。"""

    def read_statement(self) -> str | None:
        """读取一条 SQL 语句（多行累积）；EOF 返回 None。"""

    def add_history(self, statement: str) -> None:
        """把已执行语句加入历史。"""

    def save_history(self) -> None:
        """退出时保存历史到磁盘。"""


class PromptToolkitReplIO:
    """基于 prompt_toolkit 的实现（首选路径）。"""

    def __init__(self, db_path: str, history_path: Path, color: bool) -> None:
        self._session = PromptSession(
            lexer=PygmentsLexer(SqlLexer) if color else None,
            history=FileHistory(str(history_path)),
            multiline=True,
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
        )
        self._color = color
        self._continuation = HTML("<ansigray>...> </ansigray>")

    def read_statement(self) -> str | None:
        try:
            prompt = self._make_prompt()
            text = self._session.prompt(prompt, multiline=True)
            return text
        except EOFError:
            return None
        except KeyboardInterrupt:
            return ""

    def add_history(self, statement: str) -> None:
        if statement.strip():
            self._session.history.append_string(statement)


class FallbackReplIO:
    """prompt_toolkit 不可用时的退化实现（stdlib-only）。"""

    def __init__(self, db_path: str, history_path: Path) -> None:
        self._db_path = db_path
        self._history = []
        self._buf = ""

    def read_statement(self) -> str | None:
        try:
            prompt = CONTINUATION_PROMPT if self._buf else _make_prompt(self._db_path)
            line = input(prompt)
        except EOFError:
            return None
        except KeyboardInterrupt:
            self._buf = ""
            print("\n(Use .exit or Ctrl-D to exit)")
            return ""
        if not line.strip() and not self._buf:
            return ""
        self._buf += line + "\n"
        if _is_unterminated(self._buf):
            return ""  # 累积中
        return self._consume_buf()

    def _consume_buf(self) -> str:
        text = self._buf.rstrip("\n")
        self._buf = ""
        return text
```

### 模块级 `try/except ImportError`

```python
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.lexers import PygmentsLexer
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from pygments.lexers.sql import SqlLexer

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False
```

`main()` 检测 `_HAS_PROMPT_TOOLKIT`：True 时用 `PromptToolkitReplIO`，False 时用 `FallbackReplIO` 并打印警告。

### NO_COLOR / TERM 检测

```python
def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    term = os.environ.get("TERM", "")
    if term == "dumb":
        return False
    return True
```

## Module Spec: `_repl_format.py`

```python
"""结果格式化（table/csv/json）。"""
from __future__ import annotations

import csv
import io
import json
from typing import Literal

from tinydb.database import Row

FormatName = Literal["table", "csv", "json"]


def format_rows(rows: list[Row], fmt: FormatName) -> str:
    """根据 fmt 格式化 rows；空 rows 返回 "(no rows)"。"""
    if not rows:
        return "(no rows)"
    if fmt == "table":
        return _format_table(rows)
    if fmt == "csv":
        return _format_csv(rows)
    if fmt == "json":
        return _format_json(rows)
    raise ValueError(f"unknown format: {fmt}")


def _format_table(rows: list[Row]) -> str:
    """迁移自 repl.py:_format_table。"""
    ...


def _format_csv(rows: list[Row]) -> str:
    buf = io.StringIO()
    columns = list(rows[0].columns)
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row.values)
    return buf.getvalue().rstrip("\n")


def _format_json(rows: list[Row]) -> str:
    columns = list(rows[0].columns)
    return json.dumps(
        [dict(zip(columns, row.values)) for row in rows],
        ensure_ascii=False,
        default=str,
    )
```

## Module Spec: `_repl_meta.py`

### 命令注册表

```python
from typing import Callable

from tinydb.database import Database


class MetaCommand:
    """meta 命令描述符。"""

    def __init__(
        self,
        name: str,
        handler: Callable[[list[str], Database], bool],
        help_text: str,
        takes_arg: bool = False,
    ) -> None:
        self.name = name
        self.handler = handler
        self.help_text = help_text
        self.takes_arg = takes_arg

    def __call__(self, args: list[str], db: Database) -> bool:
        if self.takes_arg and not args:
            print(f"ERROR: missing argument for .{self.name}", file=sys.stderr)
            return True
        return self.handler(args, db)


META_COMMANDS: dict[str, MetaCommand] = {
    "exit": MetaCommand("exit", _cmd_exit, "exit the REPL"),
    "quit": MetaCommand("quit", _cmd_exit, "exit the REPL"),
    "help": MetaCommand("help", _cmd_help, "show this help"),
    "tables": MetaCommand("tables", _cmd_tables, "list tables"),
    "schema": MetaCommand("schema", _cmd_schema, "show CREATE TABLE <name>", takes_arg=True),
    "read": MetaCommand("read", _cmd_read, "execute a SQL file", takes_arg=True),
    "explain": MetaCommand("explain", _cmd_explain, "show query plan for <sql>", takes_arg=True),
    "indexes": MetaCommand("indexes", _cmd_indexes, "list indexes [table]"),
    "stats": MetaCommand("stats", _cmd_stats, "show database statistics"),
    "timer": MetaCommand("timer", _cmd_timer, "toggle execution timing: .timer on|off"),
    "format": MetaCommand("format", _cmd_format, "switch output format: .format table|csv|json"),
    "color": MetaCommand("color", _cmd_color, "toggle color output: .color on|off"),
}


def handle_meta(line: str, db: Database, state: ReplState) -> bool:
    """解析并分发 meta 命令；返回 True 表示已处理（无论成功失败）。"""
    stripped = line.lstrip()
    if not stripped.startswith("."):
        return False
    parts = stripped.split(maxsplit=1)
    cmd = parts[0].lstrip(".")
    arg = parts[1].strip() if len(parts) == 2 else ""
    if cmd not in META_COMMANDS:
        print(f"ERROR: unknown command: .{cmd}", file=sys.stderr)
        return True
    return META_COMMANDS[cmd](arg.split() if arg else [], db)
```

### 各命令实现

```python
def _cmd_help(args: list[str], db: Database) -> bool:
    print("Meta commands:")
    for cmd in META_COMMANDS.values():
        if cmd.name in ("exit", "quit"):
            continue  # 合并显示
        print(f"  .{cmd.name:<10} {cmd.help_text}")
    print("  .exit | .quit  exit the REPL")
    print("Shortcuts: Ctrl-D exits; Ctrl-C clears the current buffer.")
    return True


def _cmd_explain(args: list[str], db: Database) -> bool:
    sql = " ".join(args)
    try:
        plan = db.explain_plan(sql)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return True
    print("Plan:")
    print(format_plan(plan))
    return True


def _cmd_indexes(args: list[str], db: Database) -> bool:
    table_filter = args[0] if args else None
    indexes = db.index_manager.all_indexes()  # 假设此 API 存在
    for idx in indexes:
        if table_filter and idx.table != table_filter:
            continue
        print(f"{idx.table}.{idx.column}  root_page={idx.root_page_id}  "
              f"keys≈{idx.estimated_count}")
    return True


def _cmd_stats(args: list[str], db: Database) -> bool:
    catalog = db.catalog
    n_tables = len(catalog.tables)
    n_rows = sum(_count_rows(db, t) for t in catalog.tables)
    n_pages = db.pager.page_count()
    n_free = db.pager.free_list_length()
    wal_size = _wal_size(db)
    print(f"Tables:     {n_tables}")
    print(f"Rows:       {n_rows}")
    print(f"Pages:      {n_pages}")
    print(f"Free pages: {n_free}")
    print(f"WAL:        {wal_size} bytes")
    return True


def _cmd_timer(args: list[str], db: Database) -> bool:
    if not args or args[0] not in ("on", "off"):
        print("ERROR: .timer on|off", file=sys.stderr)
        return True
    state.timer_enabled = args[0] == "on"
    print(f"Timer: {'on' if state.timer_enabled else 'off'}")
    return True


def _cmd_format(args: list[str], db: Database) -> bool:
    if not args or args[0] not in ("table", "csv", "json"):
        print("ERROR: .format table|csv|json", file=sys.stderr)
        return True
    state.output_format = args[0]
    print(f"Format: {state.output_format}")
    return True


def _cmd_color(args: list[str], db: Database) -> bool:
    if not args or args[0] not in ("on", "off"):
        print("ERROR: .color on|off", file=sys.stderr)
        return True
    state.color_enabled = args[0] == "on"
    print(f"Color: {'on' if state.color_enabled else 'off'}")
    return True
```

### 模块级状态

```python
# 模块级单例状态（简单场景可接受；future refactor 可注入 ReplState 实例）
class ReplState:
    """REPL 运行期状态。"""

    def __init__(self) -> None:
        self.timer_enabled: bool = False
        self.output_format: FormatName = "table"
        self.color_enabled: bool = True
```

## Module Spec: `repl.py` 重构

```python
"""Interactive SQL shell for tinydb; thin entry that delegates to _repl_io/_repl_meta/_repl_format."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from tinydb.database import Database
from tinydb._repl_io import PromptToolkitReplIO, FallbackReplIO, _HAS_PROMPT_TOOLKIT
from tinydb._repl_meta import handle_meta, _cmd_exit
from tinydb._repl_format import format_rows
from tinydb.errors import ConstraintViolation

USAGE = "Usage: tinydb-repl [--database PATH]"
HISTORY_PATH = "~/.tinydb_history"


class _ExitRepl(Exception):
    """Internal control flow for .exit/.quit."""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        print(USAGE)
        return 0
    if not args:
        db_path = ":memory:"
    elif len(args) == 2 and args[0] == "--database":
        db_path = os.path.expanduser(args[1])
    else:
        print(f"ERROR: invalid argument: {args[0]}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    history_path = Path(os.path.expanduser(HISTORY_PATH))
    state = ReplState()

    if _HAS_PROMPT_TOOLKIT:
        io = PromptToolkitReplIO(db_path, history_path, state.color_enabled)
    else:
        print("WARNING: prompt_toolkit not available; falling back to input() mode",
              file=sys.stderr)
        io = FallbackReplIO(db_path, history_path)

    db = Database(db_path)
    try:
        return _interactive_loop(db, io, state)
    finally:
        io.save_history()
        db.close()


def _interactive_loop(db: Database, io: ReplIOProtocol, state: ReplState) -> int:
    while True:
        text = io.read_statement()
        if text is None:
            return 0  # EOF
        if not text.strip():
            continue
        if text.lstrip().startswith("."):
            try:
                handle_meta(text, db, state)
            except _ExitRepl:
                return 0
            continue
        io.add_history(text)
        _run_sql(db, text, state)


def _run_sql(db: Database, sql: str, state: ReplState) -> None:
    from tinydb.parser import Select, parse
    from tinydb.tokenizer import tokenize

    try:
        statements = parse(tokenize(sql)).statements
        last_is_select = bool(statements) and isinstance(statements[-1], Select)
    except Exception:
        last_is_select = False

    import time
    start = time.perf_counter() if state.timer_enabled else None
    try:
        rows = db.execute(sql)
    except Exception as exc:
        message = _format_exception(exc)
        print(message, file=sys.stderr)
        return
    elapsed = (time.perf_counter() - start) if start else None

    if not last_is_select:
        print("OK")
    elif not rows:
        print("(no rows)")
    else:
        print(format_rows(rows, state.output_format))

    if elapsed is not None:
        print(f"Time: {elapsed * 1000:.3f} ms")


def _format_exception(exc: Exception) -> str:
    if isinstance(exc, ConstraintViolation):
        return f"ERROR: {exc}"
    return f"ERROR: {type(exc).__name__}: {exc}"
```

## Test Plan

### 单元测试

| 文件 | 用例 |
|------|------|
| `tests/unit/test_repl_meta.py` | (1) 命令分发：`.help` 返回所有命令; (2) `.explain` 调 `explain_plan` 不执行; (3) `.timer on` 切换状态; (4) `.format csv` 切换状态; (5) `.color off` 切换状态; (6) `.explain <invalid>` 友好错误; (7) `.format markdown` 未知格式错误; (8) `.indexes` 列出 |
| `tests/unit/test_repl_format.py` | (1) `table` 格式对齐; (2) `csv` 格式 RFC 4180; (3) `json` 格式数组; (4) `format_rows([], 'csv')` 返回 "(no rows)"; (5) `format_rows(rows, 'unknown')` 抛 ValueError |

### 集成测试

| 文件 | 用例 |
|------|------|
| `tests/integration/test_repl_io_prompt_toolkit.py` | 用 `PromptSession` + stdin/stdout Patch 注入 SQL；断言执行成功 + 输出包含期望行 |
| `tests/integration/test_repl_multiline.py` | 输入跨 5 行 SELECT；断言拼接 + 执行成功 + 结果正确 |
| `tests/integration/test_repl_color_off.py` | `NO_COLOR=1` + `monkeypatch.delenv("NO_COLOR")`；断言输出无 ANSI 码 |
| `tests/integration/test_repl_fallback.py` | `monkeypatch.setattr(_repl_io, '_HAS_PROMPT_TOOLKIT', False)`；启动 REPL；断言警告信息 + 单行 input() 模式可用 |
| `tests/integration/test_repl_meta_commands.py` | 所有 meta 命令端到端（创建表 → `.indexes` → `.stats` → `.format csv` → `.timer on` → `.explain`） |

### 手动冒烟

```bash
tinydb-repl --database /tmp/test.db
# 多行 SELECT
# .explain SELECT ...
# .indexes
# .stats
# .timer on  → 下一条 SQL 后跟 Time 行
# .format csv → 切换输出格式
# .color off → 关闭高亮
# Ctrl-C 清空缓冲区
# Ctrl-D 退出
```

## Module Spec: 命令输出示例

### `.explain`

```
tinydb> .explain SELECT * FROM users WHERE age > 18
Plan:
  Scan(users)
    Filter: age > 18
      Project: *
```

### `.indexes`

```
tinydb> .indexes
users.id  root_page=42  keys≈1000
users.age  root_page=43  keys≈1000
orders.user_id  root_page=44  keys≈5000
```

### `.stats`

```
tinydb> .stats
Tables:     3
Rows:       15234
Pages:      87
Free pages: 12
WAL:        4096 bytes
```

### `.timer on` + SELECT

```
tinydb> .timer on
Timer: on
tinydb> SELECT COUNT(*) FROM users;
  count
-------
   1000
Time: 4.213 ms
```

### `.format csv`

```
tinydb> .format csv
Format: csv
tinydb> SELECT id, name FROM users LIMIT 2;
id,name
1,Alice
2,Bob
```

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| prompt_toolkit 与现有 input() 行为差异 | FallbackReplIO 保持现有体验；新行为仅在 prompt_toolkit 可用时启用 |
| 终端不支持颜色 / TERM=dumb | `_color_enabled()` 检测；提示信息明确 |
| 跨平台键绑定差异 | 仅 Linux/macOS 测试；Windows 留作 follow-up |
| .explain 对无效 SQL 抛异常 | 捕获 ParseError/TokenizerError 并友好展示 |
| pygments lexer 对 tinydb 方言支持有限 | 仅高亮标准 SQL token；tinydb 方言扩展留作 follow-up |
| 历史文件权限/路径 | `os.path.expanduser` 解析 `~`；不存在则创建 |
| Module 单例状态 ReplState 难测试 | 命令 handler 接受 `state: ReplState` 参数；测试可构造临时实例 |
| 大查询历史膨胀 | `HISTORY_LENGTH = 1000`（保留现有值） |
| `.indexes` 需要 `IndexManager.all_indexes()` API | 可能需要在 IndexManager 添加新方法（task 4.4 范围） |
| `.stats` 调用 `_count_rows(db, t)` 慢（全表扫描） | v1 仅在用户显式 `.stats` 时调用；可接受 |

## Open Questions（Q1-Q4）

- **Q1**：`.explain ANALYZE` 是否在 v1？**决策**：推迟到 v2
- **Q2**：是否支持 `.format markdown/html`？**决策**：v1 仅 table/csv/json
- **Q3**：prompt_toolkit FileHistory 接管后是否仍支持 readline 格式导入？**决策**：不导入；HISTORY_PATH 兼容但内容从空开始
- **Q4**：是否在 REPL 启动时自动 `.timer on`？**决策**：默认 off；首次启动打印提示

## Migration Plan（迁移计划）

无 schema 迁移。新增依赖 + 新增 meta 命令；现有 REPL 用户在升级后获得多行/高亮/行编辑能力，无需额外配置。

回滚策略：移除 `pygments` / `prompt_toolkit` 依赖即可退回 302 行最小 REPL（FallbackReplIO 路径）。

## Verification Strategy（验证策略）

- `pytest` 全套通过（≥ 92% 覆盖率）
- prompt_toolkit 路径 + Fallback 路径独立通过测试
- 手动冒烟：所有 6 个新 meta 命令 + 3 种输出格式 + 多行查询
- 回退冒烟：临时卸载 prompt_toolkit 后 REPL 仍可用