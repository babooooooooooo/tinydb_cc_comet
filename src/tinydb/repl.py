"""Interactive SQL shell for tinydb; stdlib-only and isolated from the MVP core."""

import os
import sys
from pathlib import Path

from tinydb.database import Database, Row
from tinydb.parser import Select, parse
from tinydb.tokenizer import tokenize

PRIMARY_PROMPT_PREFIX = "tinydb"
CONTINUATION_PROMPT = "...> "
HISTORY_PATH = "~/.tinydb_history"
HISTORY_LENGTH = 1000
MAX_COLUMN_WIDTH = 30
HELP_TEXT = """Meta commands:
  .exit               exit the REPL
  .quit               exit the REPL
  .help               show this help
  .tables             list tables
  .schema <name>      show CREATE TABLE
  .read <path>        execute a SQL file
Shortcuts: Ctrl-D exits; Ctrl-C clears the current buffer."""


class _ExitRepl(Exception):
    """Internal control flow for .exit and .quit."""


def _make_prompt(db_path: str) -> str:
    return f"{PRIMARY_PROMPT_PREFIX}> [{db_path}] "


def _read_one_statement(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        return None


def _interactive_loop(db: Database, db_path: str) -> int:
    readline_ok = _setup_history()
    buf = ""
    try:
        while True:
            try:
                prompt = CONTINUATION_PROMPT if buf else _make_prompt(db_path)
                line = _read_one_statement(prompt)
            except KeyboardInterrupt:
                print("\n(Use .exit or Ctrl-D to exit)")
                buf = ""
                continue
            if line is None:
                return 0
            if not line.strip() and not buf:
                continue
            if not buf and line.lstrip().startswith("."):
                try:
                    _handle_meta(line, db)
                except _ExitRepl:
                    return 0
                continue
            buf += line + "\n"
            if _is_unterminated(buf):
                continue
            if readline_ok:
                try:
                    import readline

                    readline.add_history(buf.rstrip("\n"))
                except (ImportError, AttributeError):
                    pass
            _run_sql(db, buf)
            buf = ""
    finally:
        _save_history(readline_ok)


def main() -> int:
    """Run the tinydb REPL."""
    db_path = ":memory:"
    db = Database(db_path)
    try:
        return _interactive_loop(db, db_path)
    finally:
        db.close()


def _format_table(rows: list[Row]) -> str:
    if not rows:
        return "(no rows)"
    columns = list(rows[0].columns)
    raw_values = [[str(value) for value in row.values] for row in rows]
    widths = [
        min(
            max(len(column), *(len(values[index]) for values in raw_values)),
            MAX_COLUMN_WIDTH,
        )
        for index, column in enumerate(columns)
    ]

    def truncate(value: str) -> str:
        if len(value) <= MAX_COLUMN_WIDTH:
            return value
        return value[: MAX_COLUMN_WIDTH - 1] + "…"

    def render(values: list[str]) -> str:
        cells = [truncate(value).ljust(width) for value, width in zip(values, widths)]
        return " | ".join(cells).rstrip()

    header = render(columns)
    separator = " | ".join("---" for _ in columns)
    body = [render(values) for values in raw_values]
    return "\n".join([header, separator, *body])


def _run_sql(db: Database, sql: str) -> None:
    try:
        statements = parse(tokenize(sql)).statements
        last_is_select = bool(statements) and isinstance(statements[-1], Select)
    except Exception:
        last_is_select = False

    try:
        rows = db.execute(sql)
    except Exception as exc:
        message = " ".join(str(exc).splitlines())
        print(f"ERROR: {type(exc).__name__}: {message}", file=sys.stderr)
        return

    if not last_is_select:
        print("OK")
    elif not rows:
        print("(no rows)")
    else:
        print(_format_table(rows))


def _run_file(db: Database, path_str: str) -> None:
    try:
        text = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        print(f"ERROR: cannot read file: {path_str}", file=sys.stderr)
        return

    buf = ""
    for raw_line in text.splitlines(keepends=True):
        buf += raw_line
        if not _is_unterminated(buf):
            _run_sql(db, buf)
            buf = ""
    if buf.strip():
        print(
            f"ERROR: unterminated statement at EOF in {path_str}",
            file=sys.stderr,
        )


def _setup_history() -> bool:
    try:
        import readline
    except ImportError:
        return False
    history_file = os.path.expanduser(HISTORY_PATH)
    try:
        readline.read_history_file(history_file)
    except OSError:
        pass
    readline.set_history_length(HISTORY_LENGTH)
    return True


def _save_history(readline_ok: bool) -> None:
    if not readline_ok:
        return
    try:
        import readline

        readline.write_history_file(os.path.expanduser(HISTORY_PATH))
    except (ImportError, OSError):
        pass


def _handle_meta(line: str, db: Database) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith("."):
        return False
    parts = stripped.split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) == 2 else ""
    if command in {".exit", ".quit"}:
        raise _ExitRepl
    if command == ".help":
        print(HELP_TEXT)
        return True
    if command == ".tables":
        for name in sorted(db.catalog.tables):
            print(name)
        return True
    if command in {".schema", ".read"} and not argument:
        print(f"ERROR: missing argument for {command}", file=sys.stderr)
        return True
    if command == ".schema":
        table = db.catalog.get_table(argument)
        if table is None:
            print(f"ERROR: no such table: {argument}", file=sys.stderr)
            return True
        columns = ", ".join(f"{name} {type_name}" for name, type_name in table.schema)
        print(f"CREATE TABLE {argument}({columns});")
        return True
    if command == ".read":
        _run_file(db, argument)
        return True
    print(f"ERROR: unknown command: {command}", file=sys.stderr)
    return True


def _is_unterminated(buf: str) -> bool:
    in_sq = False
    in_dq = False
    in_lc = False
    in_bc = False
    parens = 0
    i = 0
    while i < len(buf):
        char = buf[i]
        nxt = buf[i + 1] if i + 1 < len(buf) else ""
        if in_lc:
            in_lc = char != "\n"
            i += 1
            continue
        if in_bc:
            if char == "*" and nxt == "/":
                in_bc = False
                i += 2
            else:
                i += 1
            continue
        if in_sq:
            if char == "'" and nxt == "'":
                i += 2
            elif char == "'":
                in_sq = False
                i += 1
            else:
                i += 1
            continue
        if in_dq:
            if char == '"' and nxt == '"':
                i += 2
            elif char == '"':
                in_dq = False
                i += 1
            else:
                i += 1
            continue
        if char == "-" and nxt == "-":
            in_lc = True
            i += 2
        elif char == "/" and nxt == "*":
            in_bc = True
            i += 2
        elif char == "'":
            in_sq = True
            i += 1
        elif char == '"':
            in_dq = True
            i += 1
        elif char == "(":
            parens += 1
            i += 1
        elif char == ")":
            parens -= 1
            i += 1
        else:
            i += 1
    return in_sq or in_dq or in_lc or in_bc or parens > 0
