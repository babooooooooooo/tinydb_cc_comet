"""End-to-end tests for ``.read`` SQL-script execution (Round 1 review).

The Round 1 review found that ``.read`` discarded SELECT rows because
it executed statements via a stripped-down ``_run_sql_from_meta`` that
only printed ``OK``.  These tests assert the fix: ``.read`` now routes
through the same ``_run_sql`` as interactive SQL, so SELECTs render
rows via ``format_rows()`` and the active timer/format/color settings
are honoured.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tinydb._repl_io as io_mod
from tinydb._repl_io import PromptToolkitReplIO
from tinydb._repl_meta import ReplState, _cmd_read
from tinydb.database import Database
from tinydb.repl import _interactive_loop


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# FakeSession / FakeHistory plumbing (mirrors test_repl_meta_commands.py)
# ---------------------------------------------------------------------------


class _FakeHistory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def append_string(self, text: str) -> None:
        self.calls.append(text)


class _FakeSession:
    def __init__(self, **kwargs) -> None:
        self.history = _FakeHistory()
        self._prompts = kwargs.pop("_prompts_queue", [])
        if self._prompts:
            value = self._prompts[0]
            if value and value.strip():
                self.history.append_string(value)

    def prompt(self, prompt, multiline=False):  # noqa: ARG002
        if not self._prompts:
            raise EOFError
        value = self._prompts.pop(0)
        if value is None:
            raise EOFError
        return value


def _make_io(monkeypatch, prompts: list, tmp_path) -> PromptToolkitReplIO:
    """Build a PromptToolkitReplIO whose session serves the canned prompts."""
    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(io_mod, "FileHistory", lambda p: _FakeHistory())
    monkeypatch.setattr(io_mod, "AutoSuggestFromHistory", lambda: None)
    monkeypatch.setattr(io_mod, "PygmentsLexer", lambda l: None)
    monkeypatch.setattr(io_mod, "SqlLexer", object())
    queue: list = list(prompts)

    def _session_factory(**kwargs):
        return _FakeSession(_prompts_queue=queue, **kwargs)

    monkeypatch.setattr(io_mod, "PromptSession", _session_factory)
    history = tmp_path / "h"
    history.touch()
    return PromptToolkitReplIO(":memory:", history, False)


# ---------------------------------------------------------------------------
# 1. .read executes SELECTs and renders rows
# ---------------------------------------------------------------------------


def test_read_renders_select_rows(monkeypatch, tmp_path, capsys):
    """``.read <script>` renders rows from SELECT statements in the script."""
    script = tmp_path / "seed.sql"
    script.write_text(
        "CREATE TABLE t(id INT, name TEXT);\n"
        "INSERT INTO t(id, name) VALUES (1, 'alice');\n"
        "INSERT INTO t(id, name) VALUES (2, 'bob');\n"
        "SELECT id, name FROM t;\n",
        encoding="utf-8",
    )
    io = _make_io(
        monkeypatch,
        [f".read {script}", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # CREATE + 2 INSERTs each print "OK".
    assert out.count("OK") == 3
    # The SELECT in the script renders rows via format_rows().
    assert "id" in out
    assert "name" in out
    assert "alice" in out
    assert "bob" in out
    # And the column header separator (table format default).
    assert "---" in out


def test_read_respects_output_format_csv(monkeypatch, tmp_path, capsys):
    """``.format csv` before ``.read` renders SELECT rows as CSV."""
    script = tmp_path / "seed.sql"
    script.write_text(
        "CREATE TABLE t(id INT, name TEXT);\n"
        "INSERT INTO t(id, name) VALUES (1, 'alice');\n"
        "SELECT id, name FROM t;\n",
        encoding="utf-8",
    )
    io = _make_io(
        monkeypatch,
        [".format csv", f".read {script}", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # CREATE + 1 INSERT print OK; the SELECT now renders CSV (header line).
    assert out.count("OK") == 2
    assert "id,name" in out  # CSV header row
    assert "1,alice" in out  # CSV data row


def test_read_with_select_timer(monkeypatch, tmp_path, capsys):
    """``.timer on` before ``.read` prints ``Time:`` after each statement."""
    script = tmp_path / "seed.sql"
    script.write_text(
        "CREATE TABLE t(id INT);\n"
        "INSERT INTO t(id) VALUES (1);\n"
        "SELECT * FROM t;\n",
        encoding="utf-8",
    )
    io = _make_io(
        monkeypatch,
        [".timer on", f".read {script}", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # Timer banner, then Time: line for each statement.
    assert "Timer: on" in out
    # 3 statements in the script: each emits a Time: line.
    assert out.count("Time:") == 3


# ---------------------------------------------------------------------------
# 2. .read still surfaces parser / execution errors via stderr
# ---------------------------------------------------------------------------


def test_read_error_writes_to_stderr(monkeypatch, tmp_path, capsys):
    """``.read <bad sql>` writes ERROR to stderr (loop continues)."""
    script = tmp_path / "bad.sql"
    script.write_text(
        "SELECT * FROM ghost;\n"
        "CREATE TABLE ok(id INT);\n",
        encoding="utf-8",
    )
    io = _make_io(
        monkeypatch,
        [f".read {script}", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    # The failing SELECT writes an error to stderr; the successful CREATE
    # writes OK to stdout.
    assert "ERROR" in captured.err
    assert "ghost" in captured.err
    assert "OK" in captured.out
