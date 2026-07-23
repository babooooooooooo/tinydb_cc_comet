"""Unit tests for tinydb.tokenizer (Task 13).

Covers identifier, keyword (case-insensitive), punctuation, and TokenError
position reporting per REQ-PARSE-001 SCN-01/02/05/06.
"""
import pytest
from tinydb.tokenizer import tokenize
from tinydb.errors import TokenError

KEYWORDS = {"CREATE", "TABLE", "DROP", "INSERT", "INTO", "VALUES", "SELECT",
            "FROM", "WHERE", "TRUE", "FALSE", "INT", "TEXT", "FLOAT", "BOOL"}


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-01")
def test_tokenize_identifier():
    toks = tokenize("users")
    assert len(toks) == 2  # value + EOF
    t = toks[0]
    assert t.type == "IDENT" and t.value == "users" and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-02")
def test_tokenize_keyword_case_insensitive():
    for variant in ("CREATE", "create", "Create"):
        toks = tokenize(variant)
        assert toks[0].type == "KEYWORD" and toks[0].value == "CREATE"


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-05")
def test_tokenize_punctuation():
    toks = tokenize("( ) , ; = *")
    puncts = [t.value for t in toks if t.type == "PUNCT"]
    assert puncts == ["(", ")", ",", ";", "=", "*"]


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-05")
def test_tokenize_punctuation_comparison_ops():
    # Regression for C-1 governance: PUNCT extended to include `<` and `>`
    # so spec §REQ-PARSE-005-SCN-04 (WHERE id > 1) can tokenize end-to-end
    # and reach the parser for unsupported-operator check.
    toks = tokenize("WHERE id < 5")
    puncts = [t.value for t in toks if t.type == "PUNCT"]
    assert "<" in puncts and ">" not in puncts
    toks = tokenize("WHERE id > 5")
    puncts = [t.value for t in toks if t.type == "PUNCT"]
    assert ">" in puncts and "<" not in puncts


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-06")
def test_tokenizer_error_reports_position():
    with pytest.raises(TokenError) as excinfo:
        tokenize("@")
    assert excinfo.value.line == 1
    assert excinfo.value.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-03")
def test_tokenize_int_literal():
    toks = tokenize("42")
    assert len(toks) == 2  # INT + EOF
    t = toks[0]
    assert t.type == "INT" and t.value == 42 and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-04")
def test_tokenize_float_literal():
    toks = tokenize("3.14")
    assert len(toks) == 2  # FLOAT + EOF
    t = toks[0]
    assert t.type == "FLOAT" and t.value == 3.14 and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-07")
def test_tokenize_text_literal_simple():
    toks = tokenize("'hello world'")
    assert len(toks) == 2  # TEXT + EOF
    t = toks[0]
    assert t.type == "TEXT" and t.value == "hello world" and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-08")
def test_tokenize_text_literal_doubled_quote():
    toks = tokenize("'it''s ok'")
    assert len(toks) == 2  # TEXT + EOF
    t = toks[0]
    assert t.type == "TEXT" and t.value == "it's ok" and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-09")
def test_tokenize_text_literal_consecutive_quotes():
    # Regression for C-1: six consecutive single quotes encode two literal quotes.
    # Previous bug: scanner folded ''->' then parse_text_literal folded again,
    # turning "''" into "'" instead of leaving it as "''".
    toks = tokenize("''''''")
    assert len(toks) == 2  # TEXT + EOF
    t = toks[0]
    assert t.type == "TEXT" and t.value == "''" and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-10")
def test_tokenize_true_false_bool():
    for src, expected in (("TRUE", True), ("FALSE", False), ("true", True), ("False", False)):
        toks = tokenize(src)
        assert len(toks) == 2  # BOOL + EOF
        t = toks[0]
        assert t.type == "BOOL" and t.value == expected and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-11")
def test_tokenize_unterminated_text_raises():
    with pytest.raises(TokenError) as excinfo:
        tokenize("'abc")
    assert "unterminated" in str(excinfo.value)
    assert excinfo.value.line == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-12")
