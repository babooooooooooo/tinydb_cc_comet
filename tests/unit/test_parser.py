"""Unit tests for tinydb.parser (Tasks 15 + 16).

Task 15 covers CREATE TABLE / DROP TABLE AST shape, duplicate column detection,
unsupported type rejection, and missing-identifier errors per
REQ-PARSE-002 SCN-01/02/03 + REQ-PARSE-003 SCN-01/02.

Task 16 covers INSERT / SELECT / DELETE / StatementList + parser purity per
REQ-PARSE-004/005/006/007/008.

Engine-v1 migration: WHERE tuple assertions (Task 3) were rewritten to
EqualsExpr structural equality, and ``columns == ["*"]`` to ``columns == ("*",)``.
"""
import pytest
from tinydb.parser import parse, CreateTable, Insert, ColumnDefinition, EqualsExpr
from tinydb.tokenizer import tokenize, Token
from tinydb.errors import ParseError


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-002-SCN-01")
def test_parse_create_table_simple():
    stmt = parse(tokenize("CREATE TABLE users (id INT, name TEXT)"))
    assert stmt.statements[0].name == "users"
    assert stmt.statements[0].columns == (
        ColumnDefinition(name="id", type="INT", nullable=True, unique=False, primary_key=False),
        ColumnDefinition(name="name", type="TEXT", nullable=True, unique=False, primary_key=False),
    )


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-002-SCN-02")
def test_parse_create_table_rejects_duplicate_column():
    with pytest.raises(ParseError, match="duplicate column"):
        parse(tokenize("CREATE TABLE t(id INT, id TEXT)"))


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-002-SCN-03")
def test_parse_create_table_rejects_unsupported_type():
    # BLOB is not a registered type — must be rejected. (VARCHAR was the
    # previous fixture but is now a supported parametric type per Task 12.)
    with pytest.raises(ParseError, match="BLOB not supported"):
        parse(tokenize("CREATE TABLE t(id BLOB)"))


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-003-SCN-01")
def test_parse_drop_table():
    stmt = parse(tokenize("DROP TABLE users"))
    assert stmt.statements[0].name == "users"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-003-SCN-02")
def test_parse_drop_table_missing_name_raises():
    with pytest.raises(ParseError, match="expected table name"):
        parse(tokenize("DROP TABLE"))


# --- Task 16: INSERT --------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-004-SCN-01")
def test_parse_insert_single_row():
    stmt = parse(tokenize("INSERT INTO users(id, name) VALUES (1, 'alice')"))
    ins = stmt.statements[0]
    assert ins.table == "users"
    assert ins.columns == ["id", "name"]
    assert ins.values == [[1, "alice"]]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-004-SCN-02")
def test_parse_insert_multi_row():
    stmt = parse(tokenize("INSERT INTO users(id, name) VALUES (1, 'a'), (2, 'b')"))
    assert stmt.statements[0].values == [[1, "a"], [2, "b"]]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-004-SCN-03")
def test_parse_insert_count_mismatch_raises():
    with pytest.raises(ParseError, match="value count mismatch"):
        parse(tokenize("INSERT INTO users(id, name) VALUES (1)"))


# --- Task 16: SELECT --------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-01")
def test_parse_select_star():
    stmt = parse(tokenize("SELECT * FROM users"))
    s = stmt.statements[0]
    assert s.columns == ("*",)
    assert s.table == "users"
    assert s.where is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-03")
def test_parse_select_with_where():
    stmt = parse(tokenize("SELECT * FROM users WHERE id = 1"))
    s = stmt.statements[0]
    assert s.where == EqualsExpr(column="id", value=1)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-04")
