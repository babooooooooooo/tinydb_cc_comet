"""4-type system: INT/TEXT/FLOAT/BOOL encode/decode + literal parse + py_to_db/db_to_py + validate_compare. <= 150 lines."""

import math
import struct
from typing import Any, Protocol

_INT_FMT = ">q"  # signed 64-bit big-endian
_INT_SIZE = 8


def encode_int(value: int) -> bytes:
    if not -2**63 <= value < 2**63:
        raise OverflowError(f"INT out of range: {value}")
    return struct.pack(_INT_FMT, value)


def decode_int(buf: bytes, offset: int) -> tuple[int, int]:
    if offset + _INT_SIZE > len(buf):
        raise ValueError(f"INT decode truncated at offset {offset}")
    return struct.unpack_from(_INT_FMT, buf, offset)[0], offset + _INT_SIZE


def encode_text(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack(">H", len(data)) + data


def decode_text(buf: bytes, offset: int) -> tuple[str, int]:
    if offset + 2 > len(buf):
        raise ValueError("TEXT length prefix truncated")
    (n,) = struct.unpack_from(">H", buf, offset)
    if offset + 2 + n > len(buf):
        raise ValueError(f"TEXT payload truncated (need {n} bytes)")
    return buf[offset + 2 : offset + 2 + n].decode("utf-8"), offset + 2 + n


def encode_bool(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"


def decode_bool(buf: bytes, offset: int) -> tuple[bool, int]:
    if offset + 1 > len(buf):
        raise ValueError("BOOL decode truncated")
    return buf[offset] != 0, offset + 1


_FLOAT_FMT = ">d"


def encode_float(value: float) -> bytes:
    return struct.pack(_FLOAT_FMT, value)


def decode_float(buf: bytes, offset: int) -> tuple[float, int]:
    if offset + 8 > len(buf):
        raise ValueError("FLOAT decode truncated")
    return struct.unpack_from(_FLOAT_FMT, buf, offset)[0], offset + 8


def parse_int_literal(s: str) -> int:
    return int(s)


def parse_float_literal(s: str) -> float:
    v = float(s)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"FLOAT inf/NaN not allowed: {s!r}")
    return v


def parse_text_literal(s: str) -> str:
    # s already includes surrounding single quotes (tokenizer produces raw text)
    if len(s) < 2 or s[0] != "'" or s[-1] != "'":
        raise ValueError(f"invalid text literal: {s!r}")
    inner = s[1:-1].replace("''", "'")
    return inner


def parse_bool_literal(s: str) -> bool:
    u = s.upper()
    if u == "TRUE":
        return True
    if u == "FALSE":
        return False
    raise ValueError(f"invalid bool literal: {s!r}")


def py_to_db(value, column_type: str) -> bytes:
    if column_type == "INT":
        # bool 是 int 的子类，必须先剔除
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"expected int for INT, got {type(value).__name__}")
        return encode_int(value)
    if column_type == "TEXT":
        if not isinstance(value, str):
            raise TypeError(f"expected str for TEXT, got {type(value).__name__}")
        return encode_text(value)
    if column_type == "FLOAT":
        if not isinstance(value, float):
            raise TypeError(f"expected float for FLOAT, got {type(value).__name__}")
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"FLOAT inf/NaN not allowed: {value!r}")
        return encode_float(value)
    if column_type == "BOOL":
        if not isinstance(value, bool):
            raise TypeError(f"expected bool for BOOL, got {type(value).__name__}")
        return encode_bool(value)
    raise ValueError(f"unsupported column type: {column_type}")


def db_to_py(buf: bytes, column_type: str):
    if column_type == "INT":
        return decode_int(buf, 0)[0]
    if column_type == "TEXT":
        return decode_text(buf, 0)[0]
    if column_type == "FLOAT":
        return decode_float(buf, 0)[0]
    if column_type == "BOOL":
        return decode_bool(buf, 0)[0]
    raise ValueError(f"unsupported column type: {column_type}")


def validate_compare(col_bytes: bytes, col_type: str,
                     lit_bytes: bytes, lit_type: str) -> None:
    if col_type != lit_type:
        raise TypeError(f"type mismatch: {col_type} vs {lit_type}")
    if col_type == "FLOAT":
        v = decode_float(col_bytes, 0)[0]
        if math.isnan(v) or math.isinf(v):
            raise ValueError("FLOAT inf/NaN not allowed")


# ---------------------------------------------------------------------------
# TypeCodec Protocol + REGISTRY scaffolding (Task 1 of tinydb-types change).
# The legacy module-level encode/decode helpers above remain untouched for
# backward compatibility (Design §F2). Subsequent tasks will populate
# REGISTRY with concrete codec instances and introduce parameterised
# variants for VARCHAR/CHAR/DECIMAL.
# ---------------------------------------------------------------------------


class TypeCodec(Protocol):
    """Protocol for all type codecs. Each codec owns its bytes encoding."""

    name: str
    aliases: tuple = ()

    def encode_py(self, value: Any) -> bytes: ...
    def decode_bytes(self, buf: bytes, offset: int) -> tuple: ...
    def parse_literal(self, text: str, params: tuple) -> Any: ...
    def validate(self, value: Any) -> None: ...


REGISTRY: dict = {}


_ALIAS_MAP: dict = {}


def lookup(type_name: str):
    """Return the parameterless codec template for type_name (case-sensitive uppercase).

    Raises KeyError if unknown.
    """
    if type_name in REGISTRY:
        return REGISTRY[type_name]
    if type_name in _ALIAS_MAP:
        return _ALIAS_MAP[type_name]
    raise KeyError(f"unknown type: {type_name!r}")


def codec_for(type_name: str, params: tuple = ()):
    """Return a configured codec instance for type_name with params.

    For non-parametric types (INT, TEXT, FLOAT, BOOL, DATE, TIME, TIMESTAMP),
    params must be () and the registry singleton is returned.

    For parametric types:
      - VARCHAR(N) / CHAR(N): params must be (N,) with N >= 1
      - DECIMAL(p, s): params must be (p, s) with 1 <= p <= 18 and 0 <= s < p

    Returns the registry entry for now (specific instantiations in later tasks).
    Raises ValueError on invalid params; KeyError on unknown type.
    """
    if type_name not in REGISTRY and type_name not in _ALIAS_MAP:
        raise KeyError(f"unknown type: {type_name!r}")
    if type_name in ("VARCHAR", "CHAR"):
        if len(params) != 1 or params[0] < 1:
            raise ValueError(f"{type_name} requires (N,) with N >= 1, got {params}")
    if type_name == "DECIMAL":
        if len(params) != 2:
            raise ValueError(f"DECIMAL requires (p, s), got {params}")
        p, s = params
        if not (1 <= p <= 18 and 0 <= s < p):
            raise ValueError(f"DECIMAL({p},{s}) invalid; need 1 <= p <= 18 and 0 <= s < p")
    return lookup(type_name)
