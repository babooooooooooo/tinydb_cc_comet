"""Type system: codecs for INT/TEXT/FLOAT/BOOL/DECIMAL/DATE/TIME/TIMESTAMP/VARCHAR/CHAR/etc.

The codec registry is the canonical type contract. Every codec exposes
``encode_py``/``decode_bytes``/``validate`` and is selected with
:func:`codec_for`. Parametric types are instantiated per call by
:func:`codec_for`.

Parametric types (VARCHAR(N), CHAR(N), DECIMAL(p,s)) are stored as codec
classes in the registry and instantiated per-call by :func:`codec_for`.
"""

import datetime as _dt
import math
import struct
from typing import Any, Protocol

_EPOCH_DATE = _dt.date(1970, 1, 1)  # DATE days-since-epoch origin
_EPOCH_DT = _dt.datetime(1970, 1, 1)  # TIMESTAMP seconds-since-epoch origin


class CodecError(TypeError, ValueError, OverflowError):
    """Raised by a TypeCodec when a value violates the codec's contract.

    Multi-inherits ``TypeError``/``ValueError``/``OverflowError`` so that
    legacy ``except (TypeError, ValueError, OverflowError)`` blocks
    continue to work unmodified; new code should catch ``CodecError``
    directly to make intent clear.

    Carries no extra payload — the message is the diagnostic.
    """

    pass


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


def _format_type_params(type_name: str, params: tuple) -> str:
    """Render ``'VARCHAR(10,) [5]'`` style suffix; empty when params empty."""
    if not params:
        return ""
    return f"{list(params)}"


def validate_compare_types(col_type: str, col_params: tuple,
                           lit_type: str, lit_params: tuple) -> None:
    """Strict same-type comparison per Design D6.

    WHERE-clause equality requires the column type and the literal type to
    match by both ``type_name`` and ``type_params``. Used by the executor
    before delegating byte comparison to ``codec_for``.

    Raises:
        TypeError: column type or params differ from literal.
    """
    if col_type != lit_type or col_params != lit_params:
        raise TypeError(
            f"type mismatch: {col_type}{_format_type_params(col_type, col_params)} "
            f"vs {lit_type}{_format_type_params(lit_type, lit_params)}"
        )


def infer_literal_type(value: object) -> tuple[str, tuple]:
    """Map a parsed-literal Python value to ``(type_name, type_params)``.

    The parser emits Python primitives (``bool``/``int``/``float``/``str``) for
    unprefixed literals and ``datetime.date``/``datetime.time``/
    ``datetime.datetime`` for date/time/timestamp-prefixed literals. We infer
    the most common DB type the literal would be assigned to:

    - ``bool``  -> ``BOOL``
    - ``int``   -> ``INT`` (the default INT width; SMALLINT/BIGINT literals
      are not expressible in the current grammar)
    - ``float`` -> ``DOUBLE`` (Python float is double precision; FLOAT col
      expects a width-4 value the executor cannot infer from Python)
    - ``str``   -> ``TEXT``
    - ``datetime.date`` -> ``DATE``
    - ``datetime.time`` -> ``TIME``
    - ``datetime.datetime`` -> ``TIMESTAMP``

    Raises:
        TypeError: unrecognized Python type.
    """
    # bool before int: bool is a subclass of int in Python.
    if isinstance(value, bool):
        return "BOOL", ()
    if isinstance(value, int):
        return "INT", ()
    if isinstance(value, float):
        return "DOUBLE", ()
    if isinstance(value, str):
        return "TEXT", ()
    if isinstance(value, _dt.datetime):
        return "TIMESTAMP", ()
    if isinstance(value, _dt.date):
        return "DATE", ()
    if isinstance(value, _dt.time):
        return "TIME", ()
    raise TypeError(f"unknown literal type: {type(value).__name__}")


# Parametric codecs are stored as classes and instantiated by codec_for().


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
    """Return a validated singleton or configured parametric codec."""
    if type_name not in REGISTRY and type_name not in _ALIAS_MAP:
        raise KeyError(f"unknown type: {type_name!r}")
    if type_name in ("VARCHAR", "CHAR"):
        if len(params) != 1 or params[0] < 1:
            raise ValueError(f"{type_name} requires (N,) with N >= 1, got {params}")
    elif type_name == "DECIMAL" and len(params) != 2:
        raise ValueError(f"DECIMAL requires (p, s), got {params}")
    entry = lookup(type_name)
    return entry(*params) if isinstance(entry, type) else entry


