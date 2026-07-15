"""4-type system: INT/TEXT/FLOAT/BOOL encode/decode + literal parse + py_to_db/db_to_py + validate_compare. <= 150 lines."""

import math
import struct

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
