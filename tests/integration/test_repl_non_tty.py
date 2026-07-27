"""End-to-end tests for non-TTY stdin handling (Round 1 review).

When stdin is a pipe / redirect, prompt_toolkit's ``PromptSession.prompt``
silently drops every line because it expects an interactive TTY.  The
Round 1 review fix is: ``main()`` detects ``not sys.stdin.isatty()``
before selecting an IO adapter, and forces the stdlib-based
``FallbackReplIO`` silently.  These tests assert that behavior by
driving the real ``tinydb.repl`` module in a subprocess with a piped
stdin.
"""
from __future__ import annotations

import subprocess
import sys

import pytest


pytestmark = pytest.mark.integration


def _run_repl_subprocess(input_text: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m tinydb.repl`` with a piped stdin.

    The Round 1 fix only matters when prompt_toolkit is *available*; if
    the optional dependency is missing the fallback is used anyway.
    Under our test environment, prompt_toolkit is installed in the venv
    so the fix's non-TTY branch is the one exercised.
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "tinydb.repl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(
            f"tinydb.repl timed out\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return subprocess.CompletedProcess(
        process.args, process.returncode, stdout, stderr
    )


# ---------------------------------------------------------------------------
# 1. Non-TTY stdin: SQL piped to the REPL renders rows.
# ---------------------------------------------------------------------------


def test_non_tty_stdin_executes_sql_and_renders_rows():
    """Piped stdin (non-TTY) is served by FallbackReplIO; SELECT renders rows.

    Before the Round 1 fix, prompt_toolkit's session would silently
    consume the piped input and the loop would never see it; the result
    was a REPL that appeared to hang and then exit 0 with no output.
    The fix forces FallbackReplIO when stdin is not a TTY, so SQL piped
    via ``echo ... | tinydb-repl`` works as expected.
    """
    result = _run_repl_subprocess(
        "CREATE TABLE t(id INT);\n"
        "INSERT INTO t(id) VALUES (1);\n"
        "INSERT INTO t(id) VALUES (2);\n"
        "SELECT * FROM t;\n"
    )
    assert result.returncode == 0, (
        f"unexpected returncode\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    # CREATE + 2 INSERTs each print "OK".
    assert result.stdout.count("OK") == 3
    # The SELECT renders rows.
    assert "id" in result.stdout
    assert "1" in result.stdout
    assert "2" in result.stdout


def test_non_tty_stdin_meta_command_routes_through_fallback():
    """``.exit` in a piped script reaches the meta dispatcher cleanly."""
    result = _run_repl_subprocess(".exit\n")
    assert result.returncode == 0
    # No traceback; the loop returns 0.
    assert "Traceback" not in result.stderr
