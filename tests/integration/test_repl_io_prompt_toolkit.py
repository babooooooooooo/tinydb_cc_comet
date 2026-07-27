"""End-to-end REPL integration tests via PromptSession patching (Task 6).

Drives the real `_interactive_loop` against a `PromptToolkitReplIO` whose
`PromptSession.prompt` method is monkey-patched to return canned SQL
strings.  Asserts the loop executes the statements and produces the
expected output.

These tests exercise the Task 5 commit (991f3e7) thin-wrapper contract:
`_interactive_loop(db, io, state)` accepts any `ReplIOProtocol` adapter.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tinydb._repl_io as io_mod
from tinydb._repl_io import PromptToolkitReplIO
from tinydb._repl_meta import ReplState
from tinydb.database import Database
from tinydb.repl import _interactive_loop


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test fixtures: FakeSession / FakeHistory patched at the module level so
# `PromptToolkitReplIO.__init__` instantiates our stand-ins.
# ---------------------------------------------------------------------------


class _FakeHistory:
    """In-memory FileHistory replacement."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def append_string(self, text: str) -> None:
        self.calls.append(text)


class _FakeSession:
    """PromptSession stand-in.  ``_prompts`` drives the canned inputs."""

    def __init__(self, **kwargs) -> None:
        self.history = _FakeHistory()
        self._prompts = getattr(self, "_prompts", [])

    def prompt(self, prompt, multiline=False):  # noqa: ARG002
        if not self._prompts:
            raise EOFError
        value = self._prompts.pop(0)
        if value is None:
            raise EOFError
        if value == "_KEYBOARD_INTERRUPT_":
            raise KeyboardInterrupt
        return value


def _patch_prompt_toolkit(monkeypatch, prompts: list):
    """Wire up monkey-patches so PromptToolkitReplIO uses our fakes."""
    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(io_mod, "PromptSession", _FakeSession)
    monkeypatch.setattr(io_mod, "FileHistory", lambda p: _FakeHistory())
    monkeypatch.setattr(io_mod, "AutoSuggestFromHistory", lambda: None)
    monkeypatch.setattr(io_mod, "PygmentsLexer", lambda l: None)
    monkeypatch.setattr(io_mod, "SqlLexer", object())

    def _session_factory(**kwargs):
        session = _FakeSession(**kwargs)
        session._prompts = list(prompts)
        return session

    monkeypatch.setattr(io_mod, "PromptSession", _session_factory)


# ---------------------------------------------------------------------------
# 6.1.1  Drive SQL fragments through _interactive_loop
# ---------------------------------------------------------------------------


def test_prompt_toolkit_drives_create_insert_select(monkeypatch, tmp_path, capsys):
    """Canned CREATE + INSERT + SELECT through PromptSession → _interactive_loop."""
    _patch_prompt_toolkit(
        monkeypatch,
        [
            "CREATE TABLE t(id INT);",
            "INSERT INTO t(id) VALUES (1);",
            "INSERT INTO t(id) VALUES (2);",
            "SELECT * FROM t;",
            None,  # EOF
        ],
    )
    history = tmp_path / "h"
    history.touch()
    io = PromptToolkitReplIO(":memory:", history, False)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # CREATE + 2 INSERTs each print "OK" (SELECT is not "OK"); one SELECT
    # renders rows.
    assert out.count("OK") == 3
    assert "id" in out
    assert "1" in out
    assert "2" in out


def test_prompt_toolkit_meta_exit(monkeypatch, tmp_path, capsys):
    """.exit via PromptSession raises _ExitReplSignal → loop returns 0."""
    _patch_prompt_toolkit(
        monkeypatch,
        [
            "CREATE TABLE t(id INT);",
            ".exit",
        ],
    )
    history = tmp_path / "h"
    history.touch()
    io = PromptToolkitReplIO(":memory:", history, False)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_prompt_toolkit_history_appends_executed(monkeypatch, tmp_path, capsys):
    """add_history called for each executed SQL statement; meta commands skipped."""
    _patch_prompt_toolkit(
        monkeypatch,
        [
            "CREATE TABLE t(id INT);",
            "INSERT INTO t(id) VALUES (1);",
            ".tables",
            "SELECT * FROM t;",
            None,
        ],
    )
    history_path = tmp_path / "h"
    history_path.touch()
    io = PromptToolkitReplIO(":memory:", history_path, False)
    with Database(":memory:") as db:
        _interactive_loop(db, io, ReplState())
    # 3 SQL statements were added to history; .tables is a meta command so
    # the loop does not call add_history for it.
    assert len(io._session.history.calls) == 3
    assert io._session.history.calls[0] == "CREATE TABLE t(id INT);"
    assert io._session.history.calls[1] == "INSERT INTO t(id) VALUES (1);"
    assert io._session.history.calls[2] == "SELECT * FROM t;"


def test_prompt_toolkit_empty_input_continues_loop(monkeypatch, tmp_path, capsys):
    """Whitespace-only inputs are skipped; loop proceeds to next statement."""
    _patch_prompt_toolkit(
        monkeypatch,
        [
            "   ",  # ignored
            "",     # ignored
            "CREATE TABLE t(id INT);",
            "   \n  ",  # ignored
            "SELECT * FROM t;",
            None,
        ],
    )
    history = tmp_path / "h"
    history.touch()
    io = PromptToolkitReplIO(":memory:", history, False)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out
    # CREATE prints OK; SELECT prints (no rows); whitespace rows are skipped.
    assert out.count("OK") == 1
    assert "(no rows)" in out


def test_prompt_toolkit_error_routes_to_stderr_loop_continues(
    monkeypatch, tmp_path, capsys
):
    """Failed SQL writes single ERROR line to stderr; loop continues."""
    _patch_prompt_toolkit(
        monkeypatch,
        [
            "SELECT * FROM ghost;",
            "CREATE TABLE ok(id INT);",
            None,
        ],
    )
    history = tmp_path / "h"
    history.touch()
    io = PromptToolkitReplIO(":memory:", history, False)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "ghost" in captured.err
    # Loop must have continued: CREATE TABLE succeeded (stdout OK).
    assert "OK" in captured.out
