"""Process-level tests for REPL rendering of ConstraintViolation (Plan Task 14)."""
import shutil
import subprocess

import pytest


REPL = shutil.which("tinydb-repl")


def run_repl(commands: str, *args: str) -> subprocess.CompletedProcess[str]:
    assert REPL is not None, "run pip install -e '.[dev]' before integration tests"
    process = subprocess.Popen(
        [REPL, *args],
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
        pytest.fail(f"timed out\nstdout:\n{stdout}\nstderr:\n{stderr}")
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


@pytest.mark.integration
def test_repl_constraint_violation_renders_kind_null():
    result = run_repl(
        "CREATE TABLE t(id INT NOT NULL);\n"
        "INSERT INTO t(id) VALUES (NULL);\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "ERROR: ConstraintViolation(kind='null', column='id', value=None)" in result.stderr


@pytest.mark.integration
def test_repl_constraint_violation_renders_kind_unique():
    result = run_repl(
        "CREATE TABLE t(id INT, email TEXT UNIQUE);\n"
        "INSERT INTO t(id, email) VALUES (1, 'a@x');\n"
        "INSERT INTO t(id, email) VALUES (2, 'a@x');\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "ERROR: ConstraintViolation(kind='unique'" in result.stderr
    assert "columns=['email']" in result.stderr


@pytest.mark.integration
def test_repl_constraint_violation_renders_kind_duplicate_pk():
    result = run_repl(
        "CREATE TABLE t(id INT PRIMARY KEY, name TEXT);\n"
        "INSERT INTO t(id, name) VALUES (1, 'a');\n"
        "INSERT INTO t(id, name) VALUES (1, 'b');\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "ERROR: ConstraintViolation(kind='duplicate_pk'" in result.stderr
    assert "columns=['id']" in result.stderr


@pytest.mark.integration
def test_repl_loop_continues_after_constraint_violation():
    result = run_repl(
        "CREATE TABLE t(id INT NOT NULL);\n"
        "INSERT INTO t(id) VALUES (NULL);\n"
        "CREATE TABLE ok(id INT);\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert "OK" in result.stdout
    assert "ERROR: ConstraintViolation" in result.stderr