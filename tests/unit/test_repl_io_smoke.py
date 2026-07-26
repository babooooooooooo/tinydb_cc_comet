"""Smoke test for `_repl_io` soft-import placeholder.

This test guards the soft-import contract documented in
``cli-enhancements/design.md`` Requirement "REPL degrades gracefully when
prompt_toolkit unavailable" (and the design doc Module Spec).

The full ``ReplIO`` implementation lands in Task 2; this file only verifies:

1. The placeholder module exists and is importable.
2. ``_HAS_PROMPT_TOOLKIT`` is a boolean (either True or False), reflecting
   whether the optional ``prompt_toolkit`` dependency is present.
3. When ``_HAS_PROMPT_TOOLKIT`` is True, both ``prompt_toolkit.PromptSession``
   and ``pygments.lexers.sql.SqlLexer`` are reachable via the placeholder
   (i.e. the try/except ImportError block succeeded).
"""
from __future__ import annotations

import pytest

import tinydb._repl_io as _repl_io


def test_repl_io_has_prompt_toolkit_flag() -> None:
    """``_HAS_PROMPT_TOOLKIT`` must be a boolean flag."""
    assert isinstance(_repl_io._HAS_PROMPT_TOOLKIT, bool)
    assert _repl_io._HAS_PROMPT_TOOLKIT in (True, False)


@pytest.mark.skipif(
    not getattr(_repl_io, "_HAS_PROMPT_TOOLKIT", False),
    reason="prompt_toolkit not installed; soft-import fell back to False",
)
def test_repl_io_prompt_toolkit_actually_importable() -> None:
    """When ``_HAS_PROMPT_TOOLKIT`` is True the dependencies are usable."""
    import pygments.lexers.sql as _pyg_sql
    import prompt_toolkit as _ptk

    assert _ptk.PromptSession is not None
    assert _pyg_sql.SqlLexer is not None


@pytest.mark.unit
def test_prompt_toolkit_and_pygments_importable() -> None:
    """cli-enhancements 依赖必须可解析 (plan Step 1.1 unconditional happy-path).

    All six optional REPL dependencies must resolve under the active
    ``.venv``. This test fails loudly if any of the six submodules is
    missing or shadowed, catching dep-regressions at Task 1 RED-step time
    rather than at REPL runtime.
    """
    import pygments  # noqa: F401
    import pygments.lexers.sql  # noqa: F401
    import prompt_toolkit  # noqa: F401
    import prompt_toolkit.history  # noqa: F401
    import prompt_toolkit.lexers  # noqa: F401
    import prompt_toolkit.auto_suggest  # noqa: F401


@pytest.mark.unit
def test_repl_io_module_imports_soft_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_repl_io`` 顶层 try/except 允许 prompt_toolkit/pygments 缺失.

    This test is the only automated coverage for REQ-FALLBACK in Task 1:
    monkeypatch ``sys.modules`` to ``None`` for all four optional packages,
    reload ``tinydb._repl_io``, and assert ``_HAS_PROMPT_TOOLKIT`` is False.
    Without this test the ``# pragma: no cover - exercised by fallback
    test in Task 6`` branch in ``src/tinydb/_repl_io.py`` would remain
    uncovered in Task 1's deliverable.
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "prompt_toolkit", None)
    monkeypatch.setitem(sys.modules, "pygments", None)
    monkeypatch.setitem(sys.modules, "pygments.lexers", None)
    monkeypatch.setitem(sys.modules, "pygments.lexers.sql", None)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.history", None)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.lexers", None)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.auto_suggest", None)
    if "tinydb._repl_io" in sys.modules:
        del sys.modules["tinydb._repl_io"]
    import tinydb._repl_io as io_mod  # noqa: F401
    assert io_mod._HAS_PROMPT_TOOLKIT is False
    # Touch importlib to keep the reference honest if a future maintainer
    # re-adds ``importlib.reload(io_mod)`` per plan Step 1.1.
    assert importlib is not None
