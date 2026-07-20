"""Schema column parsing + name-to-index helpers.

Centralizes the patterns that used to be re-implemented in 4+ places:

- :func:`col_type_and_params` — extract ``(type, type_params)`` from a
  schema column tuple (2-tuple legacy form or 3-tuple parametric form).
  Previously lived in ``row_codec._column_type_and_params`` and was
  re-inlined in ``executor.eval_expr``, ``executor._exec_update`` and
  ``_executor_sort.stable_sort``.

- :func:`schema_name_index` — build ``{column_name: index}`` for a
  v2 schema (list of ``(name, type[, params])`` tuples). Used by SELECT,
  aggregation, ORDER BY, projection.

- :func:`row_name_index` / :func:`ti_name_index` — equivalents for
  Row (named columns) and TableInfo (Column objects with .name).

All helpers are pure: no class state, no logging, safe to call inside
hot loops. Callers should cache the result if the schema is reused
across many rows (see e.g. ``_exec_select``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from tinydb.catalog import Row, TableInfo


def col_type_and_params(col: tuple) -> tuple[str, tuple]:
    """Return ``(type, type_params)`` from a schema column entry.

    Accepts both forms:
      - ``(name, type)`` — legacy 2-tuple; ``type_params`` defaults to ``()``.
      - ``(name, type, params)`` — v2 form; ``params`` is normalized to ``tuple``.

    Raises ``ValueError`` for any other shape; the schema is the
    contract boundary and silent acceptance would mask parser bugs.
    """
    if len(col) == 2:
        return col[1], ()
    if len(col) == 3:
        return col[1], tuple(col[2])
    raise ValueError(
        f"schema column entry must be (name, type[, params]), got {col!r}"
    )


def schema_name_index(
    schema: Sequence[tuple],
    *,
    lowercase_keys: bool = False,
) -> dict[str, int]:
    """Build ``{column_name: index}`` for a v2 schema.

    The schema is a sequence of ``(name, type[, params])`` tuples; this
    helper only reads the first element so it works regardless of
    whether the parser emits 2- or 3-tuple rows.

    Args:
        schema: table schema (v2 form).
        lowercase_keys: if True, lowercase the key (legacy compat for
            a couple of parser-side paths that normalize identifiers
            before lookup).
    """
    if lowercase_keys:
        return {n.lower(): i for i, (n, *_) in enumerate(schema)}
    return {n: i for i, (n, *_) in enumerate(schema)}


def row_name_index(row: "Row") -> dict[str, int]:
    """Build ``{column_name: index}`` from a Row's ``columns`` tuple."""
    return {n: i for i, n in enumerate(row.columns)}


def ti_name_index(ti: "TableInfo") -> dict[str, int]:
    """Build ``{column_name: index}`` from a TableInfo's Column objects."""
    return {c.name: i for i, c in enumerate(ti.columns)}


# Re-export for callers that want a single import path. Kept private
# (``_SchemaCol``) because external code should consume
# ``col_type_and_params`` directly.
SchemaColumn = tuple  # see module docstring for accepted shapes.


__all__ = [
    "SchemaColumn",
    "col_type_and_params",
    "row_name_index",
    "schema_name_index",
    "ti_name_index",
]