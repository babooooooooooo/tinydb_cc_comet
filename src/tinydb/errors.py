"""Tinydb exception hierarchy. TinydbError base; ParseError/TokenError/ExecutionError + storage-level."""


class TinydbError(Exception):
    """Base for all tinydb-raised exceptions."""


class ParseError(TinydbError):
    def __init__(self, line: int, col: int, msg: str):
        super().__init__(f"line {line}, col {col}: {msg}")
        self.line = line
        self.col = col
        self.msg = msg


class TokenError(TinydbError):
    def __init__(self, line: int, col: int, msg: str):
        super().__init__(f"line {line}, col {col}: {msg}")
        self.line = line
        self.col = col
        self.msg = msg


class ExecutionError(TinydbError): ...


_UNSET = object()


class ConstraintViolation(ExecutionError):
    """Raised when a column-level constraint is violated (NOT NULL / UNIQUE / PK).

    Always includes a stable ``kind`` string so callers (REPL, Python API
    consumers) can dispatch on the violation class. The ``column`` /
    ``columns`` / ``value`` attributes are populated contextually:

    * ``kind='null'``            — single-column (NOT NULL / PK) violation; uses ``column``.
    * ``kind='unique'``          — single- or composite-column UNIQUE violation; uses ``columns``.
    * ``kind='duplicate_pk'``    — PRIMARY KEY duplicate; uses ``columns``.
    """

    def __init__(self, kind: str, *, column=_UNSET, columns=_UNSET, value=_UNSET):
        self.kind = kind
        self.column = None if column is _UNSET else column
        self.columns = None if columns is _UNSET else columns
        self.value = None if value is _UNSET else value
        parts = [f"kind={kind!r}"]
        if column is not _UNSET:
            parts.append(f"column={column!r}")
        if columns is not _UNSET:
            parts.append(f"columns={list(columns)!r}")
        if value is not _UNSET:
            parts.append(f"value={value!r}")
        super().__init__(f"ConstraintViolation({', '.join(parts)})")


class InvalidDatabaseFile(TinydbError): ...
class UnsupportedSchemaVersion(TinydbError): ...


class PageFull(TinydbError):
    """Raised when a SlottedPage has no room for a new row and no tombstone to reuse."""


class CatalogFull(TinydbError): ...
