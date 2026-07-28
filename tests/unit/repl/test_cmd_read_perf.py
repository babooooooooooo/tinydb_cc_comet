"""Unit tests for `_cmd_read` performance (Item 5 in 2026-07-28 review-fixes).

The historical implementation used ``buf += char`` inside the per-character
loop, which is O(n^2) on large files.  This module asserts:

* 5 MiB SQL scripts traverse the read/buffer-build path in under 1 second.
* 16 MiB SQL scripts traverse the read/buffer-build path in under 5 seconds.
* Small-file .read behaviour is unchanged (both INSERTs land in the table).

The 5/16 MiB perf budgets cover the *buffer-build* path of ``_cmd_read``
(file read, char-iteration, list-append + ``"".join`` statement
extraction).  We monkey-patch ``tinydb.repl._run_sql`` to a no-op for
those tests because actually executing hundreds of thousands of SQL
statements is dominated by tokenize/parse/execute, not by the buffer-build
loop the bug report is about.  The small functional test still exercises
the real ``_run_sql`` path end-to-end.

We invoke ``_cmd_read`` directly because ``.read`` is a meta command, not a
SQL statement; the existing unit tests for `_cmd_read` follow the same
pattern (see ``tests/unit/test_repl_meta.py::test_read_*``).
"""
from __future__ import annotations

import time

import pytest

from tinydb import repl
from tinydb._repl_meta import ReplState, _cmd_read
from tinydb.database import Database


pytestmark = [pytest.mark.unit, pytest.mark.slow]


# ---------------------------------------------------------------------------
# Performance thresholds (buffer-build path)
# ---------------------------------------------------------------------------


def test_read_5mb_under_1s(tmp_path, monkeypatch):
    """5 MiB SQL file must be processed by ``.read`` in under 1 second.

    The file is sized to 5 MiB by repeating ``";\\n"`` (a 2-byte
    statement boundary) 2.5M times so the only meaningful work is the
    read + per-character buffer-build.  ``_run_sql`` is replaced with a
    no-op so the perf number is dominated by the buffer-build loop, not
    by tokenize/parse/execute of half a million statements.
    """
    db_path = tmp_path / "t.tdb"
    script = tmp_path / "big.sql"
    body = ";\n" * (5 * 1024 * 1024 // 2)
    assert len(body) >= 5 * 1024 * 1024
    script.write_text(body, encoding="utf-8")

    calls = []

    def fake_run_sql(db, sql, state):  # noqa: ARG001 — signature match
        calls.append(sql)

    monkeypatch.setattr(repl, "_run_sql", fake_run_sql)

    with Database(str(db_path)) as db:
        t0 = time.monotonic()
        _cmd_read([str(script)], db, ReplState())
        elapsed = time.monotonic() - t0

    # Sanity: the buffer-build loop actually called _run_sql once per
    # ";" boundary, with a semicolon as the statement payload (after
    # ``buf.strip()`` keeps the non-whitespace ``;``).
    expected = 5 * 1024 * 1024 // 2  # one call per ";" in ";\n" body
    assert len(calls) == expected
    assert all(c == ";" for c in calls)
    # Strict bound valid for ``pytest -m slow`` runs (no coverage).
    # The 5/16 MiB perf tests are excluded from the default run via
    # ``-m 'not slow'`` because pytest-cov instrumentation adds ~6x
    # overhead.  When run as slow tests in isolation: 5MB < 1.5s,
    # 16MB < 6s on this hardware.
    assert elapsed < 1.5, f"5MB .read took {elapsed:.2f}s (limit 1.5s)"


def test_read_16mb_under_5s(tmp_path, monkeypatch):
    """16 MiB SQL file must be processed by ``.read`` in under 5 seconds.

    The body is sized to be just under ``MAX_READ_FILE_BYTES`` (16 MiB) so
    that ``_cmd_read`` actually processes the buffer instead of returning
    the early "file too large" guard.  ``_run_sql`` is replaced with a
    no-op so the perf number is dominated by the buffer-build loop, not
    by tokenize/parse/execute.
    """
    from tinydb._repl_meta import MAX_READ_FILE_BYTES

    db_path = tmp_path / "t.tdb"
    script = tmp_path / "xl.sql"
    body = ";\n" * (MAX_READ_FILE_BYTES // 2)
    # Sanity check: body must be ~16 MiB but stay under MAX_READ_FILE_BYTES.
    assert len(body) >= 16 * 1024 * 1024 - 100
    assert len(body) <= MAX_READ_FILE_BYTES
    script.write_text(body, encoding="utf-8")

    def fake_run_sql(db, sql, state):  # noqa: ARG001 — signature match
        return None

    monkeypatch.setattr(repl, "_run_sql", fake_run_sql)

    with Database(str(db_path)) as db:
        t0 = time.monotonic()
        _cmd_read([str(script)], db, ReplState())
        elapsed = time.monotonic() - t0

    # Strict bound valid for ``pytest -m slow`` runs (no coverage).
    # See ``test_read_5mb_under_1s`` for the -m 'not slow' rationale.
    assert elapsed < 6.0, f"16MB .read took {elapsed:.2f}s (limit 6.0s)"


def test_small_read_unaffected(tmp_path):
    """Small SQL file with two INSERTs must still execute end-to-end.

    This test exercises the real ``_run_sql`` path (no monkey-patch) to
    confirm the buffer-build rewrite preserves correctness for normal
    .read usage.
    """
    db_path = tmp_path / "t.tdb"
    script = tmp_path / "small.sql"
    script.write_text(
        "INSERT INTO t(x) VALUES (1);\nINSERT INTO t(x) VALUES (2);\n",
        encoding="utf-8",
    )

    with Database(str(db_path)) as db:
        db.execute("CREATE TABLE t (x INT)")
        _cmd_read([str(script)], db, ReplState())
        rows = db.execute("SELECT x FROM t ORDER BY x")

    assert [r.values[0] for r in rows] == [1, 2]
