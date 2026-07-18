"""Row codec: null bitmap (LSB-first) + length-prefixed values per Design Doc §3.4.

Codec dispatch goes through ``codec_for`` (Protocol-based) so that subsequent
type additions (VARCHAR/CHAR/DECIMAL/etc.) and the FLOAT 4-byte migration only
need to touch type_system.py. The legacy module-level ``encode_int``/``decode_int``
helpers are no longer referenced here; existing callers that still rely on
those helpers should import them directly from ``tinydb.type_system``.

Schema tuple shape:
  - (name, type)                 — 2-tuple, legacy form; params defaults to ()
  - (name, type, params)         — 3-tuple, forward-compatible form (Plan task 7
                                   wires Parser to produce this for VARCHAR/CHAR/DECIMAL)
"""
from tinydb.type_system import codec_for


def _bitmap_len(col_count: int) -> int:
    return (col_count + 7) // 8


def _column_type_and_params(col):
    """Return (typ, params) from a schema column tuple. Accepts 2-tuple or 3-tuple."""
    if len(col) == 2:
        return col[1], ()
    if len(col) == 3:
        return col[1], tuple(col[2])
    raise ValueError(f"schema column entry must be (name, type[, params]), got {col!r}")


def encode_row(values: list, schema: list) -> bytes:
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
    for i, (val, col) in enumerate(zip(values, schema)):
        if val is None:
            bitmap[i // 8] |= 1 << (i % 8)
            continue
        typ, params = _column_type_and_params(col)
        codec = codec_for(typ, params)
        parts.append(codec.encode_py(val))
    return bytes(bitmap) + b"".join(parts)


def decode_row(buf: bytes, schema: list) -> list:
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
    for i, col in enumerate(schema):
        null_bit = (bitmap[i // 8] >> (i % 8)) & 1
        if null_bit:
            out.append(None)
            continue
        typ, params = _column_type_and_params(col)
        codec = codec_for(typ, params)
        val, off = codec.decode_bytes(buf, off)
        out.append(val)
    return out
