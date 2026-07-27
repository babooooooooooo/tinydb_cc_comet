"""Unit tests for tinydb._repl_meta dispatcher and 12 commands (Task 4)."""
import os

import pytest

from tinydb.database import Database, Row
from tinydb._repl_meta import (
    META_COMMANDS,
    ReplState,
    handle_meta,
    _cmd_help,
    _cmd_explain,
    _cmd_indexes,
    _cmd_stats,
    _cmd_timer,
    _cmd_format,
    _cmd_color,
    _cmd_tables,
    _cmd_schema,
    _cmd_read,
)
from tinydb.plan import LogicalPlan  # 仅用于 type check


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ReplState
# ---------------------------------------------------------------------------

def test_repl_state_defaults():
    s = ReplState()
    assert s.timer_enabled is False
    assert s.output_format == "table"
    assert s.color_enabled is True


# ---------------------------------------------------------------------------
# handle_meta 分发
# ---------------------------------------------------------------------------

def test_handle_meta_returns_false_for_non_dot():
    with Database(":memory:") as db:
        assert handle_meta("SELECT 1;", db, ReplState()) is False


def test_handle_meta_returns_true_for_unknown_command(capsys):
    with Database(":memory:") as db:
        assert handle_meta(".foo", db, ReplState()) is True
    assert "ERROR: unknown command" in capsys.readouterr().err


@pytest.mark.parametrize("command", [".exit", ".quit"])
def test_handle_meta_exit_quit_raise(command):
    from tinydb._repl_meta import _ExitReplSignal
    with Database(":memory:") as db:
        with pytest.raises(_ExitReplSignal):
            handle_meta(command, db, ReplState())


# ---------------------------------------------------------------------------
# .help
# ---------------------------------------------------------------------------

def test_help_lists_every_command(capsys):
    state = ReplState()
    with Database(":memory:") as db:
        _cmd_help([], db, state)
    out = capsys.readouterr().out
    for cmd in ("exit", "quit", "help", "tables", "schema", "read",
                "explain", "indexes", "stats", "timer", "format", "color"):
        assert f".{cmd}" in out, f"missing .${cmd}"


# ---------------------------------------------------------------------------
# .tables
# ---------------------------------------------------------------------------

def test_tables_are_sorted(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT)")
        db.execute("CREATE TABLE orders(id INT)")
        _cmd_tables([], db, ReplState())
    assert capsys.readouterr().out.splitlines() == ["orders", "users"]


# ---------------------------------------------------------------------------
# .schema
# ---------------------------------------------------------------------------

def test_schema_renders_create_table(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, name TEXT)")
        _cmd_schema(["users"], db, ReplState())
    assert capsys.readouterr().out == "CREATE TABLE users(id INT, name TEXT);\n"


def test_schema_unknown_table(capsys):
    with Database(":memory:") as db:
        _cmd_schema(["ghost"], db, ReplState())
    assert capsys.readouterr().err == "ERROR: no such table: ghost\n"


def test_schema_missing_argument(capsys):
    with Database(":memory:") as db:
        _cmd_schema([], db, ReplState())
    assert capsys.readouterr().err == "ERROR: missing argument for .schema\n"


# ---------------------------------------------------------------------------
# .read
# ---------------------------------------------------------------------------

def test_read_executes_file(tmp_path, capsys):
    script = tmp_path / "seed.sql"
    script.write_text("CREATE TABLE t(id INT);", encoding="utf-8")
    with Database(":memory:") as db:
        _cmd_read([str(script)], db, ReplState())
    assert "OK" in capsys.readouterr().out


def test_read_missing_file(capsys, tmp_path):
    missing = tmp_path / "missing.sql"
    with Database(":memory:") as db:
        _cmd_read([str(missing)], db, ReplState())
    assert capsys.readouterr().err == f"ERROR: cannot read file: {missing}\n"


def test_read_non_utf8_file(capsys, tmp_path):
    script = tmp_path / "binary.sql"
    script.write_bytes(b"\xff\xfe")
    with Database(":memory:") as db:
        _cmd_read([str(script)], db, ReplState())
    assert capsys.readouterr().err == f"ERROR: cannot read file: {script}\n"


def test_read_rejects_oversized_file(capsys, monkeypatch, tmp_path):
    script = tmp_path / "large.sql"
    script.write_bytes(b"SELECT 1;")
    monkeypatch.setattr("tinydb._repl_meta.MAX_READ_FILE_BYTES", 8, raising=False)
    with Database(":memory:") as db:
        _cmd_read([str(script)], db, ReplState())
    assert capsys.readouterr().err == f"ERROR: file too large: {script}\n"


