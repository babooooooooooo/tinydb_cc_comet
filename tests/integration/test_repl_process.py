"""Process-level tests for the installed tinydb-repl console script."""
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
        pytest.fail(
            f"tinydb-repl timed out\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


@pytest.mark.integration
def test_repl_basic_crud():
    result = run_repl(
        "CREATE TABLE t(id INT);\n"
        "INSERT INTO t(id) VALUES (1);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "id" in result.stdout and "1" in result.stdout
    assert result.stderr == ""


@pytest.mark.integration
def test_repl_select_no_rows():
    result = run_repl("CREATE TABLE t(id INT);\nSELECT * FROM t;\n.exit\n")
    assert result.returncode == 0
    assert "(no rows)" in result.stdout


@pytest.mark.integration
def test_repl_tables_meta():
    result = run_repl(
        "CREATE TABLE users(id INT);\n"
        "CREATE TABLE orders(id INT);\n"
        ".tables\n.exit\n"
    )
    assert result.returncode == 0
    assert "users" in result.stdout and "orders" in result.stdout


@pytest.mark.integration
def test_repl_schema_meta():
    result = run_repl("CREATE TABLE users(id INT, name TEXT);\n.schema users\n.exit\n")
    assert result.returncode == 0
    assert "CREATE TABLE users(id INT, name TEXT);" in result.stdout


@pytest.mark.integration
def test_repl_read_executes_each_same_line_statement(tmp_path):
    script = tmp_path / "seed.sql"
    script.write_text(
        "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1);",
        encoding="utf-8",
    )
    result = run_repl(f".read {script}\nSELECT * FROM t;\n.exit\n")
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "1" in result.stdout


@pytest.mark.integration
@pytest.mark.parametrize("command", [".exit", ".quit"])
def test_repl_meta_exit_returns_zero(command):
    result = run_repl(command + "\n")
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


@pytest.mark.integration
def test_repl_eof_returns_zero():
    result = run_repl("")
    assert result.returncode == 0


@pytest.mark.integration
def test_repl_multiline_insert():
    result = run_repl(
        "CREATE TABLE t(id INT, name TEXT);\n"
        "INSERT INTO t(id, name) VALUES (\n"
        "1, 'alice');\n"
        "SELECT * FROM t;\n.exit\n"
    )
    assert result.returncode == 0
    assert "...> " in result.stdout
    assert "alice" in result.stdout


@pytest.mark.integration
def test_repl_error_is_single_line_and_loop_continues():
    result = run_repl("SELECT FROM;\nCREATE TABLE ok(id INT);\n.exit\n")
    assert result.returncode == 0
    error_lines = [line for line in result.stderr.splitlines() if line]
    assert len(error_lines) == 1
    assert error_lines[0].startswith("ERROR: ParseError:")
    assert "OK" in result.stdout


@pytest.mark.integration
def test_repl_database_flag_persists(tmp_path):
    database = tmp_path / "persist.db"
    first = run_repl(
        "CREATE TABLE t(id INT);\nINSERT INTO t(id) VALUES (7);\n.exit\n",
        "--database",
        str(database),
    )
    second = run_repl(
        "SELECT * FROM t;\n.exit\n",
        "--database",
        str(database),
    )
    assert first.returncode == 0
    assert second.returncode == 0
    assert database.exists()
    assert "7" in second.stdout
