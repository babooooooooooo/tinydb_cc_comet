"""Unit tests for tinydb._repl_io (Task 2).

Covers _color_enabled, _is_unterminated moved from repl.py, plus
_HAS_PROMPT_TOOLKIT module-level detection and both I/O implementations.
"""
import builtins
import importlib
import sys
from pathlib import Path

import pytest


@pytest.mark.unit
def test_color_enabled_true_when_no_env_no_dumb_term(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    from tinydb._repl_io import _color_enabled

    assert _color_enabled() is True


@pytest.mark.unit
def test_color_disabled_when_no_color_set(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    from tinydb._repl_io import _color_enabled

    assert _color_enabled() is False


@pytest.mark.unit
def test_color_disabled_when_term_dumb(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    from tinydb._repl_io import _color_enabled

    assert _color_enabled() is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("buf", "expected"),
    [
        ("SELECT 1;", False),
        ("INSERT INTO t(id) VALUES (", True),
        ("INSERT INTO t(name) VALUES ('alice", True),
        ("INSERT INTO t(name) VALUES ('o''brien');", False),
        ("SELECT 1 -- ( ignored\n", False),
        ("SELECT 1 /* unterminated", True),
        ("-- leading comment\nSELECT 1;", False),
        ("SELECT 'foo' /* done */", False),
        ('SELECT "a""b";', False),
        ('SELECT "unterminated', True),
    ],
)
def test_is_unterminated_matches_repl_behavior(buf, expected):
    """The exact migration of repl._is_unterminated; same semantics."""
    from tinydb._repl_io import _is_unterminated

    assert _is_unterminated(buf) is expected


@pytest.mark.unit
def test_has_prompt_toolkit_flag_is_bool():
    from tinydb._repl_io import _HAS_PROMPT_TOOLKIT

    assert isinstance(_HAS_PROMPT_TOOLKIT, bool)


@pytest.mark.unit
def test_repl_io_reimportable_without_prompt_toolkit(monkeypatch):
    """Module remains importable when optional dependencies are unavailable."""
    with monkeypatch.context() as mocked:
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
            mocked.setitem(sys.modules, name, None)
        mod = importlib.import_module("tinydb._repl_io")
        assert mod._HAS_PROMPT_TOOLKIT is False

    sys.modules.pop("tinydb._repl_io", None)
    importlib.import_module("tinydb._repl_io")


@pytest.mark.unit
def test_prompt_toolkit_replio_raises_when_disabled(monkeypatch, tmp_path):
    """PromptToolkitReplIO cannot construct without its optional dependency."""
    import tinydb._repl_io as io_mod

    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", False)
    with pytest.raises(RuntimeError):
        io_mod.PromptToolkitReplIO(":memory:", tmp_path / "h", True)


@pytest.mark.unit
def test_prompt_toolkit_replio_read_returns_text(monkeypatch, tmp_path):
    """read_statement passes through prompt_toolkit's returned text."""
    import tinydb._repl_io as io_mod

    class FakeHistory:
        def __init__(self):
            self.calls = []

        def append_string(self, text):
            self.calls.append(text)

    class FakeSession:
        def __init__(self, **kw):
            self.history = FakeHistory()

        def prompt(self, prompt, multiline=False):
            return "SELECT 1;"

    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(io_mod, "PromptSession", FakeSession)
    monkeypatch.setattr(io_mod, "FileHistory", lambda p: None)
    monkeypatch.setattr(io_mod, "AutoSuggestFromHistory", lambda: None)
    monkeypatch.setattr(io_mod, "PygmentsLexer", lambda l: None)
    monkeypatch.setattr(io_mod, "SqlLexer", object())

    io = io_mod.PromptToolkitReplIO(":memory:", tmp_path / "h", False)
    assert io.read_statement() == "SELECT 1;"
    # add_history is a no-op on PromptToolkitReplIO (Round 1 review fix):
    # Buffer.validate_and_handle() already appends to the session's
    # history on submit, so the loop must not double-write.
    io.add_history("SELECT 1;")
    io.add_history("   ")
    assert io._session.history.calls == []


@pytest.mark.unit
def test_prompt_toolkit_replio_eof_maps_to_none(monkeypatch, tmp_path):
    """read_statement maps prompt_toolkit EOFError to None."""
    import tinydb._repl_io as io_mod

    class FakeHistory:
        def append_string(self, text):
            pass

    class FakeSession:
        def __init__(self, **kw):
            self.history = FakeHistory()

        def prompt(self, prompt, multiline=False):
            raise EOFError

    monkeypatch.setattr(io_mod, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(io_mod, "PromptSession", FakeSession)
    monkeypatch.setattr(io_mod, "FileHistory", lambda p: None)
    monkeypatch.setattr(io_mod, "AutoSuggestFromHistory", lambda: None)

    io = io_mod.PromptToolkitReplIO(":memory:", tmp_path / "h", False)
    assert io.read_statement() is None


@pytest.mark.unit
def test_fallback_replio_eof_returns_none(monkeypatch):
    monkeypatch.setattr(
        builtins, "input", lambda prompt: (_ for _ in ()).throw(EOFError)
    )
    from tinydb._repl_io import FallbackReplIO

    io = FallbackReplIO(":memory:", Path("/tmp/none"))
    assert io.read_statement() is None


@pytest.mark.unit
def test_fallback_replio_keyboard_interrupt_clears_buf(monkeypatch, capsys):
    def fake_input(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", fake_input)
    from tinydb._repl_io import FallbackReplIO

    io = FallbackReplIO(":memory:", Path("/tmp/none"))
    io._buf = "SELECT * FROM "
    assert io.read_statement() == ""
    assert io._buf == ""
    captured = capsys.readouterr()
    assert "(Use .exit" in captured.out


@pytest.mark.unit
def test_fallback_replio_saves_history_in_memory():
    from tinydb._repl_io import FallbackReplIO

    io = FallbackReplIO(":memory:", Path("/tmp/none"))
    io.add_history("SELECT 1;")
    io.add_history("")
    assert list(io.history) == ["SELECT 1;"]


@pytest.mark.unit
def test_fallback_replio_accumulates_until_terminator(monkeypatch):
    responses = iter(["SELECT * FROM t", " WHERE id =", " 1;"])

    def fake_input(prompt):
        try:
            return next(responses)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    from tinydb._repl_io import FallbackReplIO

    io = FallbackReplIO(":memory:", Path("/tmp/none"))
    assert io.read_statement() == ""
    assert io.read_statement() == ""
    assert io.read_statement() == "SELECT * FROM t\n WHERE id =\n 1;"
