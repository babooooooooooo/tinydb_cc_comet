"""Unit tests for tinydb error hierarchy alignment (Task 7 of review-fixes).

Verifies that ``DatabaseLocked`` (raised by the cross-process lock layer)
inherits from ``ExecutionError`` (not directly from ``TinydbError``), making
its parent chain consistent with other user-recoverable errors like
``ConstraintViolation`` and ``ResolutionError``.

The change is non-breaking: ``except TinydbError`` still catches
``DatabaseLocked`` because ``ExecutionError`` itself subclasses
``TinydbError``.
"""
from tinydb import errors as errors_module
from tinydb.errors import (
    DatabaseLocked,
    ExecutionError,
    TinydbError,
)


# ---------------------------------------------------------------------------
# Parent class alignment
# ---------------------------------------------------------------------------

def test_database_locked_subclasses_execution_error():
    """DatabaseLocked must subclass ExecutionError, not TinydbError directly.

    The parent chain (DatabaseLocked -> ExecutionError -> TinydbError) must
    be intact so that ``except TinydbError`` keeps catching DatabaseLocked
    while grouping DatabaseLocked with other user-recoverable errors.
    """
    assert issubclass(DatabaseLocked, ExecutionError)
    # Both must hold: subclass of ExecutionError AND reachable via TinydbError
    # chain (non-breaking property).
    assert issubclass(DatabaseLocked, TinydbError)


def test_repl_catches_database_locked_via_tinydb_error():
    """REPL's ``except TinydbError`` path must still catch DatabaseLocked.

    Models the catch block used by ``_repl_meta._run_sql`` (and any other
    caller that uses the broad TinydbError umbrella). After the parent
    change, this still works because ExecutionError -> TinydbError.
    """
    err = DatabaseLocked("/tmp/x.tdb")
    try:
        raise err
    except TinydbError as caught:
        assert caught is err
    else:
        raise AssertionError("DatabaseLocked was not caught by except TinydbError")


def test_database_locked_importable_from_wildcard():
    """`from tinydb.errors import *` must still expose DatabaseLocked.

    The wildcard import path is the public re-export surface used by REPL
    callers; DatabaseLocked must remain in errors.__all__.
    """
    exported = getattr(errors_module, "__all__", None)
    # If __all__ is not defined, Python falls back to public names.
    # Both forms must surface DatabaseLocked.
    if exported is not None:
        assert "DatabaseLocked" in exported
    assert hasattr(errors_module, "DatabaseLocked")
