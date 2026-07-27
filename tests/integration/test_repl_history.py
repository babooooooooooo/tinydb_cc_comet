"""End-to-end tests for no-double-write history (Round 1 review).

The Round 1 review found that ``PromptToolkitReplIO.add_history`` was
called from ``_interactive_loop`` even though ``Buffer.validate_and_handle``
already appends the entered text to the session's history on submit.
Each SQL statement was therefore recorded twice, polluting up-arrow
recall.  The fix makes ``add_history`` a no-op on the prompt-toolkit
adapter and skips the loop's explicit call.

These tests assert the new contract: each SQL statement lands in the
session's history exactly once.
"""
from __future__ import annotations

import pytest

import tinydb._repl_io as io_mod
from tinydb._repl_io import PromptToolkitReplIO
from tinydb._repl_meta import ReplState
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
        self.history = kwargs.pop("history", None) or _FakeHistory()
        self._prompts = kwargs.pop("_prompts_queue", [])

    def prompt(self, prompt, multiline=False):  # noqa: ARG002
        if not self._prompts:
            raise EOFError
        value = self._prompts.pop(0)
        if value is None:
            raise EOFError
        # Simulate prompt_toolkit's auto-append to history on submit.
        if value and value.strip():
            self.history.append_string(value)
        return value


def _patch_prompt_toolkit(monkeypatch, prompts: list):
    """Wire monkey-patches; all sessions share one history instance.

    Returns the shared history.  ``PromptToolkitReplIO.__init__`` will
    pass ``history=FileHistory(...)`` to the factory, so the factory
    must replace that argument with our shared history before
    constructing the session.  This is how the real prompt_toolkit
    behaviour — a single FileHistory that survives ``set_color``
    rebuilds — is mirrored in the test.
    """
    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(io_mod, "FileHistory", lambda p: _FakeHistory())
    monkeypatch.setattr(io_mod, "AutoSuggestFromHistory", lambda: None)
    monkeypatch.setattr(io_mod, "PygmentsLexer", lambda l: None)
    monkeypatch.setattr(io_mod, "SqlLexer", object())

    shared_history = _FakeHistory()
    queue: list = list(prompts)

    def _session_factory(**kwargs):
        # Always use the shared history regardless of what was passed.
        kwargs["history"] = shared_history
        kwargs["_prompts_queue"] = queue
        return _FakeSession(**kwargs)

    monkeypatch.setattr(io_mod, "PromptSession", _session_factory)
    return shared_history


# ---------------------------------------------------------------------------
# 1. SQL persists exactly once per statement.
# ---------------------------------------------------------------------------


def test_prompt_toolkit_records_each_sql_once(monkeypatch, tmp_path):
    """Each non-empty SQL line is appended to history exactly once.

    Before the Round 1 fix, ``_interactive_loop`` called
    ``io.add_history`` for every executed statement, which in turn
    called ``self._session.history.append_string``.  Since the fake
    session also appended on ``prompt()`` (mirroring real
    prompt_toolkit), each statement appeared twice.  After the fix
    the loop does NOT call ``add_history`` for prompt-toolkit I/O and
    the record is a no-op, so each statement appears exactly once.
    """
    history = _patch_prompt_toolkit(
        monkeypatch,
        [
            "CREATE TABLE t(id INT);",
            "INSERT INTO t(id) VALUES (1);",
            "SELECT * FROM t;",
        ],
    )
    io_path = tmp_path / "h"
    io_path.touch()
    io = PromptToolkitReplIO(":memory:", io_path, False)
    with Database(":memory:") as db:
        _interactive_loop(db, io, ReplState())
    # Each SQL statement lands in the shared history exactly once.
    assert history.calls == [
        "CREATE TABLE t(id INT);",
        "INSERT INTO t(id) VALUES (1);",
        "SELECT * FROM t;",
    ]


def test_prompt_toolkit_meta_command_does_not_double_write(monkeypatch, tmp_path):
    """``.exit` (meta) does not get a second append via the loop's add_history.

    Even though the loop now skips ``add_history`` for prompt-toolkit
    I/O, the fake session still auto-records the submitted line
    (matching real prompt_toolkit's behaviour, where meta commands
    submitted via the prompt *are* stored for up-arrow recall).  This
    test pins the no-double-write invariant: 1 record per submitted
    line, regardless of whether the loop treats it as SQL or meta.
    """
    history = _patch_prompt_toolkit(
        monkeypatch,
        [
            ".exit",
        ],
    )
    io_path = tmp_path / "h"
    io_path.touch()
    io = PromptToolkitReplIO(":memory:", io_path, False)
    with Database(":memory:") as db:
        _interactive_loop(db, io, ReplState())
    # Exactly one append, not zero and not two.
    assert history.calls == [".exit"]


# ---------------------------------------------------------------------------
# 2. add_history is a no-op on PromptToolkitReplIO.
# ---------------------------------------------------------------------------


def test_prompt_toolkit_add_history_is_noop(monkeypatch, tmp_path):
    """Direct ``PromptToolkitReplIO.add_history`` does not record anything.

    The Round 1 fix makes the method a no-op so the loop can call it
    freely without double-writing.  This unit-style test pins the
    behaviour for callers that might rely on the prior contract.
    """
    _patch_prompt_toolkit(monkeypatch, [])
    io_path = tmp_path / "h"
    io_path.touch()
    io = PromptToolkitReplIO(":memory:", io_path, False)
    # Capture the original session's history.
    original_calls = list(io._session.history.calls)
    io.add_history("CREATE TABLE x(id INT);")
    io.add_history("   ")  # whitespace — historically filtered; now no-op
    io.add_history("SELECT 1;")
    # The session's history was untouched.
    assert io._session.history.calls == original_calls


# ---------------------------------------------------------------------------
# 3. FallbackReplIO still records via add_history (regression guard).
# ---------------------------------------------------------------------------


def test_fallback_replio_records_history(monkeypatch, tmp_path):
    """FallbackReplIO.add_history still appends to its in-memory history.

    The prompt-toolkit no-op is asymmetric on purpose: prompt_toolkit
    auto-records on submit, but ``input()`` does not.  This test
    guards the fallback contract.
    """
    from tinydb._repl_io import FallbackReplIO

    io = FallbackReplIO(":memory:", tmp_path / "h")
    io.add_history("CREATE TABLE x(id INT);")
    io.add_history("   ")
    io.add_history("SELECT 1;")
    # Whitespace-only entries are filtered (the original contract).
    assert list(io.history) == [
        "CREATE TABLE x(id INT);",
        "SELECT 1;",
    ]
