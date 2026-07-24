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