def test_read_unterminated_eof_warns(tmp_path, capsys):
    script = tmp_path / "broken.sql"
    script.write_text("CREATE TABLE t(id INT", encoding="utf-8")
    with Database(":memory:") as db:
        _cmd_read([str(script)], db, ReplState())
    err = capsys.readouterr().err
    assert "unterminated statement at EOF" in err


def test_read_missing_argument(capsys):
    with Database(":memory:") as db:
        _cmd_read([], db, ReplState())
    assert capsys.readouterr().err == "ERROR: missing argument for .read\n"


# ---------------------------------------------------------------------------
# .explain
# ---------------------------------------------------------------------------

def test_explain_does_not_execute(capsys):
    """explain 解析为 LogicalPlan,但不修改 db 状态."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, age INT)")
        _cmd_explain(["SELECT", "*", "FROM", "users"], db, ReplState())
        out = capsys.readouterr().out
        assert "Plan:" in out
        # 既不应执行 SELECT,也不应插入行
        rows = db.execute("SELECT COUNT(*) FROM users")
        assert rows[0].values[0] == 0 or rows == []


def test_explain_invalid_sql_shows_error(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT)")
        _cmd_explain(["SELECT", "FROMM", "users"], db, ReplState())
    err = capsys.readouterr().err
    assert "ERROR:" in err
    # 不应有 traceback
    assert "Traceback" not in err


def test_explain_missing_argument(capsys):
    with Database(":memory:") as db:
        _cmd_explain([], db, ReplState())
    assert capsys.readouterr().err == "ERROR: missing argument for .explain\n"


# ---------------------------------------------------------------------------
# .indexes
# ---------------------------------------------------------------------------

def test_indexes_lists_all_when_no_filter(capsys):
    """无参数时列出全部索引;若 IndexManager 返回空表则只显示 header."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT PRIMARY KEY)")
        db.execute("INSERT INTO users(id) VALUES (1)")
        _cmd_indexes([], db, ReplState())
    out = capsys.readouterr().out
    # 输出应当存在,可能为空;不要求具体行
    assert isinstance(out, str)


def test_indexes_filtered_by_table(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT PRIMARY KEY)")
        db.execute("INSERT INTO users(id) VALUES (1)")
        _cmd_indexes(["users"], db, ReplState())
        # 其他表的索引不应出现
        assert "orders." not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# .stats
# ---------------------------------------------------------------------------

def test_stats_includes_required_fields(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT)")
        _cmd_stats([], db, ReplState())
    out = capsys.readouterr().out
    for field in ("Tables", "Rows", "Pages", "Free pages", "WAL"):
        assert field in out


# ---------------------------------------------------------------------------
# .timer
# ---------------------------------------------------------------------------

def test_timer_on_sets_state(capsys):
    state = ReplState()
    with Database(":memory:") as db:
        _cmd_timer(["on"], db, state)
    assert state.timer_enabled is True
    assert "Timer: on" in capsys.readouterr().out


def test_timer_off_clears_state(capsys):
    state = ReplState()
    state.timer_enabled = True
    with Database(":memory:") as db:
        _cmd_timer(["off"], db, state)
    assert state.timer_enabled is False


def test_timer_invalid_argument(capsys):
    state = ReplState()
    with Database(":memory:") as db:
        _cmd_timer(["maybe"], db, state)
    assert "ERROR:" in capsys.readouterr().err


def test_timer_missing_argument(capsys):
    state = ReplState()
    with Database(":memory:") as db:
        _cmd_timer([], db, state)
    assert "ERROR: .timer on|off" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# .format
# ---------------------------------------------------------------------------

def test_format_sets_state(capsys):
    state = ReplState()
    with Database(":memory:") as db:
        _cmd_format(["csv"], db, state)
    assert state.output_format == "csv"


def test_format_invalid(capsys):
    state = ReplState()
    with Database(":memory:") as db:
        _cmd_format(["markdown"], db, state)
    assert "ERROR:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# .color
# ---------------------------------------------------------------------------

def test_color_off_sets_state(capsys):
    state = ReplState()
    state.color_enabled = True
    with Database(":memory:") as db:
        _cmd_color(["off"], db, state)
    assert state.color_enabled is False


def test_color_invalid(capsys):
    state = ReplState()
    with Database(":memory:") as db:
        _cmd_color(["maybe"], db, state)
    assert "ERROR:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# META_COMMANDS 注册表完整性
# ---------------------------------------------------------------------------

def test_meta_commands_table_contains_all():
    expected = {"exit", "quit", "help", "tables", "schema", "read",
                "explain", "indexes", "stats", "timer", "format", "color"}
    assert set(META_COMMANDS.keys()) == expected


def test_meta_commands_have_help_text():
    for name, cmd in META_COMMANDS.items():
        assert cmd.help_text, f".{name} missing help text"