def test_parse_select_rejects_unsupported_operator():
    # End-to-end after C-1 governance fix: tokenizer PUNCT set now includes
    # `<>` so `WHERE id > 1` tokenizes successfully and reaches the parser,
    # which raises ParseError for unsupported comparison operators.
    with pytest.raises(ParseError, match=r"operator > not supported"):
        parse(tokenize("SELECT * FROM users WHERE id > 1"))


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-005-SCN-05")
def test_parse_select_missing_from_raises():
    with pytest.raises(ParseError, match="expected FROM"):
        parse(tokenize("SELECT id"))


# --- Task 16: DELETE --------------------------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-006-SCN-01")
def test_parse_delete_all():
    stmt = parse(tokenize("DELETE FROM users"))
    d = stmt.statements[0]
    assert d.table == "users"
    assert d.where is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-006-SCN-02")
def test_parse_delete_with_where():
    stmt = parse(tokenize("DELETE FROM users WHERE id = 1"))
    assert stmt.statements[0].where == EqualsExpr(column="id", value=1)


# --- Task 16: StatementList + purity ---------------------------------------


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-007-SCN-02")
def test_parse_multiple_statements():
    # Two statements separated by `;`. INSERT uses an explicit column list
    # per REQ-PARSE-004 grammar `INSERT INTO table(col, ...) VALUES ...`.
    stmt = parse(tokenize("CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1)"))
    assert len(stmt.statements) == 2
    assert isinstance(stmt.statements[0], CreateTable)
    assert isinstance(stmt.statements[1], Insert)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-008-SCN-01")
def test_parser_is_pure_deterministic():
    sql = "CREATE TABLE t(id INT, name TEXT)"
    a = parse(tokenize(sql))
    b = parse(tokenize(sql))
    assert a.statements[0].columns == b.statements[0].columns


# --- tinydb-join-query (Task 2): FROM/JOIN/ON/USING/NATURAL AST parsing -----


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-001")
def test_parse_table_alias_with_as():
    """`FROM users AS u` produces TableRef(name='users', alias='u') with no joins."""
    from tinydb.parser import Select
    stmt = parse(tokenize("SELECT u.id FROM users AS u"))
    sel = stmt.statements[0]
    assert isinstance(sel, Select)
    assert sel.from_.name == "users"
    assert sel.from_.alias == "u"
    assert sel.joins == ()


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-002")
def test_parse_inner_join_with_on():
    """`INNER JOIN ... ON ...` produces a JoinClause with kind='INNER' and on_expr set."""
    from tinydb.parser import JoinClause, JoinOnPredicate
    stmt = parse(tokenize(
        "SELECT * FROM users u INNER JOIN orders o ON u.id = o.user_id"
    ))
    sel = stmt.statements[0]
    assert len(sel.joins) == 1
    j = sel.joins[0]
    assert isinstance(j, JoinClause)
    assert j.kind == "INNER"
    assert j.right.name == "orders" and j.right.alias == "o"
    assert isinstance(j.on_expr, JoinOnPredicate)
    assert j.using_keys == () and j.natural is False


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-003")
def test_parse_left_outer_join_is_left_kind():
    """`LEFT OUTER JOIN` collapses to kind='LEFT'."""
    stmt = parse(tokenize(
        "SELECT * FROM users LEFT OUTER JOIN orders ON users.id = orders.user_id"
    ))
    j = stmt.statements[0].joins[0]
    assert j.kind == "LEFT"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-004")
def test_parse_using_keys():
    """`JOIN ... USING (id, code)` produces using_keys tuple; on_expr is None."""
    stmt = parse(tokenize(
        "SELECT * FROM users JOIN orders USING (id, code)"
    ))
    j = stmt.statements[0].joins[0]
    assert j.kind == "INNER"
    assert j.using_keys == ("id", "code")
    assert j.on_expr is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-005")
