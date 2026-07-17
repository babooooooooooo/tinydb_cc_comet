"""E2E golden SQL test runner: byte-compares db.execute output to .expected.txt.

Discovers every ``*.sql`` file under ``tests/e2e/sql/`` (recursive),
executes the statements one at a time against a fresh temporary
``tinydb.Database``, and produces a deterministic text output for
each statement:

* Non-empty result  -> ``repr(Row(...))`` per row, one per line.
* Empty result      -> ``(no rows)``.
* Raised exception  -> ``ERROR: <ExceptionType>: <message>``.

The concatenated output is byte-compared against the matching
``.expected.txt`` next to the SQL file. The runner recognises an
optional ``-- REOPEN`` comment line: when seen, the Database is
closed and re-opened on the same path before the next statement
runs, so scenarios can exercise the close/reopen persistence path
without touching production code.
"""
import pathlib
import pytest
import tinydb
from tinydb.errors import ConstraintViolation

SQL_DIR = pathlib.Path(__file__).parent / "sql"

# Directive inside a SQL file. When a line that starts with this
# marker (after optional leading whitespace) is encountered, the
# conftest closes the current Database and re-opens it on the same
# path before running the next statement. The directive line itself
# is not executed as SQL.
_REOPEN_MARKER = "-- REOPEN"

# Internal sentinel used between :func:`_parse_source` and the
# fixture loop to mean "close + reopen the Database now". Kept
# visually distinct from any SQL keyword.
_REOPEN_DIRECTIVE = "__REOPEN__"


def pytest_generate_tests(metafunc):
    if "golden_sql" in metafunc.fixturenames:
        files = sorted(SQL_DIR.rglob("*.sql"))
        # ``indirect=True`` routes the parameter through the fixture
        # (via ``request.param``) instead of substituting the raw
        # path into the test, so the fixture can run the SQL and
        # yield ``(sql_path, actual, expected)`` for the test to
        # compare.
        metafunc.parametrize(
            "golden_sql", files, indirect=True,
            ids=lambda p: str(p.relative_to(SQL_DIR)),
        )


def _format_rows(rows):
    if not rows:
        return "(no rows)"
    return "\n".join(repr(r) for r in rows)


def _parse_source(raw: str) -> list[str]:
    """Turn a SQL source string into an ordered list of segments.

    Each segment is either a SQL statement (with surrounding
    whitespace stripped) or the sentinel ``_REOPEN_DIRECTIVE`` that
    tells the runner to close and re-open the Database before the
    next segment.

    The source is processed line by line so a ``-- REOPEN`` line
    never gets glued onto the previous SQL statement by the
    ``;``-split. The MVP tokenizer does not emit ``;`` from inside
    a text literal, so a naive ``split(";")`` on the surviving SQL
    text is safe for the supported grammar.
    """
    sql_lines: list[str] = []
    segments: list[str] = []
    for line in raw.splitlines():
        if line.strip() == _REOPEN_MARKER:
            # Flush any SQL accumulated so far as a single statement.
            sql_text = "\n".join(sql_lines).strip()
            if sql_text:
                segments.append(sql_text)
                sql_lines = []
            segments.append(_REOPEN_DIRECTIVE)
        else:
            sql_lines.append(line)
    sql_text = "\n".join(sql_lines).strip()
    if sql_text:
        segments.append(sql_text)
    return [s for s in segments if s]


def _is_reopen_directive(segment: str) -> bool:
    return segment == _REOPEN_DIRECTIVE


def _run_one(db: "tinydb.Database", stmt: str) -> str:
    try:
        rows = db.execute(stmt)
    except ConstraintViolation as exc:
        # ConstraintViolation renders via its own ``__str__``, which
        # uses the ``ConstraintViolation(kind=..., column=..., value=...)``
        # form. The single-prefix line matches the REPL
        # ``_format_exception`` rendering so golden files stay human
        # readable. Other exception classes keep the legacy
        # ``ERROR: <TypeName>: <message>`` shape so existing
        # ``error_cases/`` golden files (ParseError / ExecutionError)
        # are untouched.
        return f"ERROR: {exc}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    return _format_rows(rows)


@pytest.fixture
def golden_sql(request, tmp_path):
    sql_path: pathlib.Path = request.param
    expected_path = sql_path.with_suffix(".expected.txt")
    db_path = str(tmp_path / "e2e.db")

    outputs: list[str] = []
    db = tinydb.Database(db_path)
    try:
        for segment in _parse_source(sql_path.read_text()):
            if _is_reopen_directive(segment):
                # The directive lives in the SQL file; the runner
                # acts on it by closing+reopening, then the next
                # statement runs against the reopened file.
                db.close()
                db = tinydb.Database(db_path)
                continue
            # The segment may itself be a multi-statement block
            # (parser supports ``;``-separated scripts); split on
            # ``;`` so each sub-statement produces its own output
            # line and its own error envelope.
            for raw_stmt in (s.strip() for s in segment.split(";") if s.strip()):
                outputs.append(_run_one(db, raw_stmt))
        actual = "\n".join(outputs)
        if outputs:
            actual += "\n"
        expected = expected_path.read_text() if expected_path.exists() else ""
        yield sql_path, actual, expected
    finally:
        try:
            db.close()
        except Exception:
            # Closing a half-open Database must not mask the
            # assertion failure raised above.
            pass
