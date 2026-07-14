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
class InvalidDatabaseFile(TinydbError): ...
class UnsupportedSchemaVersion(TinydbError): ...
class PageFull(TinydbError): ...
class CatalogFull(TinydbError): ...