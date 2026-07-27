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


class SchemaMismatch(TinydbError):
    """Raised when on-disk schema version is incompatible with current code.

    Typically raised when attempting to open a database file whose schema
    version cannot be safely auto-upgraded (e.g. v2 db with WAL residue —
    auto-upgrade could strand uncommitted WAL records on the wrong schema).
    Callers are expected to run an explicit migration path.
    """

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


class PageFull(TinydbError):
    """Raised when a SlottedPage has no room for a new row and no tombstone to reuse."""


class CatalogFull(TinydbError): ...


# --- tinydb-join-query (T3): name resolution errors -----------------------

class ResolutionError(ExecutionError):
    """名称解析阶段抛出的错误基类（未知表 / 限定列 / 歧义 / USING 缺失 等）。"""


class UnknownSource(ResolutionError):
    """FROM / JOIN 中的表名或别名不在 catalog。"""

    def __init__(self, qualifier_or_name: str):
        super().__init__(f"unknown table or alias: {qualifier_or_name!r}")
        self.qualifier_or_name = qualifier_or_name


class UnknownQualifiedColumn(ResolutionError):
    """限定列 `qualifier.column` 在 source_map 中无对应。"""

    def __init__(self, qualifier: str, column: str):
        super().__init__(f"unknown column {column!r} in source {qualifier!r}")
        self.qualifier = qualifier
        self.column = column


class AmbiguousColumn(ResolutionError):
    """裸列名在多个 source 中同时存在。"""

    def __init__(self, column: str, sources):
        s = tuple(sources)
        super().__init__(f"ambiguous column {column!r} in sources {s!r}")
        self.column = column
        self.sources = s


class DuplicateAlias(ResolutionError):
    """同一别名指向多个 source。"""

    def __init__(self, alias: str, source1: str, source2: str):
        super().__init__(f"duplicate alias {alias!r}: {source1!r} vs {source2!r}")
        self.alias = alias
        self.source1 = source1
        self.source2 = source2


class MissingUsingKey(ResolutionError):
    """USING 列表中的列在某一侧 source 缺失。"""

    def __init__(self, column: str, side: str):
        super().__init__(f"USING column {column!r} missing from {side!r} source")
        self.column = column
        self.side = side


class IncompatibleKeyTypes(ResolutionError):
    """USING / NATURAL 共同列类型不可比较。"""

    def __init__(self, left_type: str, right_type: str):
        super().__init__(f"incompatible USING/NATURAL key types: {left_type!r} vs {right_type!r}")
        self.left_type = left_type
        self.right_type = right_type


# --- tinydb-concurrency-control (T1): DatabaseLocked ----------------------

class DatabaseLocked(TinydbError):
    """DB 文件被另一进程持有时抛出的异常.

    通过 fcntl.flock 做跨进程独占锁.``path`` 属性标识被争用的 DB 文件.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"database {path!r} is locked by another process")
