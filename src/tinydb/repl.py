"""Interactive SQL shell for tinydb.

This module is intentionally a thin entry point.  Input handling, formatting,
and meta-command implementations live in the dedicated ``_repl_*`` modules;
this module keeps the public CLI entry points and compatibility aliases.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

from tinydb._repl_format import _format_table, format_rows
from tinydb._repl_io import (
    FallbackReplIO,
    PromptToolkitReplIO,
    ReplIOProtocol,
    _HAS_PROMPT_TOOLKIT,
    _color_enabled,
    _is_unterminated,
)
from tinydb._repl_meta import ReplState, _ExitReplSignal, handle_meta
from tinydb.database import Database
from tinydb.errors import ConstraintViolation


PRIMARY_PROMPT_PREFIX = "tinydb"
CONTINUATION_PROMPT = "...> "
HISTORY_PATH = "~/.tinydb_history"
HISTORY_LENGTH = 1000
USAGE = "Usage: tinydb-repl [--database PATH]"

# A module-level state remains available to callers that used the old REPL
# singleton.  ``main`` replaces it with the state for the current session.
_state = ReplState()

# Compatibility alias retained for code that caught the old private signal.
_ExitRepl = _ExitReplSignal


def main(argv: Optional[list[str]] = None) -> int:
    """Parse CLI arguments, choose an input adapter, and run the REPL."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        print(USAGE)
        return 0
    if not args:
        db_path = ":memory:"
    elif len(args) == 2 and args[0] == "--database":
        db_path = os.path.expanduser(args[1])
    else:
        flag = args[0] if args else "--database"
        print(f"ERROR: invalid argument: {flag}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    global _state
    state = ReplState()
    state.color_enabled = _color_enabled()
    _state = state
    history_path = Path(os.path.expanduser(HISTORY_PATH))

    # Read the flag from the source module at runtime so tests and embedders can
    # disable the optional dependency after importing tinydb.repl.
    from tinydb import _repl_io

    if _repl_io._HAS_PROMPT_TOOLKIT and _HAS_PROMPT_TOOLKIT:
        io: ReplIOProtocol = PromptToolkitReplIO(
            db_path, history_path, state.color_enabled
        )
    else:
        print(
            "WARNING: prompt_toolkit not available; falling back to input() mode",
            file=sys.stderr,
        )
        io = FallbackReplIO(db_path, history_path)

    db = Database(db_path)
    try:
        print(".help for commands, .timer on for timing")
        return _interactive_loop(db, io, state)
    finally:
        try:
            io.save_history()
        except Exception:
            # History is an optional convenience and must not mask a REPL exit.
            pass
        db.close()


def _interactive_loop(
    db: Database, io: ReplIOProtocol, state: ReplState
) -> int:
    """Read statements and dispatch meta commands or SQL until EOF/exit."""
    while True:
        text = io.read_statement()
        if text is None:
            return 0
        if not text or not text.strip():
            continue
        if text.lstrip().startswith("."):
            try:
                handle_meta(text, db, state)
            except _ExitReplSignal:
                return 0
            continue
        io.add_history(text)
        _run_sql(db, text, state)


def _run_sql(db: Database, sql: str, state: ReplState) -> None:
    """Execute SQL, render its result, and optionally append elapsed time."""
    from tinydb.parser import Select, parse
    from tinydb.tokenizer import tokenize

    try:
        statements = parse(tokenize(sql)).statements
        last_is_select = bool(statements) and isinstance(statements[-1], Select)
    except Exception:
        # Let Database.execute produce the canonical parser/tokenizer error.
        last_is_select = False

    started = time.perf_counter() if state.timer_enabled else None
    try:
        rows = db.execute(sql)
        if not last_is_select:
            print("OK")
        elif not rows:
            print("(no rows)")
        else:
            output_format = state.output_format
            if output_format not in {"table", "csv", "json"}:
                raise ValueError(f"unknown format: {output_format}")
            print(format_rows(rows, output_format))
    except Exception as exc:
        print(_format_exception(exc), file=sys.stderr)
        return

    if started is not None:
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"Time: {elapsed_ms:.3f} ms")


def _format_exception(exc: Exception) -> str:
    """Render an exception as one user-facing error line."""
    detail = str(exc).replace("\r", " ").replace("\n", " ")
    if isinstance(exc, ConstraintViolation):
        return f"ERROR: {detail}"
    return f"ERROR: {type(exc).__name__}: {detail}"


__all__ = [
    "HISTORY_LENGTH",
    "USAGE",
    "_format_table",
    "_interactive_loop",
    "_is_unterminated",
    "_run_sql",
    "_state",
    "main",
]