def test_tokenize_empty_input():
    toks = tokenize("")
    assert len(toks) == 1
    assert toks[0].type == "EOF" and toks[0].value is None


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-13")
def test_tokenize_whitespace_only():
    toks = tokenize("   \n  \t  ")
    assert len(toks) == 1
    assert toks[0].type == "EOF"
    # Trailing whitespace cursor advances line/col, but EOF is still the only token.
    assert toks[0].line == 2 and toks[0].col == 6


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-14")
def test_tokenize_multiline_position():
    # "a\n  b" yields:
    #   line 1, col 1: IDENT 'a'
    #   line 2, col 3: IDENT 'b'
    toks = tokenize("a\n  b")
    non_eof = [t for t in toks if t.type != "EOF"]
    assert [t.type for t in non_eof] == ["IDENT", "IDENT"]
    assert non_eof[0].value == "a" and non_eof[0].line == 1 and non_eof[0].col == 1
    assert non_eof[1].value == "b" and non_eof[1].line == 2 and non_eof[1].col == 3


@pytest.mark.unit
@pytest.mark.spec_id("REQ-TYPE-001-SCN-02")
def test_tokenize_int_negative():
    toks = tokenize("-7")
    assert len(toks) == 2  # INT + EOF
    t = toks[0]
    assert t.type == "INT" and t.value == -7 and t.line == 1 and t.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-TYPE-001-SCN-07")
def test_tokenize_float_NaN_raises_TokenError():
    with pytest.raises(TokenError) as excinfo:
        tokenize("NaN")
    msg = str(excinfo.value)
    assert "NaN not allowed" in msg or "NaN" in msg
    assert excinfo.value.line == 1 and excinfo.value.col == 1


@pytest.mark.unit
@pytest.mark.spec_id("REQ-PARSE-001-SCN-15")
def test_tokenize_delete_keyword():
    # Regression for Task 13 plan bug: KEYWORDS list omitted DELETE.
    # Parser §Task 16 depends on DELETE being recognized as KEYWORD (not IDENT),
    # otherwise DELETE parsing would fail at tokenize stage instead of parse stage.
    toks = tokenize("DELETE")
    assert len(toks) == 2  # KEYWORD + EOF
    t = toks[0]
    assert t.type == "KEYWORD" and t.value == "DELETE" and t.line == 1 and t.col == 1


@pytest.mark.unit
def test_tokenizer_recognizes_not_null_primary_key_unique_as_keywords():
    sql = "CREATE TABLE t(id INT NOT NULL PRIMARY KEY, email TEXT UNIQUE)"
    tokens = tokenize(sql)
    keywords = [t.value for t in tokens if t.type == "KEYWORD"]
    assert "NOT" in keywords
    assert "NULL" in keywords
    assert "PRIMARY" in keywords
    assert "KEY" in keywords
    assert "UNIQUE" in keywords


@pytest.mark.unit
def test_tokenizer_does_not_treat_null_as_ident():
    # NULL should now be a KEYWORD, not an IDENT.
    tokens = tokenize("SELECT NULL FROM t")
    null_tokens = [t for t in tokens if t.value == "NULL"]
    assert len(null_tokens) == 1
    assert null_tokens[0].type == "KEYWORD"


# --- tinydb-join-query (Task 1): JOIN / OUTER / CROSS / ON / USING / NATURAL ---
# Per Design Doc §5.1, the tokenizer must emit the new JOIN-family keywords
# as KEYWORD tokens and must emit '.' as a PUNCT token. Regression tests for
# the existing single-table keyword / literal recognition are also included.


@pytest.mark.unit
def test_tokenize_join_keywords_are_recognized():
    tokens = tokenize("SELECT * FROM a JOIN b ON a.id = b.id")
    kw_values = [t.value for t in tokens if t.type == "KEYWORD"]
    assert "JOIN" in kw_values
    assert "ON" in kw_values
    # Case-insensitive: lowercase 'join' must still tokenize as KEYWORD 'JOIN'
    tokens_lc = tokenize("select * from a join b")
    assert any(
        t.type == "KEYWORD" and t.value == "JOIN"
        for t in tokens_lc
    ), "lowercase 'join' should be recognized as KEYWORD 'JOIN'"


