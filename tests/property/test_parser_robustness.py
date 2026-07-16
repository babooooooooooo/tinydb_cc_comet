"""Property-based tests for parser/tokenizer robustness (Task 23, tasks.md §10.4).

Feeds hypothesis-generated random strings through ``tokenize`` -> ``parse`` and
asserts that only domain exceptions escape. Allowed exceptions:

* ``TokenError`` — the tokenizer rejects malformed input
* ``ParseError`` — the parser rejects malformed token sequences
* ``UnicodeDecodeError`` — text literal decoding may surface invalid UTF-8
  bytes produced by the multi-byte text-literal decoder

Any other exception is treated as a production bug: the parser/tokenizer must
never leak a system exception (e.g. ``IndexError``, ``AttributeError``,
``ValueError``, ``KeyError``) for arbitrary textual input.
"""
from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import given, seed, settings

from tinydb.errors import ParseError, TokenError
from tinydb.parser import parse
from tinydb.tokenizer import tokenize

pytestmark = pytest.mark.property


_ALLOWED_EXCEPTIONS = (TokenError, ParseError, UnicodeDecodeError)


@seed(20260715)
@settings(max_examples=500, deadline=None)
@given(
    sql=st.text(
        max_size=200,
        alphabet=st.characters(
            blacklist_categories=("Cc", "Cs"),
            blacklist_characters=("\\",),
        ),
    )
)
def test_tokenize_then_parse_never_leaks_system_exceptions(sql: str) -> None:
    """Random SQL-ish input must surface only domain errors, never system ones.

    Tokenize, then parse the token stream. The tokenizer may raise
    ``TokenError`` (bad character, unterminated literal, malformed numeric
    literal) and the parser may raise ``ParseError`` (unexpected token,
    missing keyword, etc.). ``UnicodeDecodeError`` may also escape when a text
    literal contains invalid UTF-8 — the task spec explicitly tolerates it.

    Any other exception class indicates a robustness regression in the
    tokenizer or parser and should fail this property.
    """
    try:
        tokens = tokenize(sql)
    except _ALLOWED_EXCEPTIONS:
        # Expected domain failure during tokenization.
        return
    try:
        parse(tokens)
    except _ALLOWED_EXCEPTIONS:
        # Expected domain failure during parsing.
        return