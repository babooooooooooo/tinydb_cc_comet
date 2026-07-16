"""SQL tokenizer: identifier / keyword / int / float / text / bool / punctuation. ≤ 200 lines."""

from dataclasses import dataclass
from typing import Any, Literal

from tinydb.errors import TokenError
from tinydb.type_system import (
    parse_float_literal,
    parse_int_literal,
    parse_text_literal,
)

KEYWORDS = {
    "CREATE", "TABLE", "DROP", "INSERT", "INTO", "VALUES", "SELECT",
    "FROM", "WHERE", "DELETE", "INT", "TEXT", "FLOAT", "BOOL",
    "NOT", "NULL", "PRIMARY", "KEY", "UNIQUE",  # Task 4
}
# TRUE / FALSE excluded from KEYWORDS: they emit BOOL literals (Task 13 spec).

TokenType = Literal["KEYWORD", "IDENT", "INT", "FLOAT", "TEXT", "BOOL", "PUNCT", "EOF"]


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: Any
    line: int
    col: int


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c == "_"


def _is_ident_cont(c: str) -> bool:
    return c.isalnum() or c == "_"


def _advance(i: int, line: int, col: int, c: str) -> tuple[int, int, int]:
    """Advance (i, line, col) after consuming char c. Centralizes newline handling."""
    if c == "\n":
        return i + 1, line + 1, 1
    return i + 1, line, col + 1


def tokenize(sql: str) -> list[Token]:
    """Tokenize SQL string. Appends an EOF sentinel as the final token."""
    tokens: list[Token] = []
    i, n = 0, len(sql)
    line, col = 1, 1
    while i < n:
        c = sql[i]
        # whitespace
        if c in (" ", "\t", "\r", "\n"):
            i, line, col = _advance(i, line, col, c)
            continue
        # identifier / keyword / bool / special float literal
        if _is_ident_start(c):
            start_col = col
            j = i
            while j < n and _is_ident_cont(sql[j]):
                j += 1
            text = sql[i:j]
            up = text.upper()
            if up in {"NAN", "INF", "INFINITY"}:
                try:
                    val = parse_float_literal(text)
                    tokens.append(Token("FLOAT", val, line, start_col))
                except ValueError as e:
                    raise TokenError(line, start_col, str(e)) from e
            elif up == "TRUE":
                tokens.append(Token("BOOL", True, line, start_col))
            elif up == "FALSE":
                tokens.append(Token("BOOL", False, line, start_col))
            elif up in KEYWORDS:
                tokens.append(Token("KEYWORD", up, line, start_col))
            else:
                tokens.append(Token("IDENT", text, line, start_col))
            for k in range(i, j):
                i, line, col = _advance(i, line, col, sql[k])
            continue
        # integer or float literal (including negative numbers)
        if c.isdigit() or (c == "-" and i + 1 < n and sql[i + 1].isdigit()):
            start_col = col
            j = i + 1
            while j < n and (sql[j].isdigit() or sql[j] == "."):
                j += 1
            text = sql[i:j]
            try:
                if "." in text:
                    val = parse_float_literal(text)
                    tokens.append(Token("FLOAT", val, line, start_col))
                else:
                    val = parse_int_literal(text)
                    tokens.append(Token("INT", val, line, start_col))
            except ValueError as e:
                raise TokenError(line, start_col, str(e)) from e
            for k in range(i, j):
                i, line, col = _advance(i, line, col, sql[k])
            continue
        # text literal (single-quoted, doubled '' = escape)
        if c == "'":
            start_col = col
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    # doubled-quote ('') is an escape for a literal quote;
                    # skip both characters without consuming the body content.
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise TokenError(line, start_col, "unterminated text literal")
            # raw contains the complete literal (boundary quotes + '' escapes).
            # parse_text_literal performs the single '' -> ' decode — folding here
            # would double-decode and turn "''" into "'".
            raw = sql[i:j + 1]
            val = parse_text_literal(raw)
            tokens.append(Token("TEXT", val, line, start_col))
            for k in range(i, j + 1):
                i, line, col = _advance(i, line, col, sql[k])
            continue
        # punctuation
        if c in "(),;=*<>":
            tokens.append(Token("PUNCT", c, line, col))
            i, line, col = _advance(i, line, col, c)
            continue
        raise TokenError(line, col, f"unexpected character {c!r}")
    tokens.append(Token("EOF", None, line, col))
    return tokens