@pytest.mark.unit
def test_tokenize_all_join_kind_keywords():
    for kw in ("INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "USING", "NATURAL"):
        tokens = tokenize(f"SELECT * FROM a {kw} JOIN b")
        assert any(
            t.type == "KEYWORD" and t.value == kw for t in tokens
        ), f"{kw} missing from keyword stream"


@pytest.mark.unit
def test_tokenize_dot_punctuation_for_qualified_columns():
    tokens = tokenize("SELECT u.id FROM users u")
    dots = [t for t in tokens if t.type == "PUNCT" and t.value == "."]
    assert len(dots) == 1, f"expected exactly one '.' PUNCT, got {len(dots)}"
    # "SELECT u.id": S(1) E(2) L(3) E(4) C(5) T(6) ' '(7) u(8) .(9) i(10) d(11)
    assert dots[0].line == 1 and dots[0].col == 9


@pytest.mark.unit
def test_tokenize_consecutive_dots_emit_two_puncts():
    """Per Design Doc §5.1, the tokenizer must accept '.' as PUNCT. Two
    consecutive '.'s emit two PUNCT tokens — the parser (Task 2) is
    responsible for the semantic 'consecutive dots are illegal' check."""
    tokens = tokenize("SELECT u..id FROM t")
    dots = [t for t in tokens if t.type == "PUNCT" and t.value == "."]
    assert len(dots) == 2
    idents = [t.value for t in tokens if t.type == "IDENT"]
    assert "u" in idents and "id" in idents


@pytest.mark.unit
def test_tokenize_leading_dot_emits_punct():
    """'.id' starts with '.' which is now a valid PUNCT, followed by
    IDENT('id'). The tokenizer must not raise; the parser rejects this
    qualified-name pattern."""
    tokens = tokenize("SELECT .id FROM t")
    dots = [t for t in tokens if t.type == "PUNCT" and t.value == "."]
    assert len(dots) == 1
    assert any(t.type == "IDENT" and t.value == "id" for t in tokens)


@pytest.mark.unit
def test_tokenize_trailing_dot_emits_punct():
    """'u.' scans as IDENT(u) + PUNCT(.) — also a parser-rejected form
    (empty column name after qualifier) but the tokenizer must accept it."""
    tokens = tokenize("SELECT u. FROM t")
    dots = [t for t in tokens if t.type == "PUNCT" and t.value == "."]
    assert len(dots) == 1


@pytest.mark.unit
def test_tokenize_preserves_existing_keywords_and_literals():
    """Regression: FROM / WHERE / SELECT / TEXT / INT keywords and string /
    int literals must remain unchanged after the JOIN keyword additions."""
    tokens = tokenize("SELECT 'abc', 123 FROM t WHERE id = 1")
    assert any(t.type == "TEXT" and t.value == "abc" for t in tokens)
    assert any(t.type == "INT" and t.value == 123 for t in tokens)
    assert any(t.type == "KEYWORD" and t.value == "SELECT" for t in tokens)
    assert any(t.type == "KEYWORD" and t.value == "FROM" for t in tokens)
    assert any(t.type == "KEYWORD" and t.value == "WHERE" for t in tokens)


@pytest.mark.unit
def test_tokenize_dot_inside_float_literal_is_not_punct():
    """Regression: 3.14 must remain a single FLOAT token; the embedded '.'
    must NOT be emitted as a separate PUNCT. The float-literal branch in the
    tokenizer consumes the '.' greedily before reaching the PUNCT branch."""
    tokens = tokenize("SELECT 3.14 FROM t")
    floats = [t for t in tokens if t.type == "FLOAT"]
    assert len(floats) == 1 and floats[0].value == 3.14
    # No '.' PUNCT in the stream when the only '.' is inside a float literal.
    dots = [t for t in tokens if t.type == "PUNCT" and t.value == "."]
    assert dots == []


@pytest.mark.unit
def test_tokenize_as_keyword_is_recognized():
    """AS is the alias introducer (Task 2); tokenizer must emit KEYWORD."""
    tokens = tokenize("SELECT * FROM users AS u")
    assert any(t.type == "KEYWORD" and t.value == "AS" for t in tokens)
