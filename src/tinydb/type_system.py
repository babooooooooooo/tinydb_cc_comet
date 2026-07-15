"""4-type system: INT/TEXT/FLOAT/BOOL encode/decode + literal parse + py_to_db/db_to_py + validate_compare. <= 150 lines."""

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
