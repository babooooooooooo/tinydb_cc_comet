"""REPL meta 命令注册表与实现.

模块级单例:
    META_COMMANDS — 12 个 meta 命令的注册表
    ReplState     — REPL 运行期状态(timer/format/color)

对外 API:
    handle_meta(line, db, state) — 解析并分发;.exit/.quit 抛 _ExitReplSignal
    MetaCommand                  — 命令描述符 dataclass
    _cmd_*                       — 单命令实现(测试可单独调用)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List

from tinydb._repl_format import FormatName, format_rows  # 仅类型契约
from tinydb.database import Database


VALID_OUTPUT_FORMATS: tuple[str, ...] = ("table", "csv", "json")
MAX_READ_FILE_BYTES = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# ReplState — 运行期状态
# ---------------------------------------------------------------------------

@dataclass
class ReplState:
    """REPL 运行期状态.timer/format/color 在会话内可由 meta 命令切换."""

    timer_enabled: bool = False
    output_format: FormatName = "table"
    color_enabled: bool = True


# ---------------------------------------------------------------------------
# MetaCommand — 命令描述符
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetaCommand:
    name: str
    handler: Callable[[List[str], Database, ReplState], bool]
    help_text: str
    takes_arg: bool = False

    def __call__(self, args: List[str], db: Database, state: ReplState) -> bool:
        if self.takes_arg and not args:
            print(f"ERROR: missing argument for .{self.name}", file=sys.stderr)
            return True
        return self.handler(args, db, state)


# ---------------------------------------------------------------------------
# 退出信号
# ---------------------------------------------------------------------------

class _ExitReplSignal(Exception):
    """内部控制流:被 .exit / .quit 抛出,主循环捕获并 0 退出."""


# ---------------------------------------------------------------------------
# 现有命令实现(迁移 + 小调整)
# ---------------------------------------------------------------------------

def _cmd_exit(args: List[str], db: Database, state: ReplState) -> bool:
    raise _ExitReplSignal


def _cmd_help(args: List[str], db: Database, state: ReplState) -> bool:
    print("Meta commands:")
    for cmd in META_COMMANDS.values():
        if cmd.name in ("exit", "quit"):
            continue
        print(f"  .{cmd.name:<10} {cmd.help_text}")
    print("  .exit | .quit    exit the REPL")
    print("Shortcuts: Ctrl-D exits; Ctrl-C clears the current buffer.")
    return True


def _cmd_tables(args: List[str], db: Database, state: ReplState) -> bool:
    for name in sorted(db.catalog.tables):
        print(name)
    return True


def _cmd_schema(args: List[str], db: Database, state: ReplState) -> bool:
    if not args:
        print("ERROR: missing argument for .schema", file=sys.stderr)
        return True
    name = args[0]
    table = db.catalog.get_table(name)
    if table is None:
        print(f"ERROR: no such table: {name}", file=sys.stderr)
        return True
    columns = ", ".join(f"{col} {type_name}" for col, type_name in table.schema)
    print(f"CREATE TABLE {name}({columns});")
    return True


def _cmd_read(args: List[str], db: Database, state: ReplState) -> bool:
    if not args:
        print("ERROR: missing argument for .read", file=sys.stderr)
        return True
    path_str = args[0]
    try:
        with Path(path_str).open("rb") as script_file:
            raw = script_file.read(MAX_READ_FILE_BYTES + 1)
    except OSError:
        print(f"ERROR: cannot read file: {path_str}", file=sys.stderr)
        return True
    if len(raw) > MAX_READ_FILE_BYTES:
        print(f"ERROR: file too large: {path_str}", file=sys.stderr)
        return True
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        print(f"ERROR: cannot read file: {path_str}", file=sys.stderr)
        return True

    from tinydb._repl_io import _is_unterminated
    from tinydb.repl import _run_sql

    buf = ""
    for char in text:
        buf += char
        if char == ";" and not _is_unterminated(buf):
            # Route through the same executor as interactive SQL so
            # SELECTs render rows via format_rows() and the active
            # timer/format/color settings are honoured.
            _run_sql(db, buf.strip(), state)
            buf = ""
    if buf.strip():
        print(
            f"ERROR: unterminated statement at EOF in {path_str}",
            file=sys.stderr,
        )
    return True


# ---------------------------------------------------------------------------
# 新命令实现
# ---------------------------------------------------------------------------

def _cmd_explain(args: List[str], db: Database, state: ReplState) -> bool:
    if not args:
        print("ERROR: missing argument for .explain", file=sys.stderr)
        return True
    sql = " ".join(args)
    try:
        plan = db.explain_plan(sql)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return True
    from tinydb.plan import format_plan as _render
    print("Plan:")
    print(_render(plan))
    return True


def _cmd_indexes(args: List[str], db: Database, state: ReplState) -> bool:
    table_filter = args[0] if args else None
    for table, column, btree in db.index_manager.all_indexes():
        if table_filter and table != table_filter:
            continue
        root = getattr(btree, "root_page_id", None)
        count_estimate = "?"  # 精确键数需要 BTree.count();v1 只显示占位
        print(f"{table}.{column:<24} root_page={root}  keys≈{count_estimate}")
    return True


def _cmd_stats(args: List[str], db: Database, state: ReplState) -> bool:
    catalog = db.catalog
    n_tables = len(catalog.tables)
    n_rows = 0
    for table in catalog.tables:
        try:
            rows = db.execute(f"SELECT COUNT(*) FROM {table}")
            if rows:
                n_rows += rows[0].values[0]
        except Exception:
            pass
    n_pages = db.pager.page_count()
    n_free = _free_list_length(db.pager)
    wal_size = _wal_size(db)
    print(f"Tables:     {n_tables}")
    print(f"Rows:       {n_rows}")
    print(f"Pages:      {n_pages}")
    print(f"Free pages: {n_free}")
    print(f"WAL:        {wal_size} bytes")
    return True


def _free_list_length(pager) -> int:
    """Return the number of pages reachable from the pager free-list head."""
    current = int.from_bytes(pager.read_page(0)[9:13], "big")
    seen: set[int] = set()
    page_cap = max(pager.page_count(), 1)
    while current != 0 and current not in seen and len(seen) < page_cap:
        seen.add(current)
        current = int.from_bytes(pager.read_page(current)[0:4], "big")
    return len(seen)


def _wal_size(db: Database) -> int:
    wal_path = getattr(db.pager, "_wal_path", None)
    if wal_path and Path(wal_path).exists():
        return Path(wal_path).stat().st_size
    return 0


def _cmd_timer(args: List[str], db: Database, state: ReplState) -> bool:
    if not args or args[0] not in ("on", "off"):
        print("ERROR: .timer on|off", file=sys.stderr)
        return True
    state.timer_enabled = args[0] == "on"
    print(f"Timer: {'on' if state.timer_enabled else 'off'}")
    return True


def _cmd_format(args: List[str], db: Database, state: ReplState) -> bool:
    if not args or args[0] not in VALID_OUTPUT_FORMATS:
        print(f"ERROR: .format {'|'.join(VALID_OUTPUT_FORMATS)}", file=sys.stderr)
        return True
    state.output_format = args[0]  # type: ignore[assignment]
    print(f"Format: {state.output_format}")
    return True


def _cmd_color(
    args: List[str],
    db: Database,
    state: ReplState,
    io: "object | None" = None,
) -> bool:
    if not args or args[0] not in ("on", "off"):
        print("ERROR: .color on|off", file=sys.stderr)
        return True
    state.color_enabled = args[0] == "on"
    print(f"Color: {'on' if state.color_enabled else 'off'}")
    # prompt_toolkit bakes the lexer into the session at construction;
    # if we have an IO handle that exposes set_color, rebuild the
    # session now.  FallbackReplIO has no set_color, which is a no-op.
    if io is not None and hasattr(io, "set_color"):
        io.set_color(state.color_enabled)
    return True


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

META_COMMANDS: dict[str, MetaCommand] = {
    "exit":    MetaCommand("exit",    _cmd_exit,    "exit the REPL"),
    "quit":    MetaCommand("quit",    _cmd_exit,    "exit the REPL"),
    "help":    MetaCommand("help",    _cmd_help,    "show this help"),
    "tables":  MetaCommand("tables",  _cmd_tables,  "list tables"),
    "schema":  MetaCommand("schema",  _cmd_schema,  "show CREATE TABLE <name>",   takes_arg=True),
    "read":    MetaCommand("read",    _cmd_read,    "execute a SQL file <path>",  takes_arg=True),
    "explain": MetaCommand("explain", _cmd_explain, "show query plan for <sql>",   takes_arg=True),
    "indexes": MetaCommand("indexes", _cmd_indexes, "list indexes [table]"),
    "stats":   MetaCommand("stats",   _cmd_stats,   "show database statistics"),
    "timer":   MetaCommand("timer",   _cmd_timer,   "toggle execution timing: .timer on|off"),
    "format":  MetaCommand("format",  _cmd_format,  "switch output format: .format table|csv|json"),
    "color":   MetaCommand("color",   _cmd_color,   "toggle color output: .color on|off"),
}


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------

def handle_meta(
    line: str,
    db: Database,
    state: ReplState,
    io: "object | None" = None,
) -> bool:
    """解析并分发;.exit/.quit 抛 _ExitReplSignal.

    ``io`` is forwarded to ``.color`` so the command can rebuild the
    prompt_toolkit session's lexer.  Other commands ignore the handle.

    返回:
        True  — 已处理(包括错误)
        False — 行不以 '.' 开头,不属于 meta 命令
    """
    stripped = line.lstrip()
    if not stripped.startswith("."):
        return False
    parts = stripped.split(maxsplit=1)
    cmd_token = parts[0]
    cmd = cmd_token.lstrip(".")
    if cmd not in META_COMMANDS:
        print(f"ERROR: unknown command: .{cmd}", file=sys.stderr)
        return True
    rest = parts[1].strip() if len(parts) == 2 else ""
    args_list = rest.split() if rest else []
    if cmd == "color":
        # Forward the IO handle so the colour toggle can rebuild the
        # prompt_toolkit session's lexer.
        return _cmd_color(args_list, db, state, io)
    return META_COMMANDS[cmd](args_list, db, state)
