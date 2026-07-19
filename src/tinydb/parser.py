"""Recursive descent SQL parser -> AST (CREATE/DROP/INSERT/SELECT/DELETE/UPDATE). <= 750 lines."""

from dataclasses import dataclass
from typing import Any, Optional

from tinydb.errors import ParseError
from tinydb.tokenizer import Token

SUPPORTED_TYPES = {
    # MVP
    "INT", "TEXT", "FLOAT", "BOOL",
    # tinydb-types: integer widths
    "SMALLINT", "BIGINT",
    # tinydb-types: float widths
    "DOUBLE", "REAL",
    # tinydb-types: parametric string types
    "VARCHAR", "CHAR",
    # tinydb-types: parametric decimal
    "DECIMAL",
    # tinydb-types: date/time
    "DATE", "TIME", "TIMESTAMP",
    # tinydb-types: aliases (resolved at codec lookup time)
    "INTEGER", "BOOLEAN",
}
SUPPORTED_OPS = {"="}
# tinydb-aggregation (T3): HAVING/ORDER compare-side operators include
# the full SQL six-operator set so the aggregation pipeline can evaluate
# range predicates on aggregate aliases / group columns.
_HAVING_OPS = {"=", ">", "<", ">=", "<=", "!="}
_LITERAL_TYPES = ("INT", "FLOAT", "TEXT", "BOOL")
_DATETIME_KEYWORDS = ("DATE", "TIME", "TIMESTAMP")

# Parametric types require an explicit parameter list at parse time.
# Maps type name -> expected arity (number of required int params).
_PARAMETRIC_TYPES = {
    "VARCHAR": 1,
    "CHAR": 1,
    "DECIMAL": 2,
}


# --- AST nodes ---------------------------------------------------------------


@dataclass
class StatementList:
    """Wrapper for one or more parsed statements."""

    statements: list
    line: int = 1
    col: int = 1


@dataclass(frozen=True)
class ColumnDefinition:
    """CREATE TABLE column definition: name, type, and column-level constraints.

    Pure data — the parser does NOT consult the catalog; the executor maps
    a list of ``ColumnDefinition`` into a list of ``catalog.Column`` at
    CREATE TABLE time (Task 7).

    tinydb-types (Task 12): ``type_params`` carries parametric type info
    (e.g. ``(10,)`` for VARCHAR(10), ``(10, 2)`` for DECIMAL(10, 2)).
    Empty tuple for non-parametric types."""

    name: str
    type: str
    type_params: tuple = ()
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False


