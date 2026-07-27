"""Unit tests for the thin REPL entry point and SQL execution (Task 5)."""
import json

import pytest

from tinydb._repl_meta import ReplState
from tinydb.database import Database
from tinydb.repl import (
    HISTORY_LENGTH,
    USAGE,
    _format_table,
    _interactive_loop,
    _is_unterminated,
    _run_sql,
    _state,
    main,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1;", False),
        ("INSERT INTO t(id) VALUES (", True),
        ("SELECT 'unterminated", True),
    ],
)
def test_repl_is_unterminated_re_export(sql, expected):
    assert _is_unterminated(sql) is expected


def test_repl_format_table_reexport_empty_rows():
    assert _format_table([]) == "(no rows)"


def test_repl_exports_legacy_constants_and_state():
    assert HISTORY_LENGTH == 1000
    assert USAGE == "Usage: tinydb-repl [--database PATH]"
    assert isinstance(_state, ReplState)


# ---------------------------------------------------------------------------
# _run_sql output and error handling
# ---------------------------------------------------------------------------


def test_run_sql_ok_for_create(capsys):
    with Database(":memory:") as db:
        _run_sql(db, "CREATE TABLE t(id INT)", ReplState())
    assert capsys.readouterr().out == "OK\n"


def test_run_sql_no_rows(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT)")
        _run_sql(db, "SELECT * FROM t", ReplState())
    assert capsys.readouterr().out == "(no rows)\n"


def test_run_sql_table_format_default(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT)")
        db.execute("INSERT INTO t(id) VALUES (1)")
        _run_sql(db, "SELECT id FROM t", ReplState())
    assert "id" in capsys.readouterr().out


def test_run_sql_csv_format(capsys):
    state = ReplState(output_format="csv")
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT)")
        db.execute("INSERT INTO t(id) VALUES (1)")
        _run_sql(db, "SELECT id FROM t", state)
    out = capsys.readouterr().out
    assert "id" in out
    assert "1" in out


def test_run_sql_json_format(capsys):
    state = ReplState(output_format="json")
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT)")
        db.execute("INSERT INTO t(id) VALUES (1)")
        _run_sql(db, "SELECT id FROM t", state)
    assert json.loads(capsys.readouterr().out) == [{"id": 1}]


def test_run_sql_timer_appends_time_line(capsys):
    state = ReplState(timer_enabled=True)
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT)")
        _run_sql(db, "SELECT * FROM t", state)
    out = capsys.readouterr().out
    assert "Time:" in out
    assert "ms" in out


def test_run_sql_no_time_when_timer_off(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT)")
        _run_sql(db, "SELECT * FROM t", ReplState())
    assert "Time:" not in capsys.readouterr().out


def test_run_sql_prints_single_line_error(capsys):
    with Database(":memory:") as db:
        _run_sql(db, "SELECT FROM", ReplState())
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR:")
    assert "Traceback" not in captured.err
    assert len(captured.err.splitlines()) == 1


# ---------------------------------------------------------------------------
# main() arguments and startup behavior
# ---------------------------------------------------------------------------


def test_main_help_returns_zero(capsys):
    assert main(["--help"]) == 0
    assert capsys.readouterr().out == USAGE + "\n"


def test_main_unknown_argument_returns_two(capsys):
    assert main(["data.db"]) == 2
    assert "ERROR: invalid argument" in capsys.readouterr().err


def test_main_default_memory(monkeypatch, tmp_path):
    import tinydb.repl as repl

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(repl, "_interactive_loop", lambda db, io, state: 0)
    assert repl.main([]) == 0
    assert list(tmp_path.iterdir()) == []


def test_main_database_expands_home(monkeypatch, tmp_path):
    import tinydb.repl as repl

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(repl, "_interactive_loop", lambda db, io, state: 0)
    assert repl.main(["--database", "~/persist.db"]) == 0
    assert (tmp_path / "persist.db").exists()


def test_main_prints_startup_hint_before_loop(monkeypatch, capsys):
    import tinydb.repl as repl

    monkeypatch.setattr(repl, "_interactive_loop", lambda db, io, state: 0)
    assert repl.main([]) == 0
    assert ".help for commands, .timer on for timing" in capsys.readouterr().out


def test_main_uses_fallback_when_prompt_toolkit_missing(monkeypatch, tmp_path, capsys):
    import tinydb._repl_io as io_mod
    import tinydb.repl as repl

    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(repl, "_interactive_loop", lambda db, io, state: 0)
    assert repl.main([]) == 0
    assert "WARNING" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _interactive_loop driven by the ReplIO protocol
# ---------------------------------------------------------------------------


class FakeIO:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.history = []

    def read_statement(self):
        try:
            return next(self._responses)
        except StopIteration:
            return None

    def add_history(self, statement):
        if statement.strip():
            self.history.append(statement)

    def save_history(self):
        return None


def test_interactive_loop_empty_then_eof():
    io = FakeIO(["", ""])
    with Database(":memory:") as db:
        assert _interactive_loop(db, io, ReplState()) == 0
    assert io.history == []


def test_interactive_loop_exit_quit_return_zero():
    for command in (".exit", ".quit"):
        io = FakeIO([command])
        with Database(":memory:") as db:
            assert _interactive_loop(db, io, ReplState()) == 0


def test_interactive_loop_help_then_eof(capsys):
    io = FakeIO([".help", ""])
    with Database(":memory:") as db:
        assert _interactive_loop(db, io, ReplState()) == 0
    assert "Meta commands:" in capsys.readouterr().out
    assert io.history == []


def test_interactive_loop_executes_sql_then_eof(capsys):
    """SQL execution path prints OK; loop exits cleanly on EOF.

    The ``FakeIO`` stub is not a ``FallbackReplIO``; the loop therefore
    does not call ``add_history`` for it.  History bookkeeping is
    covered by ``test_fallback_replio_records_history`` and
    ``test_prompt_toolkit_replio_add_history_is_noop``.
    """
    io = FakeIO(["CREATE TABLE t(id INT);", ""])
    with Database(":memory:") as db:
        assert _interactive_loop(db, io, ReplState()) == 0
    assert "OK" in capsys.readouterr().out
    # The stub's history is untouched because the loop only calls
    # add_history for FallbackReplIO.
    assert io.history == []


def test_interactive_loop_blank_silenced(capsys):
    io = FakeIO(["   ", ""])
    with Database(":memory:") as db:
        assert _interactive_loop(db, io, ReplState()) == 0
    assert capsys.readouterr().out == ""
    assert io.history == []


def test_interactive_loop_meta_does_not_enter_history(capsys):
    io = FakeIO([".help", ""])
    with Database(":memory:") as db:
        _interactive_loop(db, io, ReplState())
    assert io.history == []
    assert "Meta commands:" in capsys.readouterr().out
