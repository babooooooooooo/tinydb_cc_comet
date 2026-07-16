"""Tests for tinydb package-level imports, version, and exception hierarchy."""


def test_tinydb_imports_database_and_row():
    import tinydb
    assert hasattr(tinydb, "Database")
    assert hasattr(tinydb, "Row")


def test_tinydb_version_string():
    import tinydb
    assert tinydb.__version__ == "0.1.0"


def test_tinydb_exposes_exception_classes():
    import tinydb
    from tinydb import errors
    assert issubclass(errors.TinydbError, Exception)
    for name in ("ParseError", "TokenError", "ExecutionError",
                 "InvalidDatabaseFile", "UnsupportedSchemaVersion",
                 "PageFull", "CatalogFull"):
        assert hasattr(errors, name), f"missing errors.{name}"
