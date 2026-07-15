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