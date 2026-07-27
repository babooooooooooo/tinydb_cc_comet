"""End-to-end REPL tests for color-off environment (Task 6).

Verifies the REPL honours the ``NO_COLOR`` environment variable and the
``TERM=dumb`` convention by running ``_interactive_loop`` (or invoking
``_run_sql`` + format dispatch) and asserting no ANSI escape codes
(``\\x1b[...m``) leak into stdout or stderr.
"""
from __future__ import annotations

import re

import pytest

from tinydb._repl_io import _color_enabled
from tinydb._repl_meta import ReplState
from tinydb.database import Database
from tinydb.repl import _run_sql


pytestmark = pytest.mark.integration


# Match an ANSI escape sequence (CSI) — common: \x1b[...m for SGR, but
# also includes cursor movement / erase / etc.  Sufficient for our scope.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _assert_no_ansi(text: str) -> None:
    """Fail the test if *text* contains any ANSI escape sequence."""
    match = _ANSI_RE.search(text)
    assert match is None, f"unexpected ANSI escape: {match.group(0)!r} in {text!r}"


# ---------------------------------------------------------------------------
# 6.3.1  _color_enabled reflects NO_COLOR=1
# ---------------------------------------------------------------------------


def test_no_color_env_disables_color(monkeypatch):
    """NO_COLOR=1 must turn off colour even if TERM is a real terminal."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _color_enabled() is False


def test_term_dumb_disables_color(monkeypatch):
    """TERM=dumb disables colour (per NO_COLOR convention)."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert _color_enabled() is False


def test_color_on_when_no_no_color_and_real_term(monkeypatch):
    """Absence of NO_COLOR + non-dumb TERM enables colour."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _color_enabled() is True


# ---------------------------------------------------------------------------
# 6.3.2  _run_sql output under NO_COLOR has no ANSI codes
# ---------------------------------------------------------------------------


def test_run_sql_output_clean_when_no_color(monkeypatch, capsys):
    """`SELECT` rendered under NO_COLOR contains no ANSI escape sequences."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    state = ReplState()
    state.color_enabled = _color_enabled()  # False under NO_COLOR
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'alice')")
        _run_sql(db, "SELECT id, name FROM t", state)
    captured = capsys.readouterr()
    _assert_no_ansi(captured.out)
    _assert_no_ansi(captured.err)


def test_run_sql_timer_output_clean_when_no_color(monkeypatch, capsys):
    """Timer-enabled output under NO_COLOR is also ANSI-free."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    state = ReplState(timer_enabled=True)
    state.color_enabled = _color_enabled()
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT)")
        _run_sql(db, "SELECT * FROM t", state)
    captured = capsys.readouterr()
    _assert_no_ansi(captured.out)
    _assert_no_ansi(captured.err)
    assert "Time:" in captured.out


# ---------------------------------------------------------------------------
# 6.3.3  Format-dispatch (table/csv/json) under NO_COLOR is clean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["table", "csv", "json"])
def test_format_dispatch_clean_when_no_color(monkeypatch, capsys, fmt):
    """All three formats render ANSI-free when NO_COLOR is set."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    state = ReplState(output_format=fmt)
    state.color_enabled = _color_enabled()
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t(id INT, name TEXT)")
        db.execute("INSERT INTO t(id, name) VALUES (1, 'alice')")
        _run_sql(db, "SELECT id, name FROM t", state)
    captured = capsys.readouterr()
    _assert_no_ansi(captured.out)
    _assert_no_ansi(captured.err)
