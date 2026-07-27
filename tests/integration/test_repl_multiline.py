"""End-to-end multi-line REPL integration tests (Task 6).

Drives `_interactive_loop` with a `FallbackReplIO` whose `input()` is
patched, feeding fragmented SQL across multiple physical lines.  Verifies
the fallback adapter's line-accumulation, continuation prompts, and
`_is_unterminated` plumbing until the SQL terminator is hit.

The plan template uses 5 lines; this module generalises the pattern to
``CREATE TABLE`` + ``INSERT`` + ``SELECT`` cross-line exercises.
"""
from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from tinydb._repl_io import FallbackReplIO
from tinydb._repl_meta import ReplState
from tinydb.database import Database
from tinydb.repl import _interactive_loop


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 6.2.1  5-line SELECT through FallbackReplIO + _interactive_loop
# ---------------------------------------------------------------------------


def test_fallback_multiline_select_5_lines(monkeypatch, capsys, tmp_path):
    """Five physical lines of SELECT accumulate, terminate, and execute."""
    # `_is_unterminated` is False once the `;` is hit AND no open quote /
    # comment / parenthesis remains.  The empty `;` line is *just* the
    # terminator.  We then need a sentinel to return None (EOF) so the
    # loop exits cleanly.
    lines = iter(
        [
            "SELECT id, name",                 # unterminated (no `;`)
            " FROM users",                     # unterminated
            " WHERE id = 1",                   # unterminated
            "",                               # blank — buf is non-empty, still accum
            ";\n",                             # terminator → emit statement
            "",                                # trailing blank
            None,                              # sentinel → raise EOFError
        ]
    )

    def fake_input(prompt):
        value = next(lines, None)
        if value is None:
            raise EOFError
        return value

    monkeypatch.setattr(builtins, "input", fake_input)

    io = FallbackReplIO(":memory:", tmp_path / "h")
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, name TEXT)")
        db.execute("INSERT INTO users(id, name) VALUES (1, 'alice')")
        rc = _interactive_loop(db, io, ReplState())

    assert rc == 0
    out = capsys.readouterr().out
    assert "id" in out
    assert "name" in out
    assert "alice" in out
    assert "1" in out


# ---------------------------------------------------------------------------
# 6.2.2  CREATE TABLE + INSERT + SELECT as multi-statement session
# ---------------------------------------------------------------------------


def test_fallback_multiline_create_table_split(monkeypatch, capsys, tmp_path):
    """`CREATE TABLE` and an `INSERT` are split across multiple physical lines."""
    lines = iter(
        [
            "CREATE TABLE t(",                  # unterminated (paren open)
            "  id INT,",                         # unterminated
            "  name TEXT",                       # unterminated
            ");",                                # terminator
            "INSERT INTO t(id, name) VALUES (",  # unterminated
            "  1, 'alice'",                      # unterminated
            ");",                                # terminator
            "SELECT * FROM t;",                  # self-terminating single line
            None,                                # EOF
        ]
    )

    def fake_input(prompt):
        value = next(lines, None)
        if value is None:
            raise EOFError
        return value

    monkeypatch.setattr(builtins, "input", fake_input)

    io = FallbackReplIO(":memory:", tmp_path / "h")
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # Two statements print "OK", SELECT renders rows.
    assert out.count("OK") == 2
    assert "alice" in out
    assert "1" in out


# ---------------------------------------------------------------------------
# 6.2.3  Continuation prompt contains `...>` (prompt-toolkit-style)
# ---------------------------------------------------------------------------


def test_fallback_continuation_prompt_text(monkeypatch, tmp_path):
    """FallbackReplIO's continuation prompt is `...> ` (matches design doc)."""
    seen_prompts: list[str] = []

    def fake_input(prompt):
        seen_prompts.append(prompt)
        return ";\n" if len(seen_prompts) >= 2 else "SELECT *"

    monkeypatch.setattr(builtins, "input", fake_input)

    io = FallbackReplIO(":memory:", tmp_path / "h")
    io.read_statement()  # first line: SELECT * → buf has data, returns ""
    io.read_statement()  # second line: `;\n` → terminator, returns stmt
    # First prompt: the full `tinydb> [...] ` prompt; subsequent: `...> `
    assert any(p.startswith("...> ") for p in seen_prompts[1:]), seen_prompts
    assert any("tinydb>" in p for p in seen_prompts[:1]), seen_prompts


# ---------------------------------------------------------------------------
# 6.2.4  Quoted string spans multiple physical lines
# ---------------------------------------------------------------------------


def test_fallback_multiline_quote_spanning_lines(monkeypatch, capsys, tmp_path):
    """Quoted string in INSERT spans 2 physical lines; statement still works.

    Note: the fallback's `_buf += line + "\\n"` keeps the literal newline
    between physical lines, so a single-quoted string split across two
    lines is stored with that newline embedded.  We assert both halves are
    preserved rather than checking for the joined string.
    """
    lines = iter(
        [
            "INSERT INTO t(id, name) VALUES (",  # unterminated
            "  1, 'alice",                       # unterminated (open `'`)
            " and bob');",                       # closes quote + `;` → emit
            "SELECT name FROM t;",
            None,
        ]
    )

    def fake_input(prompt):
        value = next(lines, None)
        if value is None:
            raise EOFError
        return value

    monkeypatch.setattr(builtins, "input", fake_input)

    io = FallbackReplIO(":memory:", tmp_path / "h")
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # INSERT printed "OK" (fallback routes only the success through stdout).
    assert "OK" in out
    # The string halves must both be present in the rendered table; the
    # literal `\n` between physical lines is preserved verbatim.
    assert "alice" in out
    assert "and bob" in out


# ---------------------------------------------------------------------------
# 6.2.5  `input()` after EOF raises → loop returns 0
# ---------------------------------------------------------------------------


def test_fallback_eof_returns_zero(monkeypatch, tmp_path):
    """Empty input (immediate EOF) makes _interactive_loop return 0."""
    def fake_input(prompt):
        raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    io = FallbackReplIO(":memory:", tmp_path / "h")
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