# FLOAT is 4-byte single precision; integer width selects SMALLINT/INT/BIGINT.


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
        self.validate(value)
        fmt, _, _ = self._spec
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
            raise CodecError(f"expected int for {self.name}, got {type(value).__name__}")
        _, lo, hi = self._spec
        if not (lo <= value < hi):
            raise CodecError(f"{self.name} out of range: {value}")


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
            raise CodecError(f"expected str for TEXT, got {type(value).__name__}")


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
            raise CodecError(f"expected bool for BOOL, got {type(value).__name__}")


class _FloatCodec:
    """IEEE 754 float. width=4 single (FLOAT/REAL), width=8 double (DOUBLE)."""

    name = "FLOAT"
    aliases = ("REAL",)
    width = 4  # DOUBLE sets width=8

    def encode_py(self, value):
        self.validate(value)
        return struct.pack(">f" if self.width == 4 else ">d", value)

    def decode_bytes(self, buf, offset):
        size, fmt = (4, ">f") if self.width == 4 else (8, ">d")
        if offset + size > len(buf):
            raise ValueError(f"{self.name} decode truncated at offset {offset}")
        return struct.unpack_from(fmt, buf, offset)[0], offset + size

    def parse_literal(self, text, params):
        v = float(text)
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"{self.name} inf/NaN not allowed: {text!r}")
        return v

    def validate(self, value):
        if not isinstance(value, float):
            raise CodecError(f"expected float for {self.name}, got {type(value).__name__}")
        if math.isnan(value) or math.isinf(value):
            raise CodecError(f"{self.name} inf/NaN not allowed: {value!r}")


class _VarcharCodec:
    """VARCHAR(N): UTF-8 string with max length N."""
    name = "VARCHAR"
    def __init__(self, max_len: int):
        if max_len < 1:
            raise ValueError(f"VARCHAR max_len must be >= 1, got {max_len}")
        self.max_len = max_len
    def _check(self, n: int) -> None:
        if n > self.max_len:
            raise CodecError(f"VARCHAR({self.max_len}) length {n} exceeds max")
    def encode_py(self, value):
        data = value.encode("utf-8"); self._check(len(data))
        return struct.pack(">H", len(data)) + data
    def decode_bytes(self, buf, offset):
        if offset + 2 > len(buf):
            raise ValueError(f"VARCHAR({self.max_len}) length prefix truncated")
        (n,) = struct.unpack_from(">H", buf, offset)
        if offset + 2 + n > len(buf):
            raise ValueError(f"VARCHAR({self.max_len}) payload truncated (need {n} bytes)")
        return buf[offset + 2 : offset + 2 + n].decode("utf-8"), offset + 2 + n
    def parse_literal(self, text, params):
        v = text[1:-1].replace("''", "'"); self._check(len(v.encode("utf-8")))
        return v
    def validate(self, value):
        if not isinstance(value, str):
            raise CodecError(f"expected str for VARCHAR, got {type(value).__name__}")
        self._check(len(value.encode("utf-8")))


class _CharCodec(_VarcharCodec):
    """CHAR(N): fixed-length UTF-8 string with right-space padding (SQL92 PAD SPACE)."""
    name = "CHAR"
    def encode_py(self, value):
        d = value.encode("utf-8")
        if len(d) > self.max_len: raise CodecError(f"CHAR({self.max_len}) length {len(d)} exceeds max")
        return struct.pack(">H", self.max_len) + (value + " " * (self.max_len - len(d))).encode("utf-8")


class _DecimalCodec:
    """DECIMAL(p,s): scaled signed int64."""
    name = "DECIMAL"
    def __init__(self, precision: int, scale: int):
        if not 1 <= precision <= 18: raise ValueError(f"DECIMAL precision must be 1..18, got {precision}")
        if not 0 <= scale < precision: raise ValueError(f"DECIMAL scale must be 0..{precision - 1}, got {scale}")
        self.precision, self.scale = precision, scale
        self._factor, self._max_abs = 10 ** scale, 10 ** (precision - scale)
    def _to_scaled(self, value):
        scaled = round(value * self._factor)
        if abs(scaled) >= 2**63: raise OverflowError(f"DECIMAL({self.precision},{self.scale}) scaled value overflow")
        if abs(value) >= self._max_abs: raise OverflowError(f"DECIMAL({self.precision},{self.scale}) value {value} out of range")
        return scaled
    def encode_py(self, value):
        return struct.pack(">q", self._to_scaled(value))
    def decode_bytes(self, buf, offset):
        if offset + 8 > len(buf): raise ValueError(f"DECIMAL({self.precision},{self.scale}) decode truncated")
        (scaled,) = struct.unpack_from(">q", buf, offset)
        return scaled / self._factor, offset + 8
    def parse_literal(self, text, params):
        return self._to_scaled(float(text)) / self._factor
    def validate(self, value):
        if not isinstance(value, (int, float)) or isinstance(value, bool): raise CodecError(f"expected number for DECIMAL, got {type(value).__name__}")
        self._to_scaled(value)


