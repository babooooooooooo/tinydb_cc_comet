"""ORDER BY comparator for the Executor's SELECT path.

Extracted from ``executor.py`` to keep the latter under its module line
budget (Risk R7 in the tinydb-acid design doc). The Executor delegates
``_stable_sort`` to :func:`stable_sort` here.
"""
from __future__ import annotations

from functools import cmp_to_key
from typing import Any

from tinydb._schema import col_type_and_params, schema_name_index
from tinydb.errors import ExecutionError
from tinydb.type_system import codec_for


def stable_sort(
    rows: list[tuple[int, list[Any], int]],
    items: tuple,
    schema: list[tuple[str, str, tuple]],
) -> list[tuple[int, list[Any], int]]:
    """Stable multi-key sort by OrderByItem list.

    Uses ``cmp_to_key`` to support arbitrary Python types (INT, TEXT,
    FLOAT, BOOL) and mixed ASC/DESC. Python ``sorted`` is stable, so
    equal keys preserve insertion order (which itself is page-slot order).

    ``schema`` is the v2 form (3-tuple with type_params) so codec
    dispatch honors parametric types (Task 17).
    """
    name_to_idx = schema_name_index(schema)

    def cmp(r1: tuple, r2: tuple) -> int:
        for it in items:
            if it.column not in name_to_idx:
                raise ExecutionError(
                    f"unknown column {it.column!r} in ORDER BY"
                )
            i = name_to_idx[it.column]
            v1, v2 = r1[1][i], r2[1][i]
            col_type, col_params = col_type_and_params(schema[i])
            # codec_for is the canonical type check; surface type errors
            # as ExecutionError (consistent with executor error model).
            try:
                codec_for(col_type, col_params).validate(v1)
                codec_for(col_type, col_params).validate(v2)
            except (TypeError, ValueError, OverflowError) as e:
                raise ExecutionError(
                    f"column {it.column!r}: {e}"
                ) from e
            if v1 < v2:
                return -1 if not it.descending else 1
            if v1 > v2:
                return 1 if not it.descending else -1
        return 0  # all keys equal; Python sorted is stable

    return sorted(rows, key=cmp_to_key(cmp))