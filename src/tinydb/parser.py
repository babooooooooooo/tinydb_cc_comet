"""Recursive descent SQL parser -> AST (CREATE/DROP/INSERT/SELECT/DELETE/UPDATE). <= 750 lines."""

from dataclasses import dataclass
from typing import Any, Optional

from tinydb.errors import ParseError
from tinydb.tokenizer import Token

SUPPORTED_TYPES = {"INT", "TEXT", "FLOAT", "BOOL"}
SUPPORTED_OPS = {"="}
_LITERAL_TYPES = ("INT", "FLOAT", "TEXT", "BOOL")


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
    """INSERT INTO <table> [(cols)] VALUES (...), (...) statement."""

    table: str
    columns: list  # list[str]
    values: list  # list[list[Any]]
    line: int
    col: int


@dataclass(frozen=True)
class Select:
    """SELECT <cols> FROM <table> [WHERE <expr>] [ORDER BY ...] [LIMIT N] [OFFSET N].

    Engine-v1 upgrade: columns is tuple, where holds Expr, order_by/limit/offset
    default to empty/None for backward compatibility with MVP instances.
    """

    table: str
    columns: tuple  # tuple[str, ...]  ("*" or column names)
    where: Optional[Any] = None      # Expr (EqualsExpr | AndExpr | OrExpr | NotExpr)
    order_by: tuple = ()              # tuple[OrderByItem, ...]
    limit: Optional[int] = None
    offset: Optional[int] = None
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class Delete:
    """DELETE FROM <table> [WHERE <expr>] statement."""

    table: str
    where: Optional[Any] = None  # Expr | None
    line: int = 0
    col: int = 0


# --- engine-v1 expression AST ------------------------------------------------


@dataclass(frozen=True)
class EqualsExpr:
    """MVP-compatible: ``col = literal`` comparison."""

    column: str
    value: Any


@dataclass(frozen=True)
class AndExpr:
    """Short-circuit AND: ``left AND right``."""

    left: Any
    right: Any


@dataclass(frozen=True)
class OrExpr:
    """Short-circuit OR: ``left OR right``."""

    left: Any
    right: Any


@dataclass(frozen=True)
class NotExpr:
    """Unary NOT: ``NOT operand``."""

    operand: Any


# --- engine-v1 SELECT sub-clauses -------------------------------------------


@dataclass(frozen=True)
class OrderByItem:
    """ORDER BY item: column + ASC/DESC."""

    column: str
    descending: bool = False


# --- engine-v1 UPDATE statement ----------------------------------------------


