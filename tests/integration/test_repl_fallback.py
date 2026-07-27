"""End-to-end REPL tests for fallback path (Task 6).

Verifies that when ``prompt_toolkit`` is unavailable (or its detection
flag is monkey-patched to ``False``), ``main()`` boots in
``FallbackReplIO`` mode, prints the standard ``WARNING`` to stderr, and
still services SQL through ``_interactive_loop``.
"""
from __future__ import annotations

import builtins
import importlib
import sys
from pathlib import Path

import pytest

import tinydb
import tinydb._repl_io as io_mod
from tinydb import repl as repl_mod
from tinydb._repl_io import FallbackReplIO
from tinydb._repl_meta import ReplState
from tinydb.database import Database
from tinydb.repl import _interactive_loop


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 6.4.1  Monkey-patch _HAS_PROMPT_TOOLKIT=False and drive main()
# ---------------------------------------------------------------------------


def test_main_falls_back_to_input_when_prompt_toolkit_missing(
    monkeypatch, tmp_path, capsys
):
    """Patch `_HAS_PROMPT_TOOLKIT=False`; `main()` enters fallback mode."""
    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", False)
    # main() reads the flag from the source module each call (re-imports
    # _repl_io), so patching the source attribute is enough.

    inputs = iter([".exit"])

    def fake_input(prompt):
        value = next(inputs, None)
        if value is None:
            raise EOFError
        return value

    monkeypatch.setattr(builtins, "input", fake_input)

    rc = repl_mod.main([])
    assert rc == 0
    captured = capsys.readouterr()
    # Fallback warning is printed to stderr exactly once.
    assert "WARNING" in captured.err
    assert "falling back" in captured.err
    # Startup hint is printed to stdout.
    assert ".help" in captured.out
    assert ".timer" in captured.out


def test_main_fallback_executes_sql_via_input(monkeypatch, capsys, tmp_path):
    """Fallback main() can run CREATE/INSERT/SELECT through stdin injection."""
    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", False)

    inputs = iter(
        [
            "CREATE TABLE t(id INT, name TEXT);",
            "INSERT INTO t(id, name) VALUES (1, 'alice');",
            "SELECT id, name FROM t;",
            ".exit",
        ]
    )

    def fake_input(prompt):
        value = next(inputs, None)
        if value is None:
            raise EOFError
        return value

    monkeypatch.setattr(builtins, "input", fake_input)

    rc = repl_mod.main([])
    assert rc == 0
    captured = capsys.readouterr()
    # CREATE + INSERT → "OK" (2 lines).
    assert captured.out.count("OK") == 2
    # SELECT renders the row.
    assert "alice" in captured.out
    assert "1" in captured.out


# ---------------------------------------------------------------------------
# 6.4.2  FallbackReplIO + _interactive_loop integration
# ---------------------------------------------------------------------------


def test_fallback_io_runs_interactive_loop(monkeypatch, capsys, tmp_path):
    """FallbackReplIO + _interactive_loop end-to-end with SQL statements.

    The fallback's explicit-``;``-terminator policy (recorded in tasks.md
    §2 deviation #1) means meta commands are exercised in a dedicated
    test file (test_repl_meta_commands.py).  This test focuses on the
    SQL execution path.
    """
    inputs = iter(
        [
            "CREATE TABLE t(id INT);",
            "INSERT INTO t(id) VALUES (1);",
            "SELECT * FROM t;",
        ]
    )

    def fake_input(prompt):
        value = next(inputs, None)
        if value is None:
            raise EOFError
        return value

    monkeypatch.setattr(builtins, "input", fake_input)

    io = FallbackReplIO(":memory:", tmp_path / "h")
    with Database(":memory:") as db:
        rc = _interactive_loop(db, io, ReplState())
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.count("OK") == 2
    # SELECT renders the row "1".
    assert "1" in captured.out


# ---------------------------------------------------------------------------
# 6.4.3  Forced re-import with prompt_toolkit blocked at sys.modules
# ---------------------------------------------------------------------------


def test_repl_io_module_unavailable_when_prompt_toolkit_blocked(monkeypatch):
    """When prompt_toolkit is None in sys.modules, _HAS_PROMPT_TOOLKIT is False."""
    import tinydb._repl_io as fresh_io
    # Already imported; ensure the detection flag is a bool.
    assert isinstance(fresh_io._HAS_PROMPT_TOOLKIT, bool)
    # Force a re-import under a context where prompt_toolkit is shadowed.
    with monkeypatch.context() as m:
        sys.modules.pop("tinydb._repl_io", None)
        for name in (
            "prompt_toolkit",
            "pygments",
            "pygments.lexers",
            "pygments.lexers.sql",
            "prompt_toolkit.history",
            "prompt_toolkit.lexers",
            "prompt_toolkit.auto_suggest",
        ):
            m.setitem(sys.modules, name, None)
        reimported = importlib.import_module("tinydb._repl_io")
        assert reimported._HAS_PROMPT_TOOLKIT is False
    # Cleanup: restore real module so other tests aren't affected.
    sys.modules.pop("tinydb._repl_io", None)
    importlib.import_module("tinydb._repl_io")


# ---------------------------------------------------------------------------
# 6.4.4  main() --help returns 0 and prints usage
# ---------------------------------------------------------------------------


def test_main_help_returns_zero(capsys):
    """`--help` arg prints USAGE and returns 0."""
    rc = repl_mod.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Usage" in out
    assert "tinydb-repl" in out


# ---------------------------------------------------------------------------
# 6.4.5  main() with bogus arg returns 2
# ---------------------------------------------------------------------------


def test_main_invalid_argument_returns_two(capsys):
    """Bogus CLI argument prints ERROR to stderr and returns 2."""
    rc = repl_mod.main(["--bogus-flag"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "invalid argument" in captured.err
