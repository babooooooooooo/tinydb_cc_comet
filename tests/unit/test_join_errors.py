"""tinydb-join-query (T3): ResolutionError hierarchy + top-level re-exports."""
import pytest
import tinydb
from tinydb.errors import (
    ResolutionError, AmbiguousColumn, DuplicateAlias,
    UnknownSource, UnknownQualifiedColumn,
    MissingUsingKey, IncompatibleKeyTypes, ExecutionError, TinydbError,
)


def test_resolution_error_is_execution_error_subclass():
    assert issubclass(ResolutionError, ExecutionError)
    assert issubclass(ExecutionError, TinydbError)


def test_ambiguous_column_subtype():
    assert issubclass(AmbiguousColumn, ResolutionError)


def test_duplicate_alias_subtype():
    assert issubclass(DuplicateAlias, ResolutionError)


def test_unknown_source_subtype():
    assert issubclass(UnknownSource, ResolutionError)


def test_unknown_qualified_column_subtype():
    assert issubclass(UnknownQualifiedColumn, ResolutionError)


def test_missing_using_key_subtype():
    assert issubclass(MissingUsingKey, ResolutionError)


def test_incompatible_key_types_subtype():
    assert issubclass(IncompatibleKeyTypes, ResolutionError)


def test_resolution_error_re_exported_from_top_level():
    assert tinydb.ResolutionError is ResolutionError
    assert tinydb.AmbiguousColumn is AmbiguousColumn
    assert tinydb.DuplicateAlias is DuplicateAlias
    assert tinydb.UnknownSource is UnknownSource
    assert tinydb.UnknownQualifiedColumn is UnknownQualifiedColumn
    assert tinydb.MissingUsingKey is MissingUsingKey
    assert tinydb.IncompatibleKeyTypes is IncompatibleKeyTypes