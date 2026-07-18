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


# Codec Protocol implementations (Tasks 2-3 of tinydb-types). Each codec owns
# its bytes encoding; legacy module-level encode_*/decode_* helpers above
# remain untouched per Design §F2. FLOAT 4-byte single precision per Design D3.
# Task 3 generalises _IntCodec via `width` and registers SMALLINT (width=2).


class _IntCodec:
    """Signed big-endian integer. width in bytes: 2=SMALLINT, 4=INT, 8=BIGINT."""

    name = "INT"
    width = 4

    @property
    def _spec(self):
        return {2: (">h", -(2**15), 2**15),
                4: (">i", -(2**31), 2**31),
                8: (">q", -(2**63), 2**63)}[self.width]

    def encode_py(self, value):
        fmt, lo, hi = self._spec
        if not (lo <= value < hi):
            raise OverflowError(f"{self.name} out of range: {value}")
        return struct.pack(fmt, value)

    def decode_bytes(self, buf, offset):
        if offset + self.width > len(buf):
            raise ValueError(f"{self.name} decode truncated at offset {offset}")
        fmt, _, _ = self._spec
        return struct.unpack_from(fmt, buf, offset)[0], offset + self.width

    def parse_literal(self, text, params):
        v = int(text)
        self.validate(v)
        return v

    def validate(self, value):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"expected int for {self.name}, got {type(value).__name__}")
        _, lo, hi = self._spec
        if not (lo <= value < hi):
            raise OverflowError(f"{self.name} out of range: {value}")


class _TextCodec:
    """Unlimited-length UTF-8 string. VARCHAR(N) / CHAR(N) in later tasks."""

    name = "TEXT"

    def encode_py(self, value):
        data = value.encode("utf-8")
        return struct.pack(">H", len(data)) + data

    def decode_bytes(self, buf, offset):
        if offset + 2 > len(buf):
            raise ValueError("TEXT length prefix truncated")
        (n,) = struct.unpack_from(">H", buf, offset)
        if offset + 2 + n > len(buf):
            raise ValueError(f"TEXT payload truncated (need {n} bytes)")
        return buf[offset + 2 : offset + 2 + n].decode("utf-8"), offset + 2 + n

    def parse_literal(self, text, params):
        # text includes surrounding single quotes (tokenizer produces raw text)
        if len(text) < 2 or text[0] != "'" or text[-1] != "'":
            raise ValueError(f"invalid text literal: {text!r}")
        return text[1:-1].replace("''", "'")

    def validate(self, value):
        if not isinstance(value, str):
            raise TypeError(f"expected str for TEXT, got {type(value).__name__}")


class _BoolCodec:
    name = "BOOL"
    aliases = ("BOOLEAN",)

    def encode_py(self, value):
        return b"\x01" if value else b"\x00"

    def decode_bytes(self, buf, offset):
        if offset + 1 > len(buf):
            raise ValueError("BOOL decode truncated")
        return buf[offset] != 0, offset + 1

    def parse_literal(self, text, params):
        u = text.upper()
        if u == "TRUE":
            return True
        if u == "FALSE":
            return False
        raise ValueError(f"invalid bool literal: {text!r}")

    def validate(self, value):
        if not isinstance(value, bool):
            raise TypeError(f"expected bool for BOOL, got {type(value).__name__}")


class _FloatCodec:
    """IEEE 754 floating point.

    width=4 -> single precision (FLOAT/REAL), bytes via '>f'.
    width=8 -> double precision (DOUBLE), bytes via '>d'. Plan task 3 adds
    DOUBLE as a separate codec that sets width=8.
    """

    name = "FLOAT"
    aliases = ("REAL",)
    width = 4  # default for FLOAT/REAL; DOUBLE sets width=8

    def encode_py(self, value):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"FLOAT inf/NaN not allowed: {value!r}")
        if self.width == 4:
            return struct.pack(">f", value)
        return struct.pack(">d", value)

    def decode_bytes(self, buf, offset):
        size = 4 if self.width == 4 else 8
        fmt = ">f" if self.width == 4 else ">d"
        if offset + size > len(buf):
            raise ValueError(f"FLOAT decode truncated at offset {offset}")
        return struct.unpack_from(fmt, buf, offset)[0], offset + size

    def parse_literal(self, text, params):
        v = float(text)
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"FLOAT inf/NaN not allowed: {text!r}")
        return v

    def validate(self, value):
        if not isinstance(value, float):
            raise TypeError(f"expected float for FLOAT, got {type(value).__name__}")
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"FLOAT inf/NaN not allowed: {value!r}")


# Populate REGISTRY with MVP codecs (Plan task 1.3)
REGISTRY["INT"] = _IntCodec()
REGISTRY["TEXT"] = _TextCodec()
REGISTRY["BOOL"] = _BoolCodec()
REGISTRY["FLOAT"] = _FloatCodec()

# Register SMALLINT (width=2) — separate _IntCodec instance with narrower width.
REGISTRY["SMALLINT"] = _IntCodec()  # default INT/width=4 below is overwritten
REGISTRY["SMALLINT"].name = "SMALLINT"
REGISTRY["SMALLINT"].width = 2
# Register BIGINT (width=8); declare INTEGER alias for INT (picked up by loop).
REGISTRY["BIGINT"] = _IntCodec(); REGISTRY["BIGINT"].name = "BIGINT"; REGISTRY["BIGINT"].width = 8
REGISTRY["INT"].aliases = ("INTEGER",)
# Build alias map from any declared aliases on the registered codecs.
for _codec in REGISTRY.values():
    for _alias in getattr(_codec, "aliases", ()):
        _ALIAS_MAP[_alias] = _codec
