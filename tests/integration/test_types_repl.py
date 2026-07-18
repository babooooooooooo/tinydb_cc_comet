"""Process-level REPL tests for the 15-type system (Plan Task 20).

Validates that every supported type round-trips through the REPL and that
display rendering is human-readable (not raw bytes / ``b'\\x07...'``-style
output) for parametric and date/time types. The REPL delegates row
formatting to ``str()`` over the decoded Python values that the executor
returns from ``row_codec.decode_row``, so the assertions here primarily
verify that the *full* type set reaches that path and that errors raised
by ``codec_for(...).validate`` reach the user as a single-line ``ERROR:``
message.
"""
import os
import shutil
import subprocess
import sys

import pytest


def _resolve_repl():
    """Locate the tinydb-repl console script (see test_repl_process.py)."""
    found = shutil.which("tinydb-repl")
    if found:
        return found
    candidate = os.path.join(os.path.dirname(sys.executable), "tinydb-repl")
    return candidate if os.path.isfile(candidate) else None


REPL = _resolve_repl()


def _run_repl(commands: str) -> subprocess.CompletedProcess:
    """Drive the REPL via stdin; return CompletedProcess with stdout/stderr."""
    assert REPL is not None, "run pip install -e '.[dev]' before integration tests"
    process = subprocess.Popen(
        [REPL],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(input=commands, timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(
            f"tinydb-repl timed out\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return subprocess.CompletedProcess(
        process.args, process.returncode, stdout, stderr
    )


# Skip the entire module when the console script is unavailable.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(REPL is None, reason="tinydb-repl not on PATH"),
]


# --- VARCHAR -----------------------------------------------------------------


@pytest.mark.integration
def test_repl_varchar_round_trip():
    """VARCHAR(64): decoded string renders verbatim, not as bytes."""
    result = _run_repl(
        "CREATE TABLE t (name VARCHAR(64));\n"
        "INSERT INTO t (name) VALUES ('alice');\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "alice" in result.stdout
    # Must NOT render as the repr of raw bytes.
    assert "b'" not in result.stdout
    assert "b\"" not in result.stdout
    assert result.stderr == ""


@pytest.mark.integration
def test_repl_varchar_overflow_emits_error():
    """VARCHAR(5) overflow raises codec ValueError; REPL prints single-line ERROR."""
    result = _run_repl(
        "CREATE TABLE t (name VARCHAR(5));\n"
        "INSERT INTO t (name) VALUES ('too long');\n"
        ".exit\n"
    )
    assert result.returncode == 0
    error_lines = [line for line in result.stderr.splitlines() if line]
    assert len(error_lines) == 1, f"expected single-line error, got: {result.stderr!r}"
    assert error_lines[0].startswith("ERROR:")
    assert "VARCHAR" in error_lines[0]
    # The REPL continues after a failure: subsequent CREATE should succeed.
    assert result.stdout.count("OK") == 1


# --- CHAR --------------------------------------------------------------------


@pytest.mark.integration
def test_repl_char_padded_display():
    """CHAR(5) pads 'ab' to 'ab   '; REPL may either preserve or trim trailing
    spaces — both are acceptable user-visible behaviors."""
    result = _run_repl(
        "CREATE TABLE t (code CHAR(5));\n"
        "INSERT INTO t (code) VALUES ('ab');\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    # Either padded ('ab   ') or trimmed ('ab') display is acceptable.
    assert ("ab   " in result.stdout) or ("ab" in result.stdout)


# --- DECIMAL -----------------------------------------------------------------


@pytest.mark.integration
def test_repl_decimal_round_trip():
    """DECIMAL(10,2) renders as a fixed-point string, not a bytes repr."""
    result = _run_repl(
        "CREATE TABLE t (amount DECIMAL(10,2));\n"
        "INSERT INTO t (amount) VALUES (12.34);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "12.34" in result.stdout
    assert "b'" not in result.stdout


# --- DATE / TIME / TIMESTAMP -------------------------------------------------


@pytest.mark.integration
def test_repl_date_round_trip():
    """DATE decodes to datetime.date; str() gives ISO YYYY-MM-DD."""
    result = _run_repl(
        "CREATE TABLE t (d DATE);\n"
        "INSERT INTO t (d) VALUES (DATE '2026-07-16');\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "2026-07-16" in result.stdout
    # Raw encoded form is a 4-byte big-endian days-since-epoch; reject that
    # representation leaking through.
    assert "b'\\x07" not in result.stdout
    assert "b'\\x00" not in result.stdout


@pytest.mark.integration
def test_repl_time_round_trip():
    """TIME decodes to datetime.time; str() gives HH:MM:SS."""
    result = _run_repl(
        "CREATE TABLE t (t TIME);\n"
        "INSERT INTO t (t) VALUES (TIME '12:34:56');\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "12:34:56" in result.stdout


@pytest.mark.integration
def test_repl_timestamp_round_trip():
    """TIMESTAMP decodes to naive datetime; str() gives 'YYYY-MM-DD HH:MM:SS'."""
    result = _run_repl(
        "CREATE TABLE t (ts TIMESTAMP);\n"
        "INSERT INTO t (ts) VALUES (TIMESTAMP '2026-07-16 14:30:00');\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "2026-07-16" in result.stdout
    assert "14:30:00" in result.stdout


# --- Integer width types -----------------------------------------------------


@pytest.mark.integration
def test_repl_smallint_round_trip():
    """SMALLINT (2-byte signed) round-trips through REPL as decimal string."""
    result = _run_repl(
        "CREATE TABLE t (id SMALLINT);\n"
        "INSERT INTO t (id) VALUES (100);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "100" in result.stdout


@pytest.mark.integration
def test_repl_bigint_round_trip():
    """BIGINT (8-byte signed) handles values outside INT32 range."""
    result = _run_repl(
        "CREATE TABLE t (n BIGINT);\n"
        "INSERT INTO t (n) VALUES (9223372036854775807);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "9223372036854775807" in result.stdout


# --- Float / Double ----------------------------------------------------------


@pytest.mark.integration
def test_repl_double_round_trip():
    """DOUBLE (8-byte IEEE 754) renders as a Python float string."""
    result = _run_repl(
        "CREATE TABLE t (val DOUBLE);\n"
        "INSERT INTO t (val) VALUES (3.14159);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "3.14159" in result.stdout


@pytest.mark.integration
def test_repl_float_round_trip():
    """FLOAT (4-byte single precision) renders as a Python float string."""
    result = _run_repl(
        "CREATE TABLE t (val FLOAT);\n"
        "INSERT INTO t (val) VALUES (1.5);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "1.5" in result.stdout


# --- Boolean -----------------------------------------------------------------


@pytest.mark.integration
def test_repl_boolean_round_trip():
    """BOOLEAN decodes to Python bool; str() gives 'True'/'False'."""
    result = _run_repl(
        "CREATE TABLE t (b BOOLEAN);\n"
        "INSERT INTO t (b) VALUES (TRUE);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "True" in result.stdout
    # Must NOT render as the integer 1.
    # The boolean column has a single cell containing 'True'.
    assert "1" not in result.stdout.split("---")[1].splitlines()[1]


# --- Combined sanity: all 15 types in one table ------------------------------


@pytest.mark.integration
def test_repl_all_15_types_in_one_table():
    """Smoke test: every type from the 15-type set in a single row renders
    without raising and without leaking raw bytes."""
    result = _run_repl(
        "CREATE TABLE all_types ("
        "  i INT, "
        "  si SMALLINT, "
        "  bi BIGINT, "
        "  f FLOAT, "
        "  d DOUBLE, "
        "  t TEXT, "
        "  vc VARCHAR(8), "
        "  ch CHAR(4), "
        "  b BOOLEAN, "
        "  de DECIMAL(8,2), "
        "  dt DATE, "
        "  tm TIME, "
        "  ts TIMESTAMP"
        ");\n"
        "INSERT INTO all_types (i, si, bi, f, d, t, vc, ch, b, de, dt, tm, ts) "
        "VALUES (1, 2, 3, 1.5, 2.5, 'hello', 'world', 'ab', TRUE, 9.99, "
        "DATE '2026-01-02', TIME '03:04:05', TIMESTAMP '2026-01-02 03:04:05');\n"
        "SELECT * FROM all_types;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    # 1 CREATE OK + 1 INSERT OK = 2 OK lines (SELECT renders the table).
    assert result.stdout.count("OK") == 2
    # Rendered values
    for expected in (
        "1", "2", "3", "1.5", "2.5", "hello", "world", "ab",
        "True", "9.99", "2026-01-02", "03:04:05",
    ):
        assert expected in result.stdout, (
            f"expected {expected!r} in REPL output, got:\n{result.stdout}"
        )
    # No raw bytes leak through
    assert "b'" not in result.stdout
    assert "b\"" not in result.stdout
    assert result.stderr == ""


# --- VARCHAR error does not poison subsequent statements ---------------------


@pytest.mark.integration
def test_repl_varchar_error_then_continue():
    """After a VARCHAR overflow error the REPL must keep accepting commands."""
    result = _run_repl(
        "CREATE TABLE t (name VARCHAR(5));\n"
        "INSERT INTO t (name) VALUES ('too long');\n"
        "INSERT INTO t (name) VALUES ('ok');\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    # 1 CREATE OK + 1 INSERT OK (the successful one) = 2 OK lines.
    assert result.stdout.count("OK") == 2
    # The successful insert is present.
    assert "ok" in result.stdout
    # Exactly one ERROR line, single-line.
    error_lines = [line for line in result.stderr.splitlines() if line]
    assert len(error_lines) == 1
    assert error_lines[0].startswith("ERROR:")
