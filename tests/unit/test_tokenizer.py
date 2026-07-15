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