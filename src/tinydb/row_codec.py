"""Row codec: null bitmap (LSB-first) + length-prefixed values per Design Doc §3.4."""
from tinydb.type_system import (
    encode_int, decode_int,
    encode_text, decode_text,
    encode_bool, decode_bool,
    encode_float, decode_float,
)

_ENCODERS = {
    "INT": encode_int,
    "TEXT": encode_text,
    "BOOL": encode_bool,
    "FLOAT": encode_float,
}
_DECODERS = {
    "INT": decode_int,
    "TEXT": decode_text,
    "BOOL": decode_bool,
    "FLOAT": decode_float,
}


def _bitmap_len(col_count: int) -> int:
    return (col_count + 7) // 8


def encode_row(values: list, schema: list[tuple[str, str]]) -> bytes:
    """Encode a row: [null_bitmap] [value_0] [value_1] ...

    Bitmap is LSB-first: column 0 -> bit 0 of byte 0, column 1 -> bit 1, ...

    Callers SHOULD pre-validate types via type_system.py_to_db for strict
    type checking (e.g., reject bool-as-INT, NaN/Inf FLOAT). This module
    performs mechanical encoding only.
    """
    if len(values) != len(schema):
        raise ValueError(f"values count {len(values)} != schema columns {len(schema)}")
    blen = _bitmap_len(len(schema))
    bitmap = bytearray(blen)
    parts: list[bytes] = []
    for i, (val, (_name, typ)) in enumerate(zip(values, schema)):
        if val is None:
            bitmap[i // 8] |= 1 << (i % 8)
            continue
        parts.append(_ENCODERS[typ](val))
    return bytes(bitmap) + b"".join(parts)


def decode_row(buf: bytes, schema: list[tuple[str, str]]) -> list:
    """Decode a row into Python values (None for NULL columns).

    Raises ValueError if buf is shorter than the null bitmap. If the bitmap
    is intact but a value is truncated, ValueError propagates from the
    underlying type decoders (e.g. "INT decode truncated at offset N").
    """
    blen = _bitmap_len(len(schema))
    if len(buf) < blen:
        raise ValueError(f"row buffer too short for bitmap: {len(buf)} < {blen}")
    bitmap = buf[:blen]
    out: list = []
    off = blen
    for i, (_name, typ) in enumerate(schema):
        null_bit = (bitmap[i // 8] >> (i % 8)) & 1
        if null_bit:
            out.append(None)
            continue
        val, off = _DECODERS[typ](buf, off)
        out.append(val)
    return out