class _DateCodec:
    """DATE: days since UTC epoch (1970-01-01). 4-byte signed big-endian."""
    name = "DATE"
    def encode_py(self, value):
        return struct.pack(">i", (value - _EPOCH_DATE).days)
    def decode_bytes(self, buf, offset):
        (days,) = struct.unpack_from(">i", buf, offset)
        return _EPOCH_DATE + _dt.timedelta(days=days), offset + 4
    def parse_literal(self, text, params):
        try: return _dt.date.fromisoformat(text)
        except ValueError as e: raise ValueError(f"DATE literal invalid: {text!r} ({e})") from e
    def validate(self, value):
        if not isinstance(value, _dt.date): raise CodecError(f"expected date for DATE, got {type(value).__name__}")


class _TimeCodec:
    """TIME: seconds since midnight UTC. 4-byte unsigned big-endian."""
    name = "TIME"
    def encode_py(self, value):
        s = value.hour * 3600 + value.minute * 60 + value.second
        if not 0 <= s <= 86399: raise ValueError(f"TIME out of range: {s}")
        return struct.pack(">I", s)
    def decode_bytes(self, buf, offset):
        (s,) = struct.unpack_from(">I", buf, offset)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return _dt.time(h, m, sec), offset + 4
    def parse_literal(self, text, params):
        try: return _dt.time.fromisoformat(text)
        except ValueError as e: raise ValueError(f"TIME literal invalid: {text!r} ({e})") from e
    def validate(self, value):
        if not isinstance(value, _dt.time): raise CodecError(f"expected time for TIME, got {type(value).__name__}")


class _TimestampCodec:
    """TIMESTAMP: seconds since UTC epoch. 8-byte signed big-endian. Naive datetime."""
    name = "TIMESTAMP"
    def encode_py(self, value):
        return struct.pack(">q", int((value - _EPOCH_DT).total_seconds()))
    def decode_bytes(self, buf, offset):
        (s,) = struct.unpack_from(">q", buf, offset)
        return _EPOCH_DT + _dt.timedelta(seconds=s), offset + 8
    def parse_literal(self, text, params):
        try: return _dt.datetime.fromisoformat(text)
        except ValueError as e: raise ValueError(f"TIMESTAMP literal invalid: {text!r} ({e})") from e
    def validate(self, value):
        if not isinstance(value, _dt.datetime): raise CodecError(f"expected datetime for TIMESTAMP, got {type(value).__name__}")


# Populate REGISTRY.
REGISTRY["INT"] = _IntCodec()
REGISTRY["TEXT"] = _TextCodec()
REGISTRY["BOOL"] = _BoolCodec()
REGISTRY["FLOAT"] = _FloatCodec()
REGISTRY["SMALLINT"] = _IntCodec(); REGISTRY["SMALLINT"].name = "SMALLINT"; REGISTRY["SMALLINT"].width = 2
REGISTRY["BIGINT"] = _IntCodec(); REGISTRY["BIGINT"].name = "BIGINT"; REGISTRY["BIGINT"].width = 8
REGISTRY["INT"].aliases = ("INTEGER",)
REGISTRY["DOUBLE"] = _FloatCodec(); REGISTRY["DOUBLE"].name = "DOUBLE"; REGISTRY["DOUBLE"].width = 8
REGISTRY["DOUBLE"].aliases = ("DOUBLE PRECISION",)
REGISTRY["VARCHAR"] = _VarcharCodec
REGISTRY["CHAR"] = _CharCodec
REGISTRY["DECIMAL"] = _DecimalCodec
REGISTRY["DATE"] = _DateCodec()
REGISTRY["TIME"] = _TimeCodec()
REGISTRY["TIMESTAMP"] = _TimestampCodec()
# BOOLEAN and REAL are first-class REGISTRY keys per the public contract.
REGISTRY["BOOLEAN"] = REGISTRY["BOOL"]
REGISTRY["REAL"] = REGISTRY["FLOAT"]
for _codec in REGISTRY.values():
    if isinstance(_codec, type):
        continue  # skip parametric class entries (no aliases to register)
    for _alias in getattr(_codec, "aliases", ()):
        _ALIAS_MAP[_alias] = _codec
