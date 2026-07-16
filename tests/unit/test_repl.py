"""Unit coverage for REPL state, formatting, meta commands, history, and SQL output."""
import builtins
import sys
from types import SimpleNamespace

import pytest

from tinydb.database import Database, Row
from tinydb.repl import (
    HISTORY_LENGTH,
    _ExitRepl,
    _format_table,
    _handle_meta,
    _is_unterminated,
    _make_prompt,
    _read_one_statement,
    _run_sql,
    _save_history,
    _setup_history,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1;", False),
        ("INSERT INTO t(id) VALUES (", True),
        ("INSERT INTO t(id) VALUES (1)", False),
        ("INSERT INTO t(name) VALUES ('alice", True),
        ("INSERT INTO t(name) VALUES ('o''brien');", False),
        ("SELECT 1 -- ( ignored\n", False),
        ("SELECT 1 /* unterminated", True),
        ("-- leading comment\nSELECT 1;", False),
    ],
)
def test_is_unterminated_sql_aware(sql, expected):
    assert _is_unterminated(sql) is expected


@pytest.mark.unit
def test_format_table_header_separator_and_rows():
    rows = [
        Row(values=(1, "alice"), columns=("id", "name")),
        Row(values=(2, "bob"), columns=("id", "name")),
    ]
    output = _format_table(rows)
    assert "id | name" in output
    assert "--- | ---" in output
    assert "1  | alice" in output
    assert "2  | bob" in output


@pytest.mark.unit
def test_format_table_truncates_at_thirty_characters():
    output = _format_table([Row(values=("x" * 31,), columns=("value",))])
    assert "x" * 29 + "…" in output
    assert "x" * 30 not in output


@pytest.mark.unit
def test_format_table_empty_rows():
    assert _format_table([]) == "(no rows)"


@pytest.mark.unit
def test_make_prompt_contains_database_path():
    assert _make_prompt(":memory:") == "tinydb> [:memory:] "
    assert _make_prompt("data.db") == "tinydb> [data.db] "


@pytest.mark.unit
def test_read_one_statement_returns_input(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt: "SELECT 1;")
    assert _read_one_statement("tinydb> ") == "SELECT 1;"


@pytest.mark.unit
def test_read_one_statement_maps_eof_to_none(monkeypatch):
    def raise_eof(prompt):
        raise EOFError

    monkeypatch.setattr(builtins, "input", raise_eof)
    assert _read_one_statement("tinydb> ") is None


@pytest.mark.unit
@pytest.mark.parametrize("command", [".exit", ".quit"])
def test_exit_meta_commands_raise_control_flow(command):
    with Database(":memory:") as db, pytest.raises(_ExitRepl):
        _handle_meta(command, db)


@pytest.mark.unit
def test_help_lists_every_meta_command(capsys):
    with Database(":memory:") as db:
        assert _handle_meta(".help", db) is True
    output = capsys.readouterr().out
    for command in (".exit", ".quit", ".help", ".tables", ".schema", ".read"):
        assert command in output


@pytest.mark.unit
def test_tables_are_sorted(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT)")
        db.execute("CREATE TABLE orders(id INT)")
        _handle_meta(".tables", db)
    assert capsys.readouterr().out.splitlines() == ["orders", "users"]


@pytest.mark.unit
def test_schema_renders_create_table(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, name TEXT)")
        _handle_meta(".schema users", db)
    assert capsys.readouterr().out == "CREATE TABLE users(id INT, name TEXT);\n"


@pytest.mark.unit
def test_schema_unknown_table(capsys):
    with Database(":memory:") as db:
        _handle_meta(".schema ghost", db)
    assert capsys.readouterr().err == "ERROR: no such table: ghost\n"


@pytest.mark.unit
def test_schema_missing_argument(capsys):
    with Database(":memory:") as db:
        _handle_meta(".schema", db)
    assert capsys.readouterr().err == "ERROR: missing argument for .schema\n"


@pytest.mark.unit
def test_read_missing_argument(capsys):
    with Database(":memory:") as db:
        _handle_meta(".read", db)
    assert capsys.readouterr().err == "ERROR: missing argument for .read\n"


@pytest.mark.unit
def test_read_missing_file(capsys, tmp_path):
    missing = tmp_path / "missing.sql"
    with Database(":memory:") as db:
        _handle_meta(f".read {missing}", db)
    assert capsys.readouterr().err == f"ERROR: cannot read file: {missing}\n"


@pytest.mark.unit
def test_unknown_meta_command(capsys):
    with Database(":memory:") as db:
        _handle_meta(".foo", db)
    assert capsys.readouterr().err == "ERROR: unknown command: .foo\n"


@pytest.mark.unit
def test_run_sql_distinguishes_ok_empty_and_rows(capsys):
    with Database(":memory:") as db:
        _run_sql(db, "CREATE TABLE t(id INT)")
        assert capsys.readouterr().out == "OK\n"
        _run_sql(db, "SELECT * FROM t")
        assert capsys.readouterr().out == "(no rows)\n"
        db.execute("INSERT INTO t(id) VALUES (1)")
        _run_sql(db, "SELECT * FROM t")
        assert "id" in capsys.readouterr().out


@pytest.mark.unit
def test_run_sql_prints_single_line_error(capsys):
    with Database(":memory:") as db:
        _run_sql(db, "SELECT FROM")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR: ParseError:")
    assert len(captured.err.splitlines()) == 1


@pytest.mark.unit
def test_setup_history_expands_home(monkeypatch, tmp_path):
    calls = []
    fake_readline = SimpleNamespace(
        read_history_file=lambda path: calls.append(("read", path)),
        set_history_length=lambda length: calls.append(("length", length)),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "readline", fake_readline)
    assert _setup_history() is True
    assert calls == [
        ("read", str(tmp_path / ".tinydb_history")),
        ("length", HISTORY_LENGTH),
    ]


@pytest.mark.unit
def test_setup_history_ignores_missing_file(monkeypatch):
    def missing(path):
        raise OSError("missing")

    fake_readline = SimpleNamespace(
        read_history_file=missing,
        set_history_length=lambda length: None,
    )
    monkeypatch.setitem(sys.modules, "readline", fake_readline)
    assert _setup_history() is True


@pytest.mark.unit
def test_setup_history_falls_back_without_readline(monkeypatch):
    real_import = builtins.__import__

    def import_without_readline(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "readline":
            raise ImportError("readline unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_readline)
    assert _setup_history() is False


@pytest.mark.unit
def test_save_history_uses_expanded_home(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "readline",
        SimpleNamespace(write_history_file=lambda path: calls.append(path)),
    )
    assert _save_history(True) is None
    assert calls == [str(tmp_path / ".tinydb_history")]


@pytest.mark.unit
def test_save_history_ignores_write_failure(monkeypatch):
    def fail(path):
        raise OSError("disk full")

    monkeypatch.setitem(
        sys.modules,
        "readline",
        SimpleNamespace(write_history_file=fail),
    )
    assert _save_history(True) is None
    assert _save_history(False) is None
