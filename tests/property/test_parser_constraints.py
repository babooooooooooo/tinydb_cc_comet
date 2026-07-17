"""Property test: random CREATE TABLE constraint clause combinations never crash."""
from hypothesis import given, seed, settings
import hypothesis.strategies as st
import pytest

from tinydb.parser import parse
from tinydb.tokenizer import tokenize
from tinydb.errors import ParseError, TokenError

pytestmark = pytest.mark.property

_ALLOWED = (ParseError, TokenError, UnicodeDecodeError)


@seed(20260716)
@settings(max_examples=200, deadline=None)
@given(
    types=st.sampled_from(["INT", "TEXT", "FLOAT", "BOOL"]),
    nullable=st.booleans(),
    unique=st.booleans(),
    pk=st.booleans(),
)
def test_random_constraint_clause_never_crashes(types, nullable, unique, pk):
    """Random constraint combinations must not leak system exceptions."""
    pieces = [types]
    if not nullable:
        pieces.append("NOT NULL")
    if unique:
        pieces.append("UNIQUE")
    if pk:
        pieces.append("PRIMARY KEY")
    sql = f"CREATE TABLE t(x {' '.join(pieces)})"
    try:
        parse(tokenize(sql))
    except _ALLOWED:
        pass