@dataclass(frozen=True)
class CreateTable:
    """CREATE TABLE <name> (<col> <type>, ...) statement."""

    name: str
    columns: tuple[ColumnDefinition, ...]
    if_not_exists: bool = False
    line: int = 0
    col: int = 0


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

    tinydb-aggregation extension: select_items / group_by / having /
    aggregate_aliases trigger the 5-phase aggregation pipeline in the
    executor when ``aggregate_aliases`` or ``group_by`` is non-empty.
    """

    table: str
    columns: tuple  # tuple[str, ...]  ("*" or column names)
    where: Optional[Any] = None      # Expr (EqualsExpr | AndExpr | OrExpr | NotExpr)
    order_by: tuple = ()              # tuple[OrderByItem, ...]
    limit: Optional[int] = None
    offset: Optional[int] = None
    # --- tinydb-aggregation (T2) ---
    select_items: tuple = ()           # tuple[SelectItem, ...]
    group_by: tuple = ()               # tuple[str, ...]
    having: Optional[object] = None    # AggregateCall | tuple[col, op, lit] | None
    aggregate_aliases: tuple = ()      # tuple[str, ...]
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


# --- tinydb-aggregation AST nodes -------------------------------------------


@dataclass(frozen=True)
class AggregateCall:
    """SQL aggregate function call: COUNT / SUM / AVG / MIN / MAX.

    `arg` is the sentinel string ``"*"`` for ``COUNT(*)``; otherwise it is an
    ``Expr`` tuple (typically ``("column", "colname")`` for phase 1).
    `alias` carries the explicit ``AS ident`` if present, else ``None`` so the
    caller can default it (``count``, ``sum_x`` etc.).
    """

    func: str                          # one of COUNT/SUM/AVG/MIN/MAX
    arg: object                        # '*' (sentinel str) for COUNT(*), else Expr tuple
    alias: Optional[str] = None        # explicit 'AS ident'; None -> defaulted
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class SelectItem:
    """A single item in the SELECT projection list.

    One of:
      - kind='star'         (SELECT *)
      - kind='column'       (IDENT [AS alias])
      - kind='aggregate'    (AggregateCall)
    """

    kind: str                          # 'star' | 'column' | 'aggregate'
    name: Optional[str] = None         # column name (column kind)
    alias: Optional[str] = None        # explicit alias (column kind, or aggregate alias)
    aggregate: Optional[AggregateCall] = None  # aggregate detail (aggregate kind)


# --- engine-v1 UPDATE statement ----------------------------------------------


@dataclass(frozen=True)
class Update:
    """UPDATE <table> SET <col=lit>[, ...] [WHERE <expr>] statement."""

    table: str
    sets: tuple                       # tuple[tuple[str, Expr], ...]
    where: Optional[Any] = None       # Expr | None
    line: int = 0
    col: int = 0


# --- tinydb-acid (Task 4): transaction-control statements ---


@dataclass(frozen=True)
class Begin:
    """BEGIN [;]: open a transaction."""

    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class Commit:
    """COMMIT [;]: flush pending writes."""

    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class Rollback:
    """ROLLBACK [;]: discard pending writes."""

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
        if kw == "BEGIN":
            self.advance()
            return Begin(line=t.line, col=t.col)
        if kw == "COMMIT":
            self.advance()
            return Commit(line=t.line, col=t.col)
        if kw == "ROLLBACK":
            self.advance()
            return Rollback(line=t.line, col=t.col)

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

        cols: list[ColumnDefinition] = []
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
            if type_tok.type not in ("KEYWORD", "IDENT"):
                value_repr = (
                    type_tok.value if type_tok.type != "EOF" else "EOF"
                )
                raise ParseError(
                    type_tok.line, type_tok.col,
                    f"expected type name, got {type_tok.type} {value_repr!r}",
                )
            type_name = type_tok.value.upper()
            if type_name not in SUPPORTED_TYPES:
                raise ParseError(
                    type_tok.line, type_tok.col,
                    f"type {type_name} not supported",
                )
            self.advance()  # consume the type name token

            type_params = self._parse_type_params(type_name, type_tok)

            # Parse optional constraint clauses: NOT NULL / UNIQUE / PRIMARY KEY.
            # Order-independent; multiple clauses allowed on one column.
            nullable = True
            unique = False
            primary_key = False
            saw_unique = False
            saw_pk = False
            saw_not_null = False
            while self.peek().type == "KEYWORD" and self.peek().value in {
                "NOT", "NULL", "PRIMARY", "KEY", "UNIQUE",
            }:
                kw_tok = self.advance()
                if kw_tok.value == "NOT":
                    nxt = self.peek()
                    if not (nxt.type == "KEYWORD" and nxt.value == "NULL"):
                        raise ParseError(
                            nxt.line, nxt.col, "expected NULL after NOT"
                        )
                    self.advance()
                    if saw_not_null:
                        raise ParseError(
                            kw_tok.line, kw_tok.col, "duplicate NOT NULL constraint"
                        )
                    saw_not_null = True
                    nullable = False
                elif kw_tok.value == "NULL":
                    # Bare NULL (without leading NOT) is rejected (R2 裁决 2).
                    raise ParseError(
                        kw_tok.line, kw_tok.col,
                        "bare NULL not allowed; use NOT NULL or omit",
                    )
                elif kw_tok.value == "PRIMARY":
                    nxt = self.peek()
                    if not (nxt.type == "KEYWORD" and nxt.value == "KEY"):
                        raise ParseError(
                            nxt.line, nxt.col, "expected KEY after PRIMARY"
                        )
                    self.advance()
                    if saw_pk:
                        raise ParseError(
                            kw_tok.line, kw_tok.col, "duplicate PRIMARY KEY"
                        )
                    saw_pk = True
                    primary_key = True
                elif kw_tok.value == "KEY":
                    # Bare KEY without PRIMARY is rejected.
                    raise ParseError(
                        kw_tok.line, kw_tok.col,
                        "unexpected KEY; use PRIMARY KEY",
                    )
                elif kw_tok.value == "UNIQUE":
                    if saw_unique:
                        raise ParseError(
                            kw_tok.line, kw_tok.col, "duplicate UNIQUE constraint"
                        )
                    saw_unique = True
                    unique = True

            cols.append(ColumnDefinition(
                name=cname, type=type_name, type_params=type_params,
                nullable=nullable, unique=unique, primary_key=primary_key,
            ))

            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break

        self.expect("PUNCT", ")")
        return CreateTable(
            name=name, columns=tuple(cols),
            line=kw.line, col=kw.col,
        )

    # --- type parameter parsing (Task 12) -----------------------------------

    def _parse_type_params(self, type_name: str, name_tok: Token) -> tuple:
        """Parse the optional ``(N)`` / ``(p, s)`` parameter list for a type.

        Returns an empty tuple for non-parametric types. Raises ``ParseError``
        for:
        - parametric types missing the parameter list (e.g. ``VARCHAR`` alone)
        - parametric types with the wrong arity (e.g. ``DECIMAL(10)``)
        - non-parametric types given a parameter list (e.g. ``INT(10)``)
        - non-integer param values (e.g. ``VARCHAR(3.5)``)
        - parametric types whose params violate per-type value ranges
          (e.g. ``VARCHAR(0)``, ``DECIMAL(20, 2)``, ``DECIMAL(5, 5)``)
        """
        has_paren = self.peek().type == "PUNCT" and self.peek().value == "("
        is_parametric = type_name in _PARAMETRIC_TYPES

        if not has_paren:
            if is_parametric:
                # Missing parameter list for a parametric type — explicit error.
                if type_name == "DECIMAL":
                    msg = "DECIMAL requires (p, s)"
                else:
                    msg = f"{type_name} requires (N)"
                raise ParseError(name_tok.line, name_tok.col, msg)
            return ()

        # Consume the opening "(".
        self.advance()

        # Parse first int arg (mandatory when "(" is present).
        first_tok = self.peek()
        if first_tok.type != "INT":
            raise ParseError(
                first_tok.line, first_tok.col,
                "expected integer in type params",
            )
        params: list = [self.advance().value]

        # Optional second int arg.
        if self.peek().type == "PUNCT" and self.peek().value == ",":
            self.advance()
            second_tok = self.peek()
            if second_tok.type != "INT":
                raise ParseError(
                    second_tok.line, second_tok.col,
                    "expected integer after ','",
                )
            params.append(self.advance().value)

        self.expect("PUNCT", ")")

        # Non-parametric types must NOT accept params at all.
        if not is_parametric:
            raise ParseError(
                name_tok.line, name_tok.col,
                f"{type_name} does not accept type parameters",
            )

        # Arity validation.
        expected_arity = _PARAMETRIC_TYPES[type_name]
        if len(params) != expected_arity:
            if type_name == "DECIMAL":
                raise ParseError(
                    name_tok.line, name_tok.col,
                    "DECIMAL requires (p, s)",
                )
            raise ParseError(
                name_tok.line, name_tok.col,
                f"{type_name} requires (N)",
            )

        # Per-type value-range validation. Re-raise codec_for's ValueError as a
        # ParseError so the user gets a parser-context message. type_system
        # already validates: VARCHAR N>=1, DECIMAL 1<=p<=18 and 0<=s<p.
        from tinydb.type_system import codec_for  # lazy import avoids cycles

        try:
            codec_for(type_name, tuple(params))
        except ValueError as e:
            raise ParseError(
                name_tok.line, name_tok.col,
                f"{type_name}({', '.join(str(p) for p in params)}) invalid: {e}",
            ) from e

        return tuple(params)

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
                v = self.peek()
                if v.type == "KEYWORD" and v.value == "NULL":
                    self.advance()
                    row.append(None)
                elif v.type == "KEYWORD" and v.value in _DATETIME_KEYWORDS:
                    row.append(self._parse_datetime_literal())
                elif v.type == "KEYWORD" and v.value == "DECIMAL":
                    row.append(self._parse_decimal_literal())
                else:
                    tok = self.advance()
                    if tok.type in _LITERAL_TYPES:
                        row.append(tok.value)
                    else:
                        raise ParseError(tok.line, tok.col, "expected literal")
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

        # tinydb-aggregation (T3): parse projection items via the shared
        # _parse_select_items helper so SELECT COUNT(*), SUM(x), cols, * are
        # all uniformly supported. The legacy ``columns`` field is still
        # populated for backward compatibility with database.Row wrapping.
        items = self._parse_select_items()

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

        # GROUP BY (optional, aggregation only)
        group_by = ()
        if self.peek().type == "KEYWORD" and self.peek().value == "GROUP":
            self.expect_keyword("GROUP")
            self.expect_keyword("BY")
            group_by = self._parse_col_list()

        # HAVING (optional, aggregation only)
        having = None
        if self.peek().type == "KEYWORD" and self.peek().value == "HAVING":
            self.expect_keyword("HAVING")
            having = self._parse_having_expr()

        order_by = self._parse_order_by()
        limit = self._parse_limit()
        offset = self._parse_offset()

        # Cached alias list for the executor's HAVING/ORDER evaluation.
        aggregate_aliases = tuple(
            si.alias or _default_alias(si.aggregate)
            for si in items if si.kind == "aggregate"
        )

        # Legacy columns field for backward compat / database.Row wrapping.
        legacy_cols: list = []
        for si in items:
            if si.kind == "star":
                legacy_cols = ["*"]
                break
            if si.kind == "aggregate":
                legacy_cols.append(si.alias or _default_alias(si.aggregate))
            else:
                legacy_cols.append(si.name)

        return Select(
            table=table, columns=tuple(legacy_cols), where=where,
            order_by=order_by, limit=limit, offset=offset,
            line=kw.line, col=kw.col,
            select_items=items,
            group_by=group_by,
            having=having,
            aggregate_aliases=aggregate_aliases,
        )

    # --- ORDER BY / LIMIT / OFFSET ---------------------------------------

    def _parse_order_by(self) -> tuple:
        if not self._peek_kw("ORDER"):
            return ()
        self.advance()
        if not self._peek_kw("BY"):
            tok = self.peek()
            raise ParseError(tok.line, tok.col, "expected BY after ORDER")
        self.advance()
        items: list = []
        while True:
            ct = self.peek()
            if ct.type != "IDENT":
                raise ParseError(ct.line, ct.col, "expected column in ORDER BY")
            col = self.advance().value
            desc = False
            if self._peek_kw("ASC"):
                self.advance()
            elif self._peek_kw("DESC"):
                self.advance()
                desc = True
            items.append(OrderByItem(column=col, descending=desc))
            if self._peek_punct(","):
                self.advance()
                continue
            break
        return tuple(items)

    def _parse_limit(self) -> Optional[int]:
        if not self._peek_kw("LIMIT"):
            return None
        self.advance()
        t = self.advance()
        if t.type != "INT":
            raise ParseError(
                t.line, t.col, "LIMIT must be a non-negative integer",
            )
        if t.value < 0:
            raise ParseError(t.line, t.col, "LIMIT must be non-negative")
        return int(t.value)

    def _parse_offset(self) -> Optional[int]:
        if not self._peek_kw("OFFSET"):
            return None
        self.advance()
        t = self.advance()
        if t.type != "INT":
            raise ParseError(
                t.line, t.col, "OFFSET must be a non-negative integer",
            )
        return int(t.value)

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
            if (self.peek().type == "KEYWORD"
                    and self.peek().value in _DATETIME_KEYWORDS):
                val = self._parse_datetime_literal()
            elif (self.peek().type == "KEYWORD"
                    and self.peek().value == "DECIMAL"):
                val = self._parse_decimal_literal()
            else:
                lit_tok = self.advance()
                if lit_tok.type not in _LITERAL_TYPES:
                    raise ParseError(
                        lit_tok.line, lit_tok.col,
                        "SET right-hand side must be a literal",
                    )
                val = lit_tok.value
            sets.append((col, EqualsExpr(column=col, value=val)))
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

    def _parse_where(self) -> Optional[Any]:
        """Parse `WHERE <expr>` if present; otherwise return None.

        Engine-v1 returns an Expr AST (EqualsExpr / AndExpr / OrExpr /
        NotExpr); the executor's eval_expr handles all four uniformly.

        tinydb-aggregation (E1): WHERE cannot contain aggregate function
        calls (use HAVING instead). Aggregate calls in HAVING/SELECT are
        handled by the aggregation pipeline.
        """
        if not (self.peek().type == "KEYWORD" and self.peek().value == "WHERE"):
            return None
        self.advance()
        t = self.peek()
        if t.type == "KEYWORD" and t.value in {"COUNT", "SUM", "AVG", "MIN", "MAX"}:
            raise ParseError(
                t.line, t.col,
                f"aggregate function {t.value} not allowed in WHERE; use HAVING",
            )
        return self._parse_expr()

    # --- expression precedence chain (OR < AND < NOT < primary) ----------

    def _peek_kw(self, kw: str) -> bool:
        t = self.peek()
        return t.type == "KEYWORD" and t.value == kw

    def _peek_punct(self, p: str) -> bool:
        t = self.peek()
        return t.type == "PUNCT" and t.value == p

    def _parse_expr(self) -> Any:
        return self._parse_or_expr()

    def _parse_or_expr(self) -> Any:
        left = self._parse_and_expr()
        while self._peek_kw("OR"):
            self.advance()
            right = self._parse_and_expr()
            left = OrExpr(left=left, right=right)
        return left

    def _parse_and_expr(self) -> Any:
        left = self._parse_not_expr()
        while self._peek_kw("AND"):
            self.advance()
            right = self._parse_not_expr()
            left = AndExpr(left=left, right=right)
        return left

    def _parse_not_expr(self) -> Any:
        if self._peek_kw("NOT"):
            self.advance()
            return NotExpr(operand=self._parse_not_expr())
        return self._parse_primary()

    def _parse_primary(self) -> Any:
        if self._peek_punct("("):
            self.advance()
            inner = self._parse_expr()
            self.expect("PUNCT", ")")
            return inner
        return self._parse_comparison()

    def _parse_comparison(self) -> EqualsExpr:
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
        if (self.peek().type == "KEYWORD"
                and self.peek().value in _DATETIME_KEYWORDS):
            lit_val = self._parse_datetime_literal()
        elif (self.peek().type == "KEYWORD"
                and self.peek().value == "DECIMAL"):
            lit_val = self._parse_decimal_literal()
        else:
            lit = self.advance()
            if lit.type not in _LITERAL_TYPES:
                raise ParseError(lit.line, lit.col, "expected literal")
            lit_val = lit.value
        return EqualsExpr(column=cname, value=lit_val)

    # --- DATE / TIME / TIMESTAMP literal prefix ---------------------------

    def _parse_datetime_literal(self):
        """Parse DATE / TIME / TIMESTAMP 'literal' and return a Python value.

        The literal string is validated via ``datetime`` ISO parsers, matching
        the codec's encoding contract for date/time/timestamp types.
        """
        import datetime as _dt
        kw = self.expect_keyword(self.peek().value)
        text_tok = self.advance()
        if text_tok.type != "TEXT":
            raise ParseError(
                text_tok.line, text_tok.col,
                f"{kw.value} literal requires quoted string",
            )
        text = text_tok.value
        try:
            if kw.value == "DATE":
                return _dt.date.fromisoformat(text)
            if kw.value == "TIME":
                return _dt.time.fromisoformat(text)
            if kw.value == "TIMESTAMP":
                return _dt.datetime.fromisoformat(text)
        except ValueError as e:
            raise ParseError(
                kw.line, kw.col,
                f"{kw.value} literal invalid: {text!r} ({e})",
            ) from e
        # Unreachable: expect_keyword guarantees one of the three above.
        raise ParseError(kw.line, kw.col, f"unknown datetime literal {kw.value}")

    # --- DECIMAL literal prefix ---------------------------------------------

    def _parse_decimal_literal(self):
        """Parse DECIMAL 'literal' and return a Python float.

        Mirrors the DATE/TIME/TIMESTAMP literal contract: the quoted text is
        validated via ``float()`` and surfaced as a Python float. The codec
        applies the DECIMAL(p, s) rounding/encode at write time.
        """
        kw = self.expect_keyword("DECIMAL")
        text_tok = self.advance()
        if text_tok.type != "TEXT":
            raise ParseError(
                text_tok.line, text_tok.col,
                f"{kw.value} literal requires quoted string",
            )
        text = text_tok.value
        try:
            return float(text)
        except ValueError as e:
            raise ParseError(
                kw.line, kw.col,
                f"{kw.value} literal invalid: {text!r} ({e})",
            ) from e

    # --- tinydb-aggregation helpers (T3/T4) --------------------------------

    def _parse_select_items(self) -> tuple:
        """Parse comma-separated SELECT projection items."""
        items: list = []
        seen_aliases: set = set()

        # SELECT *
        if self.peek().type == "PUNCT" and self.peek().value == "*":
            self.advance()
            items.append(SelectItem(kind="star"))
            return tuple(items)

        while True:
            item = self._parse_select_item()
            eff_alias = item.alias
            if eff_alias is not None:
                if eff_alias in seen_aliases:
                    line = item.aggregate.line if item.aggregate else 0
                    col = item.aggregate.col if item.aggregate else 0
                    raise ParseError(line, col, f"duplicate alias {eff_alias!r}")
                seen_aliases.add(eff_alias)
            items.append(item)

            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break
        return tuple(items)

    def _parse_select_item(self) -> SelectItem:
        """Parse a single SELECT item (column, aggregate, or star)."""
        t = self.peek()
        if self._is_keyword(t, "COUNT", "SUM", "AVG", "MIN", "MAX"):
            agg = self._parse_aggregate_call()
            alias = None
            if self._is_keyword(self.peek(), "AS"):
                self.advance()
                ident = self.peek()
                if ident.type != "IDENT":
                    raise ParseError(ident.line, ident.col, "expected alias after AS")
                alias = self.advance().value
            if alias is not None:
                agg = AggregateCall(
                    func=agg.func, arg=agg.arg, alias=alias,
                    line=agg.line, col=agg.col,
                )
            return SelectItem(kind="aggregate", alias=alias, aggregate=agg)

        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected column or aggregate function")
        name = self.advance().value
        alias = None
        if self._is_keyword(self.peek(), "AS"):
            self.advance()
            ident = self.peek()
            if ident.type != "IDENT":
                raise ParseError(ident.line, ident.col, "expected alias after AS")
            alias = self.advance().value
        return SelectItem(kind="column", name=name, alias=alias)

    def _parse_aggregate_call(self) -> AggregateCall:
        """Parse COUNT(*) | (COUNT|SUM|AVG|MIN|MAX) '(' (IDENT | '*') ')'."""
        func_tok = self.peek()
        func = self.advance().value
        self.expect("PUNCT", "(")
        if self.peek().type == "PUNCT" and self.peek().value == "*":
            self.advance()
            arg: object = "*"
        else:
            col_tok = self.peek()
            if col_tok.type != "IDENT":
                raise ParseError(
                    col_tok.line, col_tok.col,
                    "expected column or * in aggregate",
                )
            arg = ("column", self.advance().value)
        self.expect("PUNCT", ")")
        return AggregateCall(func=func, arg=arg, line=func_tok.line, col=func_tok.col)

    def _parse_col_list(self) -> tuple:
        """Parse comma-separated IDENT list for GROUP BY."""
        cols: list = []
        while True:
            t = self.peek()
            if t.type != "IDENT":
                raise ParseError(t.line, t.col, "expected column name in GROUP BY")
            cols.append(self.advance().value)
            if self.peek().type == "PUNCT" and self.peek().value == ",":
                self.advance()
                continue
            break
        return tuple(cols)

    def _parse_having_expr(self):
        """Parse HAVING clause: aggregate_call OR (IDENT op literal)."""
        if self._is_keyword(self.peek(), "COUNT", "SUM", "AVG", "MIN", "MAX"):
            return self._parse_aggregate_call()

        ct = self.peek()
        if ct.type != "IDENT":
            raise ParseError(ct.line, ct.col, "expected column in HAVING")
        cname = self.advance().value

        op_tok = self.advance()
        if op_tok.type != "PUNCT" or op_tok.value not in _HAVING_OPS:
            op_repr = op_tok.value if op_tok.type != "EOF" else "EOF"
            raise ParseError(
                op_tok.line, op_tok.col,
                f"operator {op_repr!r} not supported in HAVING",
            )

        lit = self.advance()
        if lit.type not in _LITERAL_TYPES:
            raise ParseError(lit.line, lit.col, "expected literal in HAVING")
        return (cname, op_tok.value, lit.value)

    def _is_keyword(self, t, *names: str) -> bool:
        """Return True if ``t`` matches any of the named keywords.

        Accepts the keyword as either a KEYWORD token (for grammar-level
        reserved words) or an IDENT token whose uppercase value matches
        (for non-reserved context-dependent keywords like AS / ASC / DESC).
        """
        for name in names:
            if (
                (t.type == "KEYWORD" and t.value == name)
                or (t.type == "IDENT" and str(t.value).upper() == name)
            ):
                return True
        return False


def _default_alias(agg: AggregateCall) -> str:
    """Default aggregate alias per design doc (T2)."""
    if agg.arg == "*":
        return "count"
    if (
        isinstance(agg.arg, tuple)
        and len(agg.arg) == 2
        and agg.arg[0] == "column"
    ):
        return f"{agg.func.lower()}_{agg.arg[1]}"
    return f"{agg.func.lower()}"


# --- Public entry ------------------------------------------------------------

Parser = _Parser


def parse(tokens: list[Token]) -> StatementList:
    """Parse a flat token list into a StatementList AST.

    Pure function: no I/O, no global state. Re-raise ParseError with
    (line, col) pointing at the offending token.
    """
    return _Parser(tokens).parse_statement_list()