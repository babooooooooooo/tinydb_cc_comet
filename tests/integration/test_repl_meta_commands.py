"""End-to-end tests for all 12 REPL meta commands (Task 6).

Drives ``_interactive_loop`` with a patched ``PromptSession`` so that
every meta command (``.exit``, ``.quit``, ``.help``, ``.tables``,
``.schema``, ``.read``, ``.explain``, ``.indexes``, ``.stats``,
``.timer``, ``.format``, ``.color``) is exercised end-to-end.  Output is
captured via ``capsys``; each command's expected stdout/stderr tokens
are asserted.

Why ``PromptToolkitReplIO`` rather than ``FallbackReplIO``?  The
fallback adapter requires an explicit ``;`` terminator before emitting
a statement, so meta commands (which never end in ``;``) are not
reachable through it.  The patched ``PromptToolkitReplIO`` mirrors the
real CLI behaviour for meta commands.
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
# FakeSession / FakeHistory plumbing (mirrors test_repl_io_prompt_toolkit.py)
# ---------------------------------------------------------------------------


class _FakeHistory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def append_string(self, text: str) -> None:
        self.calls.append(text)


class _FakeSession:
    def __init__(self, **kwargs) -> None:
        self.history = _FakeHistory()
        # ``_prompts_queue`` is a *deque-like* (plain list used as FIFO) of
        # values to hand out on ``prompt()``.  Each new _FakeSession
        # created by the factory shares the same list, so the test does
        # not double-emit prompts when ``set_color`` rebuilds the
        # session inside the loop.
        self._prompts_queue: list = kwargs.pop("_prompts_queue", [])
        self._prompts: list = self._prompts_queue

    def prompt(self, prompt, multiline=False):  # noqa: ARG002
        if not self._prompts:
            raise EOFError
        value = self._prompts.pop(0)
        if value is None:
            raise EOFError
        return value


def _make_io(monkeypatch, prompts: list, tmp_path) -> PromptToolkitReplIO:
    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(io_mod, "FileHistory", lambda p: _FakeHistory())
    monkeypatch.setattr(io_mod, "AutoSuggestFromHistory", lambda: None)
    monkeypatch.setattr(io_mod, "PygmentsLexer", lambda l: None)
    monkeypatch.setattr(io_mod, "SqlLexer", object())

    # The factory is invoked from both ``PromptToolkitReplIO.__init__``
    # and (post Task 5 review) from ``set_color``.  Sharing the prompts
    # list across all sessions ensures rebuilds don't replay already-
    # consumed inputs and hang the loop.
    queue: list = list(prompts)

    def _session_factory(**kwargs):
        return _FakeSession(_prompts_queue=queue, **kwargs)

    monkeypatch.setattr(io_mod, "PromptSession", _session_factory)
    history = tmp_path / "h"
    history.touch()
    return PromptToolkitReplIO(":memory:", history, False)


def _seed(db: Database) -> None:
    """Standard seed: one table with two rows + a primary key index."""
    db.execute(
        "CREATE TABLE users(id INT PRIMARY KEY, name TEXT, age INT)"
    )
    db.execute("INSERT INTO users(id, name, age) VALUES (1, 'alice', 30)")
    db.execute("INSERT INTO users(id, name, age) VALUES (2, 'bob', 25)")


# ---------------------------------------------------------------------------
# 6.5.1  .exit — exit signal
# ---------------------------------------------------------------------------


def test_meta_exit_returns_zero(monkeypatch, tmp_path, capsys):
    """``.exit`` raises _ExitReplSignal → loop returns 0."""
    io = _make_io(monkeypatch, [".exit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0


def test_meta_quit_returns_zero(monkeypatch, tmp_path, capsys):
    """``.quit`` is an alias for .exit."""
    io = _make_io(monkeypatch, [".quit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0


# ---------------------------------------------------------------------------
# 6.5.2  .help — list all commands
# ---------------------------------------------------------------------------


def test_meta_help_lists_all_commands(monkeypatch, tmp_path, capsys):
    """``.help`` prints the registered meta commands (incl. legacy aliases)."""
    io = _make_io(monkeypatch, [".help", ".exit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # New commands must appear in the help listing.
    for cmd in (
        ".tables",
        ".schema",
        ".read",
        ".explain",
        ".indexes",
        ".stats",
        ".timer",
        ".format",
        ".color",
    ):
        assert cmd in out, f".help output missing {cmd!r}"


# ---------------------------------------------------------------------------
# 6.5.3  .tables — list catalog tables
# ---------------------------------------------------------------------------


def test_meta_tables_lists_seeded_tables(monkeypatch, tmp_path, capsys):
    """``.tables`` prints every catalog table name, one per line."""
    io = _make_io(monkeypatch, [".tables", ".exit"], tmp_path)
    with Database(":memory:") as db:
        db.execute("CREATE TABLE alpha(id INT)")
        db.execute("CREATE TABLE beta(id INT)")
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # Output sorted; both tables on their own lines.
    assert "alpha" in out
    assert "beta" in out


def test_meta_tables_empty_db(monkeypatch, tmp_path, capsys):
    """``.tables`` on an empty DB prints nothing (no error)."""
    io = _make_io(monkeypatch, [".tables", ".exit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    # No error and no table names.
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# 6.5.4  .schema <name> — show CREATE TABLE statement
# ---------------------------------------------------------------------------


def test_meta_schema_known_table(monkeypatch, tmp_path, capsys):
    """``.schema users`` prints the rendered CREATE TABLE statement."""
    io = _make_io(monkeypatch, [".schema users", ".exit"], tmp_path)
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, name TEXT)")
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert "CREATE TABLE users(id INT, name TEXT);" in out


def test_meta_schema_unknown_table_writes_error(
    monkeypatch, tmp_path, capsys
):
    """``.schema ghost`` prints ERROR to stderr (loop continues)."""
    io = _make_io(
        monkeypatch,
        [".schema ghost", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "ghost" in captured.err


def test_meta_schema_no_argument_writes_error(monkeypatch, tmp_path, capsys):
    """``.schema` with no argument writes ERROR to stderr."""
    io = _make_io(monkeypatch, [".schema", ".exit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


# ---------------------------------------------------------------------------
# 6.5.5  .read <path> — execute a SQL file
# ---------------------------------------------------------------------------


def test_meta_read_executes_sql_file(monkeypatch, tmp_path, capsys):
    """``.read <path>` runs every `;`-terminated statement in the file."""
    script = tmp_path / "seed.sql"
    script.write_text(
        "CREATE TABLE t(id INT);\n"
        "INSERT INTO t(id) VALUES (1);\n"
        "INSERT INTO t(id) VALUES (2);\n",
        encoding="utf-8",
    )
    io = _make_io(
        monkeypatch,
        [f".read {script}", "SELECT * FROM t;", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # 3 statements inside the file: CREATE + 2 INSERT → 3 OKs.
    assert out.count("OK") == 3
    # SELECT renders the rows.
    assert "1" in out
    assert "2" in out


def test_meta_read_missing_file_writes_error(
    monkeypatch, tmp_path, capsys
):
    """``.read <nonexistent>` writes ERROR to stderr (no crash)."""
    io = _make_io(
        monkeypatch,
        [".read /no/such/file.sql", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "cannot read" in captured.err


# ---------------------------------------------------------------------------
# 6.5.6  .explain <sql> — render query plan
# ---------------------------------------------------------------------------


def test_meta_explain_prints_plan(monkeypatch, tmp_path, capsys):
    """``.explain SELECT …` prints a `Plan:` block."""
    io = _make_io(
        monkeypatch,
        [".explain SELECT * FROM users", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        _seed(db)
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Plan:" in out


def test_meta_explain_invalid_sql_writes_error(
    monkeypatch, tmp_path, capsys
):
    """``.explain <bad sql>` writes ERROR to stderr (loop continues)."""
    io = _make_io(
        monkeypatch,
        [".explain SELECT FROM", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


# ---------------------------------------------------------------------------
# 6.5.7  .indexes [table] — list indexes
# ---------------------------------------------------------------------------


def test_meta_indexes_prints_pk_index(monkeypatch, tmp_path, capsys):
    """``.indexes` lists the auto-created PK index for `users`."""
    io = _make_io(monkeypatch, [".indexes", ".exit"], tmp_path)
    with Database(":memory:") as db:
        _seed(db)
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # The PK index prints `users.id` (table.column).
    assert "users.id" in out


def test_meta_indexes_filter_by_table(monkeypatch, tmp_path, capsys):
    """``.indexes users` shows only the `users.*` indexes."""
    io = _make_io(monkeypatch, [".indexes users", ".exit"], tmp_path)
    with Database(":memory:") as db:
        _seed(db)
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert "users.id" in out


# ---------------------------------------------------------------------------
# 6.5.8  .stats — database statistics
# ---------------------------------------------------------------------------


def test_meta_stats_prints_summary(monkeypatch, tmp_path, capsys):
    """``.stats` prints the 5 canonical summary fields."""
    io = _make_io(monkeypatch, [".stats", ".exit"], tmp_path)
    with Database(":memory:") as db:
        _seed(db)
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    for field in ("Tables:", "Rows:", "Pages:", "Free pages:", "WAL:"):
        assert field in out, f".stats output missing {field!r}"


# ---------------------------------------------------------------------------
# 6.5.9  .timer on|off — toggle timing
# ---------------------------------------------------------------------------


def test_meta_timer_on_appends_time_line(monkeypatch, tmp_path, capsys):
    """``.timer on` then `SELECT` adds a `Time:` line to the output."""
    io = _make_io(
        monkeypatch,
        [
            ".timer on",
            "SELECT * FROM users",
            ".exit",
        ],
        tmp_path,
    )
    with Database(":memory:") as db:
        _seed(db)
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    # Timer status line is printed, and the subsequent SELECT adds Time:.
    assert "Timer: on" in out
    assert "Time:" in out


def test_meta_timer_off_disables_timing(monkeypatch, tmp_path, capsys):
    """``.timer off` cancels timing after a prior `.timer on`."""
    io = _make_io(
        monkeypatch,
        [
            ".timer on",
            ".timer off",
            "SELECT * FROM users",
            ".exit",
        ],
        tmp_path,
    )
    with Database(":memory:") as db:
        _seed(db)
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Timer: off" in out


def test_meta_timer_invalid_arg_writes_error(
    monkeypatch, tmp_path, capsys
):
    """``.timer yes` writes ERROR to stderr."""
    io = _make_io(
        monkeypatch,
        [".timer yes", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert ".timer on|off" in captured.err


# ---------------------------------------------------------------------------
# 6.5.10  .format <table|csv|json> — switch output format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["table", "csv", "json"])
def test_meta_format_switches_output(monkeypatch, tmp_path, capsys, fmt):
    """``.format <fmt>` prints the format banner and changes the renderer."""
    io = _make_io(
        monkeypatch,
        [f".format {fmt}", "SELECT id FROM users", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        _seed(db)
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert f"Format: {fmt}" in out
    # Each format has a distinctive signature:
    #   table → "---" separator line
    #   csv   → "id" header column
    #   json  → "[" opening bracket
    if fmt == "table":
        assert "---" in out
    elif fmt == "csv":
        assert "id" in out
    elif fmt == "json":
        assert "[" in out


def test_meta_format_invalid_writes_error(monkeypatch, tmp_path, capsys):
    """``.format markdown` writes ERROR to stderr."""
    io = _make_io(monkeypatch, [".format markdown", ".exit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert ".format" in captured.err


# ---------------------------------------------------------------------------
# 6.5.11  .color on|off — toggle ANSI colour
# ---------------------------------------------------------------------------


def test_meta_color_on_off_prints_status(monkeypatch, tmp_path, capsys):
    """``.color on` and ``.color off` both print the new state."""
    io = _make_io(
        monkeypatch,
        [".color on", ".color off", ".exit"],
        tmp_path,
    )
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Color: on" in out
    assert "Color: off" in out


def test_meta_color_invalid_writes_error(monkeypatch, tmp_path, capsys):
    """``.color maybe` writes ERROR to stderr."""
    io = _make_io(monkeypatch, [".color maybe", ".exit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err


# ---------------------------------------------------------------------------
# 6.5.12  Unknown meta command writes ERROR
# ---------------------------------------------------------------------------


def test_meta_unknown_command_writes_error(monkeypatch, tmp_path, capsys):
    """``.bogus` writes `ERROR: unknown command: .bogus` to stderr."""
    io = _make_io(monkeypatch, [".bogus", ".exit"], tmp_path)
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "unknown command" in captured.err
    assert ".bogus" in captured.err