@dataclass(frozen=True)
class Update:
    """UPDATE <table> SET <col=lit>[, ...] [WHERE <expr>] statement."""

    table: str
    sets: tuple                       # tuple[tuple[str, Expr], ...]
    where: Optional[Any] = None       # Expr | None
    line: int = 0
    col: int = 0


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
        if kw == "INSERT":
            return self._parse_insert()
        if kw == "SELECT":
            return self._parse_select()
        if kw == "DELETE":
            return self._parse_delete()
        if kw == "UPDATE":
            return self._parse_update()
        # All six supported statement keywords are dispatched above.
        # Reaching here means a KEYWORD (e.g. TABLE / INTO / VALUES / FROM /
        # WHERE / INT / TEXT / FLOAT / BOOL) appeared where a statement was
        # expected — that is a genuine syntax error, not an "unsupported"
        # statement. Surface the offending token instead of inventing a fake
        # "X not supported" message.
        raise ParseError(t.line, t.col, f"unexpected keyword {kw}")

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

    # --- INSERT INTO ... VALUES (...) --------------------------------------

    def _parse_insert(self) -> Insert:
        kw = self.expect_keyword("INSERT")
        self.expect_keyword("INTO")

        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        table = self.advance().value

        # Column list is required by the MVP grammar; INSERT without an
        # explicit column list is rejected for clarity.
        self.expect("PUNCT", "(")
        cols: list = []
        while True:
            ct = self.peek()
            if ct.type != "IDENT":
                raise ParseError(ct.line, ct.col, "expected column name")
            cols.append(self.advance().value)
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break
        self.expect("PUNCT", ")")

        self.expect_keyword("VALUES")

        values: list = []
        while True:
            self.expect("PUNCT", "(")
            row: list = []
            if self.peek().type == "PUNCT" and self.peek().value == ")":
                tok = self.peek()
                raise ParseError(tok.line, tok.col, "expected literal")
            while True:
                v = self.advance()
                if v.type not in _LITERAL_TYPES:
                    raise ParseError(v.line, v.col, "expected literal")
                row.append(v.value)
                if self.peek().type == "PUNCT" and self.peek().value == ",":
                    self.advance()
                    continue
                break
            if len(row) != len(cols):
                raise ParseError(
                    kw.line, kw.col,
                    f"value count mismatch: got {len(row)}, expected {len(cols)}",
                )
            values.append(row)
            self.expect("PUNCT", ")")
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break

        return Insert(
            table=table, columns=cols, values=values,
            line=kw.line, col=kw.col,
        )

    # --- SELECT [cols] FROM <table> [WHERE ...] ----------------------------

    def _parse_select(self) -> Select:
        kw = self.expect_keyword("SELECT")

        cols: list = []
        if self.peek().type == "PUNCT" and self.peek().value == "*":
            self.advance()
            cols = ["*"]
        else:
            if self.peek().type == "EOF":
                t = self.peek()
                raise ParseError(t.line, t.col, "expected column or *")
            while True:
                ct = self.peek()
                if ct.type != "IDENT":
                    raise ParseError(ct.line, ct.col, "expected column or *")
                cols.append(self.advance().value)
                if self.peek().type == "PUNCT" and self.peek().value == ",":
                    self.advance()
                    continue
                break

        # `FROM` is mandatory; SELECT without FROM is invalid in the MVP.
        ft = self.peek()
        if not (ft.type == "KEYWORD" and ft.value == "FROM"):
            raise ParseError(ft.line, ft.col, "expected FROM")
        self.advance()

        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        table = self.advance().value

        where = self._parse_where()

        return Select(
            table=table, columns=tuple(cols), where=where,
            line=kw.line, col=kw.col,
        )

    # --- DELETE FROM <table> [WHERE ...] -----------------------------------

    def _parse_delete(self) -> Delete:
        kw = self.expect_keyword("DELETE")
        self.expect_keyword("FROM")

        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        table = self.advance().value

        where = self._parse_where()

        return Delete(table=table, where=where, line=kw.line, col=kw.col)

    # --- UPDATE <table> SET <col>=<lit>[, ...] [WHERE <expr>] ---------------

    def _parse_update(self) -> Update:
        kw = self.expect_keyword("UPDATE")
        tt = self.peek()
        if tt.type != "IDENT":
            raise ParseError(tt.line, tt.col, "expected table name")
        table = self.advance().value
        self.expect_keyword("SET")

        sets: list = []
        while True:
            ct = self.peek()
            if ct.type != "IDENT":
                raise ParseError(ct.line, ct.col, "expected column name in SET")
            col = self.advance().value
            self.expect("PUNCT", "=")
            lit_tok = self.advance()
            if lit_tok.type not in _LITERAL_TYPES:
                raise ParseError(
                    lit_tok.line, lit_tok.col,
                    "SET right-hand side must be a literal",
                )
            sets.append((col, EqualsExpr(column=col, value=lit_tok.value)))
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break

        if not sets:
            raise ParseError(
                kw.line, kw.col,
                "UPDATE requires at least one SET assignment",
            )

        where = self._parse_where()
        return Update(
            table=table, sets=tuple(sets), where=where,
            line=kw.line, col=kw.col,
        )

    # --- shared WHERE clause helper ----------------------------------------

    def _parse_where(self) -> Optional[EqualsExpr]:
        """Parse `WHERE <col> = <literal>` if present; otherwise return None.

        engine-v1 returns an EqualsExpr AST node (not a tuple) so the
        executor's eval_expr can consume it uniformly with AND/OR/NOT.
        """
        if not (self.peek().type == "KEYWORD" and self.peek().value == "WHERE"):
            return None
        self.advance()

        ct = self.peek()
        if ct.type != "IDENT":
            raise ParseError(ct.line, ct.col, "expected column in WHERE")
        cname = self.advance().value

        op_tok = self.advance()
        if op_tok.type != "PUNCT" or op_tok.value not in SUPPORTED_OPS:
            op_repr = op_tok.value if op_tok.type != "EOF" else "EOF"
            raise ParseError(
                op_tok.line, op_tok.col,
                f"operator {op_repr} not supported; MVP supports only =",
            )

        lit = self.advance()
        if lit.type not in _LITERAL_TYPES:
            raise ParseError(lit.line, lit.col, "expected literal")
        return EqualsExpr(column=cname, value=lit.value)


# --- Public entry ------------------------------------------------------------


def parse(tokens: list[Token]) -> StatementList:
    """Parse a flat token list into a StatementList AST.

    Pure function: no I/O, no global state. Re-raise ParseError with
    (line, col) pointing at the offending token.
    """
    return _Parser(tokens).parse_statement_list()