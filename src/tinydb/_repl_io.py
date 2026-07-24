"""REPL I/O layer for ``tinydb-repl`` (placeholder).

This module is the soft-import boundary for the optional interactive
dependencies used by the upgraded REPL:

* ``pygments`` — SQL syntax highlighting via :class:`pygments.lexers.sql.SqlLexer`.
* ``prompt_toolkit`` — multi-line editing, history persistence, and key bindings
  via :class:`prompt_toolkit.PromptSession` + ``PygmentsLexer`` + ``FileHistory``.

The full implementations of :class:`PromptToolkitReplIO` and
:class:`FallbackReplIO` (Task 2), along with ``ReplIOProtocol``, are intentionally
deferred; this placeholder only exposes the module-level
``_HAS_PROMPT_TOOLKIT`` flag so that the rest of the REPL machinery can detect
the availability of the optional dependencies without triggering an
``ImportError`` at import time.

The soft-import pattern follows the existing ``_HAS_FCNTL`` precedent in
``tinydb/repl.py``: anything we cannot import from ``prompt_toolkit`` /
``pygments`` must collapse to a plain ``False`` here and to a stdlib-only
fallback path inside ``repl.py``.
"""
from __future__ import annotations

try:
    from prompt_toolkit import PromptSession  # noqa: F401  (re-exported by Task 2)
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory  # noqa: F401
    from prompt_toolkit.history import FileHistory  # noqa: F401
    from prompt_toolkit.lexers import PygmentsLexer  # noqa: F401
    from pygments.lexers.sql import SqlLexer  # noqa: F401

    _HAS_PROMPT_TOOLKIT: bool = True
except ImportError:  # pragma: no cover - exercised by fallback test in Task 6
    _HAS_PROMPT_TOOLKIT: bool = False


__all__ = ["_HAS_PROMPT_TOOLKIT"]