"""Integration suite for the parser <-> executor <-> storage roundtrip (Task 25).

Locks in §8.2 of the design doc: a single SQL script flows through
``tokenize`` -> ``parse`` -> ``Executor`` -> ``Pager``/``SlottedPage`` ->
``Database.execute`` and the final SELECT result matches the script's
expected row count.

Coverage:
* Parametrized full-pipeline case for a single INT column (one row).
* Parametrized full-pipeline case for a two-column INT/TEXT table with a
  WHERE-filtered SELECT that must return exactly one row.
* Parser purity / no-state-leak: tokenize+parse the same CREATE TABLE input
  twice and assert equivalent AST fields (statement names + full dataclass
  equality on ``CreateTable`` — avoids relying on mutable identity).

These tests open a real file-backed ``Pager`` via ``tmp_path`` and drive
SQL through the public ``Database`` context manager, so they live under
``tests/integration/`` and carry the ``integration`` pytest marker (which is
already registered in ``pyproject.toml``).

Test-side correction vs. plan template: the plan template shows the bare
``INSERT INTO t VALUES (...)`` form, but the MVP parser requires an explicit
column list (see ``parser.py::_parse_insert`` — columns are mandatory and
``INSERT`` without them raises ``ParseError``). The tests below use the
parser's supported form: ``INSERT INTO t(id) VALUES (...)`` and
``INSERT INTO t(a, b) VALUES (...)``. Production parser is intentionally NOT
weakened to match the plan template — the template was a sketch and the
explicit-column contract is the spec.
"""
from dataclasses import asdict

import pytest

from tinydb import Database
from tinydb.parser import CreateTable, parse
from tinydb.tokenizer import tokenize


# --- Full-pipeline parametrized cases ---------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "sql, expected_rows",
    [
        # Single-column table: CREATE + INSERT + SELECT * must yield one row.
        (
            "CREATE TABLE t(id INT); "
            "INSERT INTO t(id) VALUES (1); "
            "SELECT * FROM t",
            1,
        ),
        # Two-column table, two INSERTs, WHERE-filtered SELECT returning one row.
        (
            "CREATE TABLE t(a INT, b TEXT); "
            "INSERT INTO t(a, b) VALUES (1, 'x'); "
            "INSERT INTO t(a, b) VALUES (2, 'y'); "
            "SELECT * FROM t WHERE a = 2",
            1,
        ),
    ],
    ids=["single-column", "two-column-where"],
)
def test_full_pipeline_roundtrip(tmp_path, sql, expected_rows):
    """End-to-end: tokenize -> parse -> executor -> storage returns expected rows."""
    with Database(str(tmp_path / "rt.db")) as db:
        rows = db.execute(sql)
    assert len(rows) == expected_rows


# --- Parser purity / no-state-leak ------------------------------------------


@pytest.mark.integration
def test_parser_is_pure_no_state_leak():
    """Parsing the same CREATE TABLE input twice yields equivalent AST nodes.

    Asserts at minimum the statement names match (catches obvious regressions
    where parsing the first call consumes or mutates the second call's input).
    Goes further by comparing the full ``CreateTable`` AST via dataclass
    field equality, which avoids relying on mutable identity — even if a
    future change makes the dataclass non-frozen or wraps fields in lists,
    ``asdict`` deep-compares the public field contents.
    """
    sql = "CREATE TABLE t(id INT)"
    first = parse(tokenize(sql)).statements[0]
    second = parse(tokenize(sql)).statements[0]

    # Minimum contract: statement names agree.
    assert first.name == second.name

    # Prefer full AST equality that does not rely on mutable identity.
    assert isinstance(first, CreateTable)
    assert isinstance(second, CreateTable)
    assert asdict(first) == asdict(second)