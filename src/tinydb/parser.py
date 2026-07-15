"""Recursive descent SQL parser -> AST (CREATE/DROP/INSERT/SELECT/DELETE). <= 600 lines."""

from dataclasses import dataclass
from typing import Any, Optional

from tinydb.errors import ParseError
from tinydb.tokenizer import Token

SUPPORTED_TYPES = {"INT", "TEXT", "FLOAT", "BOOL"}


# --- AST nodes ---------------------------------------------------------------


@dataclass
class StatementList:
    """Wrapper for one or more parsed statements."""

    statements: list
    line: int = 1
    col: int = 1


@dataclass
class CreateTable:
    """CREATE TABLE <name> (<col> <type>, ...) statement."""

    name: str
    columns: list  # list[tuple[str, str]]  (column_name, type_name)
    line: int
    col: int


@dataclass
class DropTable:
    """DROP TABLE <name> statement."""

    name: str
    line: int
    col: int


@dataclass
class Insert:
    """INSERT INTO <table> [(cols)] VALUES (...) statement (parsed in Task 16)."""

    table: str
    columns: list  # list[str]
    values: list  # list[Any]
    line: int
    col: int


@dataclass
class Select:
    """SELECT <cols> FROM <table> [WHERE <col> <op> <val>] statement (parsed in Task 16)."""

    table: str
    columns: list  # list[str]  ("*" or column names)
    where: Optional[tuple]  # (column, op, value) | None
    line: int
    col: int


@dataclass
class Delete:
    """DELETE FROM <table> [WHERE <col> <op> <val>] statement (parsed in Task 16)."""

    table: str
    where: Optional[tuple]  # (column, op, value) | None
    line: int
    col: int


# --- Parser ------------------------------------------------------------------


class _Parser:
    """Recursive descent parser. Operates on a flat token list with an index cursor."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0

    # --- cursor primitives --------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.i]

    def advance(self) -> Token:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def at_end(self) -> bool:
        return self.peek().type == "EOF"

    def expect(self, type_: str, value: Any = None) -> Token:
        t = self.peek()
        if t.type != type_ or (value is not None and t.value != value):
            raise ParseError(
                t.line, t.col,
                f"expected {type_} {value!r}, got {t.type} {t.value!r}",
            )
        return self.advance()

    def expect_keyword(self, kw: str) -> Token:
        t = self.peek()
        if t.type != "KEYWORD" or t.value != kw:
            raise ParseError(t.line, t.col, f"expected keyword {kw}")
        return self.advance()

    # --- top-level dispatch -------------------------------------------------

    def parse_statement_list(self) -> StatementList:
        stmts: list = []
        while not self.at_end():
            stmts.append(self.parse_statement())
            if self.peek().type == "PUNCT" and self.peek().value == ";":
                self.advance()
                continue
            # No more semicolons — must be EOF or next statement starts a new keyword.
        return StatementList(statements=stmts)

    def parse_statement(self) -> Any:
        t = self.peek()
        if t.type != "KEYWORD":
            raise ParseError(t.line, t.col, f"expected statement, got {t.type}")

        kw = t.value
        if kw == "CREATE":
            return self._parse_create_table()
        if kw == "DROP":
            return self._parse_drop_table()
        # INSERT / SELECT / DELETE land in Task 16.
        raise ParseError(t.line, t.col, f"{kw} not supported yet")

    # --- CREATE TABLE -------------------------------------------------------

    def _parse_create_table(self) -> CreateTable:
        kw = self.expect_keyword("CREATE")
        self.expect_keyword("TABLE")

        # table name (must be IDENT — keywords like "TABLE" do not qualify)
        name_tok = self.peek()
        if name_tok.type != "IDENT":
            raise ParseError(name_tok.line, name_tok.col, "expected table name")
        name = self.advance().value

        self.expect("PUNCT", "(")

        cols: list = []
        seen: set = set()

        # Empty column list: `CREATE TABLE t ()` is invalid.
        if self.peek().type == "PUNCT" and self.peek().value == ")":
            tok = self.peek()
            raise ParseError(tok.line, tok.col, "expected column name")

        while True:
            col_tok = self.peek()
            if col_tok.type != "IDENT":
                raise ParseError(col_tok.line, col_tok.col, "expected column name")
            cname = self.advance().value
            if cname in seen:
                raise ParseError(col_tok.line, col_tok.col, f"duplicate column {cname}")
            seen.add(cname)

            type_tok = self.peek()
            if (
                type_tok.type != "KEYWORD"
                or type_tok.value not in SUPPORTED_TYPES
            ):
                # Includes IDENTs like VARCHAR, KEYWORDs like BIGINT, etc.
                value_repr = (
                    type_tok.value if type_tok.type != "EOF" else "EOF"
                )
                raise ParseError(
                    type_tok.line, type_tok.col,
                    f"type {value_repr} not supported in MVP",
                )
            ctype = self.advance().value
            cols.append((cname, ctype))

            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break

        self.expect("PUNCT", ")")
        return CreateTable(name=name, columns=cols, line=kw.line, col=kw.col)

    # --- DROP TABLE ---------------------------------------------------------

    def _parse_drop_table(self) -> DropTable:
        kw = self.expect_keyword("DROP")
        self.expect_keyword("TABLE")
        t = self.peek()
        if t.type != "IDENT":
            # Covers EOF, KEYWORD, PUNCT — never bare KeyError.
            raise ParseError(t.line, t.col, "expected table name")
        return DropTable(name=self.advance().value, line=kw.line, col=kw.col)


# --- Public entry ------------------------------------------------------------


def parse(tokens: list[Token]) -> StatementList:
    """Parse a flat token list into a StatementList AST.

    Pure function: no I/O, no global state. Re-raise ParseError with
    (line, col) pointing at the offending token.
    """
    return _Parser(tokens).parse_statement_list()