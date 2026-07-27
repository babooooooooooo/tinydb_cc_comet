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

    Engine-v1: columns is tuple, where holds Expr, order_by/limit/offset
    default to empty/None for backward compat with MVP instances.

    Aggregation extension: select_items / group_by / having /
    aggregate_aliases trigger the 5-phase aggregation pipeline.

    tinydb-join-query (T2): ``from_`` is the FROM ``TableRef``; ``joins``
    is 0..N ``JoinClause`` in left-deep order. ``table`` equals
    ``from_.name`` for backward compat with v0.1 single-table executors.
    """

    table: str
    columns: tuple
    where: Optional[Any] = None
    order_by: tuple = ()
    limit: Optional[int] = None
    offset: Optional[int] = None
    # --- tinydb-aggregation (T2) ---
    select_items: tuple = ()
    group_by: tuple = ()
    having: Optional[object] = None
    aggregate_aliases: tuple = ()
    # --- tinydb-join-query (T2) ---
    from_: Optional["TableRef"] = None
    joins: tuple = ()
    line: int = 0
    col: int = 0


# --- tinydb-join-query (T2): FROM / JOIN AST nodes -------------------------


@dataclass(frozen=True)
class TableRef:
    """FROM / JOIN 子句中的表引用。"""

    name: str
    alias: Optional[str] = None
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class JoinKey:
    """USING / NATURAL 等值键（resolver 阶段构造；parser 阶段不出现）。

    字段顺序遵循 Design Doc §5.1：位置在前，标签 / source 在后。
    """

    left_col: int
    right_col: int
    label: str
    source_left: str
    source_right: str


@dataclass(frozen=True)
class JoinOnPredicate:
    """JOIN ON 后的基础列对列比较（Task 2 范围；Task 8 扩展为复合 AND/OR/NOT）。"""

    left: "ColumnRef"
    op: str  # one of '=' '<' '>' '<=' '>=' '!='
    right: "ColumnRef"
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class JoinClause:
    """FROM 后追加的 JOIN 子句。

    ``kind`` 是 ``INNER`` / ``LEFT`` / ``RIGHT`` / ``FULL`` / ``CROSS``；裸 JOIN
    与 ``LEFT/RIGHT/FULL OUTER JOIN`` 分别规范化为 ``INNER`` / ``LEFT|RIGHT|FULL``。
    """

    kind: str
    right: TableRef
    on_expr: Optional[Any] = None
    using_keys: tuple = ()  # tuple[str, ...]
    natural: bool = False
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class ColumnRef:
    """限定列引用；``qualifier`` 为 ``None`` 表示裸列。"""

    qualifier: Optional[str]
    name: str
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
    """MVP-compatible: ``col = literal`` comparison.

    tinydb-join-query (T2): ``qualifier`` carries the optional table alias
    prefix when the column reference is qualified (e.g. ``u.id = 1``).
    Defaults to ``None`` so legacy single-table tests keep working.
    Equality is purely structural on (column, value, qualifier) — no
    source-position fields, matching pre-T2 expectations.
    """

    column: str
    value: Any
    qualifier: Optional[str] = None


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
    """ORDER BY item: column + ASC/DESC.

    tinydb-join-query (T2): ``qualifier`` carries optional table alias for
    qualified references (e.g. ``ORDER BY u.id``). Defaults to ``None``.
    """

    column: str
    descending: bool = False
    qualifier: Optional[str] = None


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

    tinydb-join-query (T2): ``qualifier`` is set when the projection column
    is qualified (e.g. ``SELECT u.id``); defaults to ``None``.
    """

    kind: str                          # 'star' | 'column' | 'aggregate'
    name: Optional[str] = None         # column name (column kind)
    alias: Optional[str] = None        # explicit alias (column kind, or aggregate alias)
    aggregate: Optional[AggregateCall] = None  # aggregate detail (aggregate kind)
    qualifier: Optional[str] = None    # tinydb-join-query (T2): optional table alias


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
        # Source-position of the most recently parsed column name. Set by
        # _parse_column_definition and read by _parse_column_list when a
        # duplicate column name surfaces, so the reported error position is
        # the duplicate token (matches pre-refactor behavior).
        self._last_col_tok: Optional[Token] = None

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
            if self._peek_punct(";"):
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
        """Parse ``CREATE TABLE name (col_def [, col_def ...])``.

        Thin dispatcher: validates the CREATE TABLE header and the table
        name, then delegates to :meth:`_parse_column_list` for the comma-
        separated column definitions. Returns a ``CreateTable`` AST node.
        """
        kw = self.expect_keyword("CREATE")
        self.expect_keyword("TABLE")

        # table name (must be IDENT — keywords like "TABLE" do not qualify)
        name_tok = self.peek()
        if name_tok.type != "IDENT":
            raise ParseError(name_tok.line, name_tok.col, "expected table name")
        name = self.advance().value

        self.expect("PUNCT", "(")
        cols = self._parse_column_list()
        self.expect("PUNCT", ")")

        return CreateTable(
            name=name, columns=tuple(cols),
            line=kw.line, col=kw.col,
        )

    def _parse_column_list(self) -> list[ColumnDefinition]:
        """Parse a comma-separated list of column definitions until ``)``.

        Empty list is rejected (``CREATE TABLE t ()`` is invalid). Each
        column is parsed by :meth:`_parse_column_definition`; duplicate
        column names are surfaced before the second definition is added so
        the reported position is the duplicate.
        """
        # Empty column list: `CREATE TABLE t ()` is invalid.
        if self._peek_punct(")"):
            tok = self.peek()
            raise ParseError(tok.line, tok.col, "expected column name")

        cols: list[ColumnDefinition] = []
        seen: set = set()
        while True:
            col = self._parse_column_definition()
            if col.name in seen:
                # Position reported is the duplicate token at the start of
                # the offending column, matching pre-refactor behavior.
                raise ParseError(
                    self._last_col_tok.line, self._last_col_tok.col,
                    f"duplicate column {col.name}",
                )
            seen.add(col.name)
            cols.append(col)
            if self._peek_punct(","):
                self.advance()
                continue
            break
        return cols

    def _parse_column_definition(self) -> ColumnDefinition:
        """Parse a single column: ``name type[(params)] [constraints...]``.

        Delegates the type-param suffix to :meth:`_parse_type_params` and
        the trailing constraint clauses to :meth:`_parse_column_constraints`.
        """
        # Column name (IDENT).
        col_tok = self.peek()
        if col_tok.type != "IDENT":
            raise ParseError(col_tok.line, col_tok.col, "expected column name")
        cname = self.advance().value
        self._last_col_tok = col_tok  # for duplicate-name error positioning

        # Type name (KEYWORD or IDENT; case-insensitive upper).
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

        nullable, unique, primary_key = self._parse_column_constraints()

        return ColumnDefinition(
            name=cname, type=type_name, type_params=type_params,
            nullable=nullable, unique=unique, primary_key=primary_key,
        )

    def _parse_column_constraints(self) -> tuple[bool, bool, bool]:
        """Parse trailing constraint clauses: ``NOT NULL`` / ``UNIQUE`` / ``PRIMARY KEY``.

        Returns ``(nullable, unique, primary_key)``. Order-independent;
        multiple clauses allowed on one column. Duplicates of any one
        clause are rejected. Bare ``NULL`` (without leading ``NOT``) and
        bare ``KEY`` (without leading ``PRIMARY``) are rejected.
        """
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
        return nullable, unique, primary_key

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
        has_paren = self._peek_punct("(")
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
        if self._peek_punct(","):
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
            if self._peek_punct(","):
                self.advance()
                continue
            break
        self.expect("PUNCT", ")")

        self.expect_keyword("VALUES")

        values: list = []
        while True:
            self.expect("PUNCT", "(")
            row: list = []
            if self._peek_punct(")"):
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
                if self._peek_punct(","):
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
            if self._peek_punct(","):
                self.advance()
                continue
            break

        return Insert(
            table=table, columns=cols, values=values,
            line=kw.line, col=kw.col,
        )

    # --- SELECT [cols] FROM <table> [WHERE ...] ----------------------------

    def _parse_select(self) -> Select:
        """Parse SELECT statement; populates ``from_`` and ``joins`` (T2)."""
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

        # tinydb-join-query (T2): FROM <table_ref> + JOIN chain.
        from_ref = self._parse_table_ref()
        joins = self._parse_join_chain()

        # Legacy ``table`` field kept for v0.1 single-table executors.
        table = from_ref.name

        where = self._parse_where()

        # GROUP BY (optional, aggregation only)
        group_by = ()
        if self._peek_kw("GROUP"):
            self.expect_keyword("GROUP")
            self.expect_keyword("BY")
            group_by = self._parse_col_list()

        # HAVING (optional, aggregation only)
        having = None
        if self._peek_kw("HAVING"):
            self.expect_keyword("HAVING")
            having = self._parse_having_expr()

        order_by = self._parse_order_by()
        limit = self._parse_limit()
        offset = self._parse_offset()

        # Cached alias list for the executor's HAVING/ORDER evaluation.
        aggregate_aliases = tuple(
            si.alias or default_alias(si.aggregate)
            for si in items if si.kind == "aggregate"
        )

        # Legacy columns field for backward compat / database.Row wrapping.
        legacy_cols: list = []
        for si in items:
            if si.kind == "star":
                legacy_cols = ["*"]
                break
            if si.kind == "aggregate":
                legacy_cols.append(si.alias or default_alias(si.aggregate))
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
            # --- tinydb-join-query (T2) ---
            from_=from_ref,
            joins=joins,
        )

    # --- tinydb-join-query (T2): FROM / JOIN helpers ------------------------

    def _parse_table_ref(self) -> TableRef:
        """Parse ``IDENT [AS IDENT | IDENT]`` after FROM or JOIN.

        接受三种形式：``FROM users`` / ``FROM users AS u`` / ``FROM users u``。
        隐式 alias（无 ``AS``）是常见 SQL 习惯。``TableRef.line / col`` 指向
        ``name`` token。
        """
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected table name")
        name_tok = self.advance()
        alias = None
        if self._peek_kw("AS"):
            # 显式 AS <ident>
            self.advance()
            a = self.peek()
            if a.type != "IDENT":
                raise ParseError(a.line, a.col, "expected alias after AS")
            alias = self.advance().value
        elif (
            not self.at_end()
            and self.peek().type == "IDENT"
        ):
            # 隐式 alias：``FROM users u``。下一 token 是 IDENT（不是
            # KEYWORD），故不会误吃 JOIN/INNER/LEFT 等。
            alias = self.advance().value
        return TableRef(name=name_tok.value, alias=alias,
                        line=name_tok.line, col=name_tok.col)

    def _parse_join_chain(self) -> tuple:
        """Parse zero or more JOIN clauses until a non-JOIN keyword.

        返回 left-deep 顺序的 ``JoinClause`` 元组；NATURAL 前缀由
        ``_parse_join_clause`` 内部消费。
        """
        joins: list = []
        while self._peek_join_start():
            joins.append(self._parse_join_clause())
        return tuple(joins)

    def _peek_join_start(self) -> bool:
        """True if the next token could begin a JOIN clause (含 NATURAL 前缀)。"""
        t = self.peek()
        return (
            t.type == "KEYWORD"
            and t.value in {
                "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "NATURAL",
            }
        )

    def _parse_join_clause(self) -> JoinClause:
        """Parse ``[NATURAL] [kind] JOIN right [ON p | USING (cols)]``.

        ``first_tok`` 在 NATURAL 已 consume 时指向 NATURAL，否则指向 JOIN 关
        键字；用于错误位置与 ``JoinClause.line / col``。
        """
        first_tok = self.peek()
        natural = False
        if first_tok.type == "KEYWORD" and first_tok.value == "NATURAL":
            self.advance()
            natural = True
            kind_tok = self.peek()
        else:
            kind_tok = first_tok

        if kind_tok.type == "KEYWORD" and kind_tok.value in {
            "INNER", "LEFT", "RIGHT", "FULL", "CROSS",
        }:
            kind = self.advance().value
            if kind in {"LEFT", "RIGHT", "FULL"} and self._peek_kw("OUTER"):
                self.advance()
            if not self._peek_kw("JOIN"):
                tok = self.peek()
                raise ParseError(
                    tok.line, tok.col, "expected JOIN after join kind",
                )
            self.advance()
        elif kind_tok.type == "KEYWORD" and kind_tok.value == "JOIN":
            kind = "INNER"
            self.advance()
        else:
            raise ParseError(
                first_tok.line, first_tok.col,
                "expected JOIN keyword",
            )

        right = self._parse_table_ref()

        on_expr = None
        using_keys: tuple = ()
        if self._peek_kw("USING"):
            self.advance()
            self.expect("PUNCT", "(")
            keys: list = []
            while True:
                c = self.peek()
                if c.type != "IDENT":
                    raise ParseError(c.line, c.col, "expected column in USING")
                keys.append(self.advance().value)
                if self._peek_punct(","):
                    self.advance()
                    continue
                break
            self.expect("PUNCT", ")")
            using_keys = tuple(keys)
        elif self._peek_kw("ON"):
            self.advance()
            on_expr = self._parse_join_predicate()
        elif kind != "CROSS" and not natural:
            # 缺键错误：位置指向 first_tok（NATURAL 或 JOIN 关键字）
            raise ParseError(
                first_tok.line, first_tok.col,
                "JOIN requires ON or USING clause (or NATURAL)",
            )

        return JoinClause(
            kind=kind, right=right, on_expr=on_expr,
            using_keys=using_keys, natural=natural,
            line=first_tok.line, col=first_tok.col,
        )

    def _parse_join_predicate(self) -> JoinOnPredicate:
        """Parse JOIN ON 后基础列对列比较（Task 2 范围；Task 8 扩展 AND/OR/NOT）。"""
        left = self._parse_qualified_column_ref()
        op_tok = self.peek()
        if op_tok.type != "PUNCT" or op_tok.value not in {
            "=", "<", ">", "<=", ">=", "!=",
        }:
            raise ParseError(
                op_tok.line, op_tok.col,
                "JOIN ON predicate must start with column comparison "
                "(complex AND/OR expressions deferred to Task 8)",
            )
        self.advance()
        right = self._parse_qualified_column_ref()
        return JoinOnPredicate(
            left=left, op=op_tok.value, right=right,
            line=left.line, col=left.col,
        )

    def _parse_qualified_column_ref(self) -> ColumnRef:
        """Parse ``qualifier.column`` or bare ``column`` -> ``ColumnRef``.

        Used by ``_parse_join_predicate`` for ON operands; canonical
        column-reference form for resolver / plan layers.
        """
        t = self.peek()
        if t.type != "IDENT":
            raise ParseError(t.line, t.col, "expected column name")
        first_tok = self.advance()
        if self._peek_punct("."):
            self.advance()
            cn = self.peek()
            if cn.type != "IDENT":
                raise ParseError(
                    cn.line, cn.col, "expected column after '.'",
                )
            return ColumnRef(
                qualifier=first_tok.value, name=self.advance().value,
                line=t.line, col=t.col,
            )
        return ColumnRef(
            qualifier=None, name=first_tok.value,
            line=first_tok.line, col=first_tok.col,
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
            col_ref = self._parse_qualified_column_ref()
            desc = False
            if self._peek_kw("ASC"):
                self.advance()
            elif self._peek_kw("DESC"):
                self.advance()
                desc = True
            items.append(OrderByItem(
                column=col_ref.name,
                descending=desc,
                qualifier=col_ref.qualifier,
            ))
            if self._peek_punct(","):
                self.advance()
                continue
            break
        return tuple(items)

    def _parse_int_kw_clause(self, kw: str, *, non_negative: bool) -> Optional[int]:
        """Parse ``KW <int>`` if present; otherwise return ``None``.

        Shared by ``LIMIT`` (non-negative is required) and ``OFFSET`` (the
        executor rejects negatives later; parser just requires an INT
        literal). The ``non_negative`` flag toggles the explicit check at
        parse time vs deferred validation — LIMIT carries the check
        because a negative limit is more likely a programmer bug than a
        typed-in-the-dark mistake.
        """
        if not self._peek_kw(kw):
            return None
        self.advance()
        t = self.advance()
        if t.type != "INT":
            raise ParseError(
                t.line, t.col, f"{kw} must be a non-negative integer",
            )
        if non_negative and t.value < 0:
            raise ParseError(t.line, t.col, f"{kw} must be non-negative")
        return int(t.value)

    def _parse_limit(self) -> Optional[int]:
        return self._parse_int_kw_clause("LIMIT", non_negative=True)

    def _parse_offset(self) -> Optional[int]:
        return self._parse_int_kw_clause("OFFSET", non_negative=False)

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
            val = self._parse_literal_value()
            sets.append((col, EqualsExpr(column=col, value=val)))
            if self._peek_punct(","):
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
        if not self._peek_kw("WHERE"):
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
        col_ref = self._parse_qualified_column_ref()
        op_tok = self.advance()
        if op_tok.type != "PUNCT" or op_tok.value not in SUPPORTED_OPS:
            op_repr = op_tok.value if op_tok.type != "EOF" else "EOF"
            raise ParseError(
                op_tok.line, op_tok.col,
                f"operator {op_repr} not supported; MVP supports only =",
            )
        lit_val = self._parse_literal_value()
        return EqualsExpr(
            column=col_ref.name, value=lit_val, qualifier=col_ref.qualifier,
        )

    def _parse_literal_value(self):
        """Dispatch the next token to a literal decoder.

        Handles the three literal forms the parser produces:
          * DATETIME-keyword prefix (``DATE '...'`` / ``TIME '...'`` /
            ``TIMESTAMP '...'``) — delegates to ``_parse_datetime_literal``
          * ``DECIMAL '...'`` prefix — delegates to ``_parse_decimal_literal``
          * bare literal (``INT`` / ``FLOAT`` / ``TEXT`` / ``BOOL``) — returns
            the token's value as-is

        Used by both ``_parse_comparison`` and the UPDATE SET clause so
        the datetime / decimal / bare-literal discrimination lives in one
        place.
        """
        if (self.peek().type == "KEYWORD"
                and self.peek().value in _DATETIME_KEYWORDS):
            return self._parse_datetime_literal()
        if (self.peek().type == "KEYWORD"
                and self.peek().value == "DECIMAL"):
            return self._parse_decimal_literal()
        lit = self.advance()
        if lit.type not in _LITERAL_TYPES:
            raise ParseError(lit.line, lit.col, "expected literal")
        return lit.value

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
        if self._peek_punct("*"):
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

            if self._peek_punct(","):
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
        # tinydb-join-query (T2): recognise ``qualifier.column`` form via the
        # canonical helper so ORDER BY / WHERE / SELECT / GROUP BY share the
        # same IDENT [ . IDENT ] parser.
        col_ref = self._parse_qualified_column_ref()
        name = col_ref.name
        qualifier = col_ref.qualifier
        alias = None
        if self._is_keyword(self.peek(), "AS"):
            self.advance()
            ident = self.peek()
            if ident.type != "IDENT":
                raise ParseError(ident.line, ident.col, "expected alias after AS")
            alias = self.advance().value
        return SelectItem(
            kind="column", name=name, alias=alias, qualifier=qualifier,
        )

    def _parse_aggregate_call(self) -> AggregateCall:
        """Parse COUNT(*) or an aggregate over a bare/qualified column."""
        func_tok = self.peek()
        func = self.advance().value
        self.expect("PUNCT", "(")
        if self._peek_punct("*"):
            self.advance()
            arg: object = "*"
        else:
            # qualified / bare column via the shared helper.
            col_ref = self._parse_qualified_column_ref()
            if col_ref.qualifier is None:
                arg = ("column", col_ref.name)
            else:
                arg = ("column", col_ref.qualifier, col_ref.name)
        self.expect("PUNCT", ")")
        return AggregateCall(func=func, arg=arg, line=func_tok.line, col=func_tok.col)

    def _parse_col_list(self) -> tuple:
        """Parse comma-separated IDENT list for GROUP BY.

        tinydb-join-query (T2): accepts ``qualifier.column`` form, emitted
        as the literal string ``"qualifier.column"`` so the existing
        ``group_by: tuple[str, ...]`` shape is preserved. Resolver splits
        the qualifier back out when building the join plan.
        """
        cols: list = []
        while True:
            col_ref = self._parse_qualified_column_ref()
            if col_ref.qualifier is None:
                cols.append(col_ref.name)
            else:
                cols.append(f"{col_ref.qualifier}.{col_ref.name}")
            if self._peek_punct(","):
                self.advance()
                continue
            break
        return tuple(cols)

    def _parse_having_expr(self):
        """Parse HAVING ``aggregate-or-column operator literal``."""
        if self._is_keyword(self.peek(), "COUNT", "SUM", "AVG", "MIN", "MAX"):
            left = self._parse_aggregate_call()
        else:
            ct = self.peek()
            if ct.type != "IDENT":
                raise ParseError(ct.line, ct.col, "expected column in HAVING")
            left = self.advance().value

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
        return (left, op_tok.value, lit.value)

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


def default_alias(agg: AggregateCall) -> str:
    """Default aggregate alias per design doc (T2).

    Exposed at module scope so the executor's aggregate-row projection
    can reuse the same rule instead of reimplementing it.

    Rules:
      - ``COUNT(*)`` -> ``"count"``
      - any aggregate with a column arg -> ``"<func>_<col>"`` lowercased
      - fallback (rare): ``<func>`` lowercased
    """
    if agg.arg == "*":
        return "count"
    if (
        isinstance(agg.arg, tuple)
        and len(agg.arg) in (2, 3)
        and agg.arg[0] == "column"
    ):
        return f"{agg.func.lower()}_{agg.arg[-1]}"
    return f"{agg.func.lower()}"


# --- Public entry ------------------------------------------------------------

Parser = _Parser


def parse(tokens: list[Token]) -> StatementList:
    """Parse a flat token list into a StatementList AST.

    Pure function: no I/O, no global state. Re-raise ParseError with
    (line, col) pointing at the offending token.
    """
    return _Parser(tokens).parse_statement_list()