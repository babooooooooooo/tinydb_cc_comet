"""REPL input and output adapters.

The preferred adapter uses the optional prompt_toolkit and Pygments packages for
multiline editing, history, and SQL highlighting.  A small stdlib-only adapter
keeps the REPL usable when those optional dependencies are unavailable.
"""
from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.lexers import PygmentsLexer
    from pygments.lexers.sql import SqlLexer

    _HAS_PROMPT_TOOLKIT: bool = True
except ImportError:  # pragma: no cover - exercised by soft-dependency tests
    PromptSession = None  # type: ignore[assignment]
    AutoSuggestFromHistory = None  # type: ignore[assignment]
    FileHistory = None  # type: ignore[assignment]
    PygmentsLexer = None  # type: ignore[assignment]
    SqlLexer = None  # type: ignore[assignment]
    _HAS_PROMPT_TOOLKIT = False


def _color_enabled() -> bool:
    """Return whether terminal color output should be enabled."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return True


def _is_unterminated(buf: str) -> bool:
    """Return whether *buf* still contains an incomplete SQL construct."""
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


@runtime_checkable
class ReplIOProtocol(Protocol):
    """Interface used by the REPL's input and history layer."""

    def read_statement(self) -> str | None:
        """Read a statement, returning ``None`` on EOF and ``""`` on Ctrl-C."""

    def add_history(self, statement: str) -> None:
        """Add an executed statement to history."""

    def save_history(self) -> None:
        """Persist history, if the adapter supports persistence."""


class PromptToolkitReplIO:
    """Prompt toolkit-backed multiline REPL input."""

    def __init__(self, db_path: str, history_path: Path, color: bool) -> None:
        if not _HAS_PROMPT_TOOLKIT:
            raise RuntimeError(
                "PromptToolkitReplIO requires prompt_toolkit; "
                "check _HAS_PROMPT_TOOLKIT first or use FallbackReplIO."
            )
        self._db_path = db_path
        self._history_path = history_path
        if not history_path.exists():
            history_path.touch(mode=0o600)
        from prompt_toolkit.formatted_text import HTML

        self._continuation = HTML("<ansigray>...> </ansigray>")
        self._session: PromptSession = PromptSession(
            history=FileHistory(str(history_path)),
            multiline=True,
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
            lexer=PygmentsLexer(SqlLexer) if color else None,
            prompt_continuation=self._continuation,
        )

    def read_statement(self) -> str | None:
        """Read one multiline statement from prompt_toolkit."""
        from prompt_toolkit.formatted_text import HTML

        html_prompt = HTML(
            f"<bold>tinydb&gt;</bold> <ansigray>[{escape(self._db_path)}]</ansigray> "
        )
        try:
            return self._session.prompt(html_prompt, multiline=True)
        except EOFError:
            return None
        except KeyboardInterrupt:
            return ""

    def add_history(self, statement: str) -> None:
        """No-op for prompt_toolkit: Buffer.validate_and_handle() already
        appends the entered text to the session history.  Calling
        ``self._session.history.append_string`` here would double-write
        every SQL statement, polluting up-arrow recall.
        """
        return None

    def set_color(self, enabled: bool) -> None:
        """Rebuild the session's lexer (preserving history) on color toggle.

        prompt_toolkit bakes the lexer into the session at construction
        time, so the only way to honour ``.color on|off`` is to drop the
        current session and create a new one with the new lexer while
        keeping the same FileHistory instance.
        """
        history = self._session.history
        self._session = PromptSession(
            history=history,
            multiline=True,
            auto_suggest=AutoSuggestFromHistory(),
            enable_history_search=True,
            lexer=PygmentsLexer(SqlLexer) if enabled else None,
            prompt_continuation=self._continuation,
        )

    def save_history(self) -> None:
        """FileHistory handles persistence itself."""
        return None


class FallbackReplIO:
    """Stdlib-only multiline REPL input adapter."""

    def __init__(self, db_path: str, history_path: Path) -> None:
        self._db_path = db_path
        self._history_path = history_path
        self._history: list[str] = []
        self._buf = ""

    def read_statement(self) -> str | None:
        """Read input lines until SQL quotes, comments, and parentheses close.

        Lines starting with ``.`` are meta commands and are returned to the
        caller immediately (without requiring the ``;`` terminator or
        accumulation) so the meta-command registry in ``_repl_meta`` can
        dispatch them.
        """
        try:
            prompt = "...> " if self._buf else f"tinydb> [{self._db_path}] "
            line = input(prompt)
        except EOFError:
            return None
        except KeyboardInterrupt:
            self._buf = ""
            print("\n(Use .exit or Ctrl-D to exit)")
            return ""

        if not line.strip() and not self._buf:
            return ""
        # Meta commands are single-line by contract; return immediately
        # so ``_interactive_loop`` can hand them to ``handle_meta``.
        if line.lstrip().startswith("."):
            return line
        self._buf += line + "\n"
        # The fallback has no editor-level submit key, so require an explicit
        # SQL terminator before handing the accumulated input to the REPL.
        if _is_unterminated(self._buf) or ";" not in self._buf:
            return ""
        statement = self._buf.rstrip("\n")
        self._buf = ""
        return statement

    def add_history(self, statement: str) -> None:
        """Keep non-empty statements in transient in-memory history."""
        if statement.strip():
            self._history.append(statement)

    def save_history(self) -> None:
        """There is no persistent history backend in fallback mode."""
        return None

    @property
    def history(self) -> Iterator[str]:
        """Expose history as a read-only iterator for callers and tests."""
        return iter(self._history)


__all__ = [
    "FallbackReplIO",
    "PromptToolkitReplIO",
    "ReplIOProtocol",
    "_HAS_PROMPT_TOOLKIT",
    "_color_enabled",
    "_is_unterminated",
]
