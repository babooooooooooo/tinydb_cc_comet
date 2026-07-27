"""End-to-end REPL tests for meta commands via FallbackReplIO (Round 1 review).

Verifies that after the Round 1 review fix, ``FallbackReplIO.read_statement``
special-cases lines starting with ``.`` and returns them to the caller
without requiring a ``;`` terminator or accumulation.  This means the
``_interactive_loop`` can hand meta commands to ``handle_meta`` even when
``prompt_toolkit`` is unavailable or the user pipes a script.
"""
from __future__ import annotations

import builtins
from pathlib import Path

import pytest

import tinydb._repl_io as io_mod
from tinydb._repl_io import FallbackReplIO
from tinydb._repl_meta import ReplState
from tinydb.database import Database
from tinydb.repl import _interactive_loop


pytestmark = pytest.mark.integration


def _drive(monkeypatch, lines: list[str], db: Database) -> int:
    """Run ``_interactive_loop`` with a FallbackReplIO + canned ``input``."""
    iterator = iter(lines)

    def fake_input(prompt):
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    io = FallbackReplIO(":memory:", Path("/tmp/none"))
    return _interactive_loop(db, io, ReplState())


# ---------------------------------------------------------------------------
# Meta command routing through FallbackReplIO
# ---------------------------------------------------------------------------


def test_fallback_help_prints_meta_command_list(monkeypatch, capsys):
    """``.help` is routed through the fallback adapter and prints commands."""
    rc = _drive(monkeypatch, [".help", ".exit"], Database(":memory:"))
    assert rc == 0
    out = capsys.readouterr().out
    # Canonical new commands must show up in the help listing.
    assert ".tables" in out
    assert ".color" in out
    assert ".format" in out
    assert ".timer" in out


def test_fallback_exit_raises_signal(monkeypatch, capsys):
    """``.exit` reaches handle_meta and triggers _ExitReplSignal."""
    rc = _drive(monkeypatch, [".exit"], Database(":memory:"))
    assert rc == 0


def test_fallback_color_toggles_state(monkeypatch, capsys):
    """``.color on|off` is processed through the fallback adapter."""
    rc = _drive(
        monkeypatch,
        [".color on", ".color off", ".exit"],
        Database(":memory:"),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Color: on" in out
    assert "Color: off" in out


def test_fallback_tables_lists_catalog(monkeypatch, capsys):
    """``.tables` lists catalog tables when invoked through the fallback."""
    db = Database(":memory:")
    db.execute("CREATE TABLE alpha(id INT)")
    db.execute("CREATE TABLE beta(id INT)")
    rc = _drive(monkeypatch, [".tables", ".exit"], db)
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out


def test_fallback_meta_does_not_require_terminator(monkeypatch, capsys):
    """Meta commands are returned by ``read_statement`` even without ``;``.

    The fallback's ``_is_unterminated`` and ``;`` checks previously
    required SQL terminators, so ``.help`` (no terminator) was lost.
    This test asserts the Round 1 fix: a meta line is yielded as-is.
    """
    io = FallbackReplIO(":memory:", Path("/tmp/none"))

    def fake_input(prompt):
        return ".help"

    monkeypatch.setattr(builtins, "input", fake_input)
    assert io.read_statement() == ".help"
    # The internal buffer must remain empty — meta lines are not accumulated.
    assert io._buf == ""


def test_fallback_meta_does_not_pollute_buffer(monkeypatch, capsys):
    """A meta line preceding SQL does not pollute the SQL buffer."""
    io = FallbackReplIO(":memory:", Path("/tmp/none"))
    responses = iter([".help", "SELECT 1"])

    def fake_input(prompt):
        try:
            return next(responses)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    # First call returns the meta line directly.
    assert io.read_statement() == ".help"
    # The buffer must be empty so the next SQL fragment is a fresh start.
    assert io._buf == ""
    # Second call accumulates the SQL until the terminator (never reached
    # before EOF here — we only assert the buffer logic, not the SQL run).
    assert io.read_statement() == ""
    assert io.read_statement() is None  # EOF -> None