def test_parse_natural_left_join():
    """`NATURAL LEFT JOIN` sets natural=True; no on_expr / no using_keys."""
    stmt = parse(tokenize("SELECT * FROM users NATURAL LEFT JOIN profiles"))
    j = stmt.statements[0].joins[0]
    assert j.kind == "LEFT"
    assert j.natural is True
    assert j.on_expr is None and j.using_keys == ()


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-006")
def test_parse_chained_multi_joins():
    """`a JOIN b ... JOIN c ...` produces two JoinClauses with right names 'b' and 'c'."""
    stmt = parse(tokenize(
        "SELECT * FROM a JOIN b ON a.id = b.aid JOIN c ON b.id = c.bid"
    ))
    sel = stmt.statements[0]
    assert len(sel.joins) == 2
    assert sel.joins[0].right.name == "b"
    assert sel.joins[1].right.name == "c"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-007")
def test_parse_qualified_column_in_select_and_where():
    """`SELECT u.id ... WHERE u.id = 1` retains qualifier on SelectItem and EqualsExpr."""
    stmt = parse(tokenize("SELECT u.id FROM users u WHERE u.id = 1"))
    sel = stmt.statements[0]
    first_item = sel.select_items[0]
    assert first_item.kind == "column"
    # qualifier is exposed on SelectItem via the T2 extension
    assert getattr(first_item, "qualifier", None) == "u"
    assert first_item.name == "id"
    # EqualsExpr.qualifier carries the qualifier
    assert sel.where is not None
    assert getattr(sel.where, "qualifier", None) == "u"
    assert sel.where.column == "id"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-008")
def test_parse_join_without_on_or_using_raises():
    """`FROM a JOIN b` (no key clause) raises ParseError pointing at JOIN keyword."""
    with pytest.raises(ParseError) as exc:
        parse(tokenize("SELECT * FROM users JOIN orders"))
    # Position must point to the JOIN keyword (line >= 1); message mentions ON or USING.
    assert exc.value.line >= 1
    assert "ON" in str(exc.value) or "USING" in str(exc.value)


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-009")
def test_parse_cross_join_does_not_require_key():
    """`CROSS JOIN` requires no ON/USING; kind='CROSS'."""
    stmt = parse(tokenize("SELECT * FROM users CROSS JOIN orders"))
    j = stmt.statements[0].joins[0]
    assert j.kind == "CROSS"
    assert j.on_expr is None and j.using_keys == () and j.natural is False


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-010")
def test_parse_existing_single_table_select_unchanged():
    """Regression: single-table SELECT still parses with joins=() and from_ set."""
    stmt = parse(tokenize(
        "SELECT id, name FROM users WHERE id = 1 ORDER BY id LIMIT 5"
    ))
    sel = stmt.statements[0]
    assert sel.from_.name == "users" and sel.from_.alias is None
    assert sel.joins == ()


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-011")
def test_parse_full_outer_join_is_full_kind():
    """`FULL OUTER JOIN` collapses to kind='FULL'."""
    stmt = parse(tokenize(
        "SELECT * FROM users FULL OUTER JOIN orders ON users.id = orders.user_id"
    ))
    j = stmt.statements[0].joins[0]
    assert j.kind == "FULL"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-012")
def test_parse_right_join_is_right_kind():
    """`RIGHT JOIN` keeps kind='RIGHT'."""
    stmt = parse(tokenize(
        "SELECT * FROM users RIGHT JOIN orders ON users.id = orders.user_id"
    ))
    j = stmt.statements[0].joins[0]
    assert j.kind == "RIGHT"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-013")
def test_parse_table_ref_without_alias_has_none():
    """`FROM users` (no AS) yields TableRef with alias=None."""
    stmt = parse(tokenize("SELECT id FROM users"))
    sel = stmt.statements[0]
    assert sel.from_.alias is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-JOIN-PARSE-014")
def test_parse_join_on_with_complex_predicate_raises():
    """Task 2 scope: ON predicate must be a column comparison. AND / OR / NOT
    triggers a ParseError hinting that compound predicates are deferred to Task 8."""
    with pytest.raises(ParseError):
        parse(tokenize(
            "SELECT * FROM users u LEFT JOIN orders o "
            "ON u.id = o.user_id AND o.total > 10"
        ))