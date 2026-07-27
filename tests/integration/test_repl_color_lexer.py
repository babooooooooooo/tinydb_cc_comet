"""End-to-end tests for PromptToolkitReplIO.set_color (Round 1 review).

After the Round 1 review fix, ``.color on|off`` rebuilds the session's
lexer by instantiating a new ``PromptSession`` while preserving the
existing ``FileHistory``.  These tests assert:

* ``set_color`` round-trips without losing history.
* The state flag flips both before and after calling ``set_color``.
* The loop routes the IO handle to ``_cmd_color`` so the rebuild is
  actually triggered.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tinydb._repl_io as io_mod
from tinydb._repl_io import PromptToolkitReplIO
from tinydb._repl_meta import ReplState, _cmd_color
from tinydb.database import Database
from tinydb.repl import _interactive_loop


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Plumbing: capture PygmentsLexer arguments across rebuilds.
# ---------------------------------------------------------------------------


class _FakeHistory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def append_string(self, text: str) -> None:
        self.calls.append(text)


class _FakeSession:
    """Records which lexer was passed at construction time."""

    instances: list = []  # class-level: every session ever built
    lexer_calls: list = []  # class-level: every (enabled, lexer) pair

    def __init__(self, **kwargs) -> None:
        # ``set_color`` rebuilds the session with an explicit ``history``
        # kwarg so the up-arrow history persists.  When the kwarg is
        # absent (initial construction), build a fresh history.
        self.history = kwargs.pop("history", None) or _FakeHistory()
        self._prompts = kwargs.pop("_prompts_queue", [])
        self.kwargs = kwargs
        # ``Buffer.validate_and_handle``-equivalent side effect.
        if self._prompts:
            value = self._prompts[0]
            if value and value.strip():
                self.history.append_string(value)
        _FakeSession.instances.append(self)
        _FakeSession.lexer_calls.append(kwargs.get("lexer"))

    def prompt(self, prompt, multiline=False):  # noqa: ARG002
        if not self._prompts:
            raise EOFError
        value = self._prompts.pop(0)
        if value is None:
            raise EOFError
        return value


@pytest.fixture(autouse=True)
def _reset_fake_session():
    """Ensure each test starts with a clean _FakeSession registry."""
    _FakeSession.instances = []
    _FakeSession.lexer_calls = []
    yield
    _FakeSession.instances = []
    _FakeSession.lexer_calls = []


def _patch(monkeypatch, prompts: list | None = None) -> None:
    """Wire monkey-patches so PromptToolkitReplIO builds _FakeSessions.

    ``prompts`` is the queue of values to feed to the loop.  Each
    subsequent session created by ``set_color`` continues to consume
    from the same queue, so rebuilds do not replay already-consumed
    inputs.
    """
    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(io_mod, "FileHistory", lambda p: _FakeHistory())
    monkeypatch.setattr(io_mod, "AutoSuggestFromHistory", lambda: None)
    monkeypatch.setattr(
        io_mod, "PygmentsLexer", lambda l: f"PYG[{l.__name__ if hasattr(l, '__name__') else l}]"
    )
    monkeypatch.setattr(io_mod, "SqlLexer", type("SqlLexer", (), {"__name__": "SqlLexer"}))
    queue: list = list(prompts) if prompts is not None else []

    def _factory(**kwargs):
        return _FakeSession(_prompts_queue=queue, **kwargs)

    monkeypatch.setattr(io_mod, "PromptSession", _factory)


# ---------------------------------------------------------------------------
# 1. set_color round-trip preserves history instance.
# ---------------------------------------------------------------------------


def test_set_color_round_trip_preserves_history(monkeypatch, tmp_path):
    """``set_color`` rebuilds the session but keeps the same history."""
    _patch(monkeypatch)
    io = PromptToolkitReplIO(":memory:", tmp_path / "h", False)
    original_history = io._session.history
    # Before the rebuild, the first session was constructed with no lexer.
    assert _FakeSession.lexer_calls[0] is None
    io.set_color(True)
    # After the rebuild, the new session still has the same history.
    assert io._session.history is original_history
    # The rebuild was issued with a non-None lexer.
    assert _FakeSession.lexer_calls[-1] is not None
    # And the rebuild replaced the session object.
    assert io._session is not _FakeSession.instances[0]
    assert io._session is _FakeSession.instances[-1]


def test_set_color_off_after_on(monkeypatch, tmp_path):
    """Toggling color on then off flips the lexer argument both ways."""
    _patch(monkeypatch)
    io = PromptToolkitReplIO(":memory:", tmp_path / "h", True)
    # Initial build with color=True → lexer is not None.
    assert _FakeSession.lexer_calls[0] is not None
    io.set_color(False)
    # Rebuild with color=False → lexer is None.
    assert _FakeSession.lexer_calls[-1] is None
    io.set_color(True)
    # Rebuild again with color=True → lexer is not None.
    assert _FakeSession.lexer_calls[-1] is not None
    # Three sessions total: initial + two rebuilds.
    assert len(_FakeSession.instances) == 3


# ---------------------------------------------------------------------------
# 2. _cmd_color with an IO handle triggers set_color on the real adapter.
# ---------------------------------------------------------------------------


def test_cmd_color_invokes_io_set_color(monkeypatch, tmp_path):
    """``_cmd_color(['on'], db, state, io)`` calls ``io.set_color(True)``."""
    _patch(monkeypatch)
    io = PromptToolkitReplIO(":memory:", tmp_path / "h", False)
    state = ReplState()
    # Spy on set_color: replace the bound method to capture the call.
    calls: list[bool] = []

    def spy_set_color(enabled: bool) -> None:
        calls.append(enabled)
        # Call the real one to keep the rebuild behaviour intact.
        PromptToolkitReplIO.set_color(io, enabled)

    io.set_color = spy_set_color  # type: ignore[assignment]
    rc = _cmd_color(["on"], Database(":memory:"), state, io)
    assert rc is True
    assert calls == [True]
    assert state.color_enabled is True
    # And the rebuild happened.
    assert _FakeSession.lexer_calls[-1] is not None


# ---------------------------------------------------------------------------
# 3. _interactive_loop forwards IO to handle_meta so the rebuild fires.
# ---------------------------------------------------------------------------


def test_interactive_loop_color_rebuilds_session(monkeypatch, tmp_path, capsys):
    """``.color on` in the loop triggers one session rebuild."""
    _patch(monkeypatch, prompts=[".color on"])
    io = PromptToolkitReplIO(":memory:", tmp_path / "h", False)
    with Database(":memory:") as db:
        rc = _interactive_loop(
            db,
            io,
            ReplState(),
        )
    assert rc == 0
    out = capsys.readouterr().out
    # The canonical status lines are printed.
    assert "Color: on" in out
    # Exactly one rebuild on top of the initial session.
    assert len(_FakeSession.instances) == 2
    # The rebuild was issued with a non-None lexer.
    assert _FakeSession.lexer_calls[-1] is not None
