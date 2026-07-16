"""Interactive SQL shell for tinydb; stdlib-only and isolated from the MVP core."""

PRIMARY_PROMPT_PREFIX = "tinydb"
CONTINUATION_PROMPT = "...> "


def _make_prompt(db_path: str) -> str:
    return f"{PRIMARY_PROMPT_PREFIX}> [{db_path}] "


def _read_one_statement(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        return None


def main() -> int:
    """Run the tinydb REPL."""
    db_path = ":memory:"
    db = Database(db_path)
    try:
        while True:
            try:
                line = _read_one_statement(_make_prompt(db_path))
            except KeyboardInterrupt:
                print("\n(Use .exit or Ctrl-D to exit)")
                continue
            if line is None:
                return 0
            if not line.strip():
                continue
            _run_sql(db, line)
    finally:
        db.close()


import sys

from tinydb.database import Database
from tinydb.parser import Select, parse
from tinydb.tokenizer import tokenize


def _run_sql(db: Database, sql: str) -> None:
    try:
        statements = parse(tokenize(sql)).statements
        last_is_select = bool(statements) and isinstance(statements[-1], Select)
    except Exception:
        last_is_select = False

    try:
        rows = db.execute(sql)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return

    if not last_is_select:
        print("OK")
    elif not rows:
        print("(no rows)")
    else:
        for row in rows:
            print(repr(row))