"""AST -> storage executor. Owns Pager+Catalog; all I/O lives here.

The Executor is the single bridge between parsed SQL statements and the
on-disk storage layer (Pager + Catalog + SlottedPage). It dispatches each
AST node to a dedicated ``_exec_*`` method. DDL (CREATE/DROP TABLE) is
fully implemented; DML (INSERT/SELECT/DELETE) is wired in and Task 19
finishes SELECT projection/WHERE/DELETE tombstone on top of Task 18's
INSERT + linear scan helper.

tinydb-acid (Task 6): transaction routing. BEGIN/COMMIT/ROLLBACK drive a
single-page-buffer per transaction so intra-txn reads see pending
writes; autocommit wraps each non-control statement in an implicit
single-statement txn so failure auto-rolls-back. The ``_IndexPager``
wrapper installed on every B+tree also routes reads/writes/frees
through ``_txn_*`` helpers so B+tree updates participate in the active
txn.
"""
from collections import defaultdict
from typing import Any, Optional, Union

from tinydb.catalog import Catalog, TableInfo
from tinydb.errors import ConstraintViolation, ExecutionError, PageFull
from tinydb.index_manager import IndexManager
from tinydb.pager import Pager
from tinydb.parser import (
    Begin, Commit, Rollback,
    CreateTable, DropTable, Insert, Select, Delete, Update,
    EqualsExpr, AndExpr, OrExpr, NotExpr,
    AggregateCall,
)
from tinydb.transaction import Transaction
from tinydb.row_codec import decode_row, encode_row
from tinydb.slotted_page import (
    FLAG_SPILL_START, FLAG_TOMBSTONE, HEADER_SIZE, MAX_INLINE_PAYLOAD,
    NULL_PAGE_ID, PAGE_SIZE, SLOT_SIZE, SlottedPage,
)
from tinydb._schema import (
    col_type_and_params,
    row_name_index,
    schema_name_index,
    ti_name_index,
)
from tinydb.parser import default_alias as _aggregate_default_alias
from tinydb.type_system import (
    CodecError,
    codec_for,
    infer_literal_type,
    validate_compare_types,
)

# MAX_INLINE_PAYLOAD = 4078; subtract SLOT_SIZE so an inline first chunk on
# an empty page leaves room for the slot directory entry (no overlap).
_CHUNK_SIZE = MAX_INLINE_PAYLOAD - SLOT_SIZE  # 4072


from tinydb._index_pager import IndexPager as _IndexPager


def eval_expr(expr: Any, row: list, schema: list) -> bool:
    """Recursive WHERE-expression evaluator; AND/OR short-circuit; strict type check.

    ``schema`` is the v2 form ``[(name, type, type_params), ...]`` so codec
    dispatch can honor parametric types (VARCHAR(N), CHAR(N), DECIMAL(p, s)).

    Raises:
        ExecutionError: unknown column.
        TypeError: column type vs literal type mismatch (preserves MVP behavior).
    """
    if isinstance(expr, EqualsExpr):
        col_idx = next(
            (i for i, (n, *_) in enumerate(schema) if n == expr.column),
            None,
        )
        if col_idx is None:
            raise ExecutionError(f"unknown column {expr.column!r}")
        col_type, col_params = col_type_and_params(schema[col_idx])
        # Strict same-type check first (Design D6 / Task 18): if the parsed
        # literal's inferred DB type or its params disagree with the column
        # declaration, raise TypeError before any byte encoding happens.
        lit_type, lit_params = infer_literal_type(expr.value)
        validate_compare_types(col_type, col_params, lit_type, lit_params)
        try:
            codec_for(col_type, col_params).validate(expr.value)
        except CodecError as e:
            raise TypeError(
                f"{col_type} vs {_python_type_to_db_type(expr.value)}: {e}"
            ) from e
        return row[col_idx] == expr.value
    if isinstance(expr, AndExpr):
        return eval_expr(expr.left, row, schema) and eval_expr(expr.right, row, schema)
    if isinstance(expr, OrExpr):
        return eval_expr(expr.left, row, schema) or eval_expr(expr.right, row, schema)
    if isinstance(expr, NotExpr):
        return not eval_expr(expr.operand, row, schema)
    raise ExecutionError(f"unsupported expression: {type(expr).__name__}")


def _python_type_to_db_type(value: object) -> str:
    """Map a parsed-literal Python value to its DB type tag (INT/TEXT/...).

    Used by SELECT/DELETE error messages so callers see ``"INT vs TEXT"``
    instead of ``"INT vs str"`` — the spec talks in DB type names, and the
    parser already maps tokens to Python primitive types (str/int/float/bool)
    based on the literal token kind.
    """
    # Order matters: bool is a subclass of int in Python, must be checked first.
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, str):
        return "TEXT"
    return type(value).__name__


# --- tinydb-aggregation: aggregation core -----------------------------------


def _agg_count_star(rows, col_idx, schema):
    """COUNT(*): count all rows including NULL columns (D2)."""
    return len(rows)


def _agg_count_expr(rows, col_idx, schema):
    """COUNT(expr): skip rows where col_idx value is None."""
    return sum(1 for r in rows if r[col_idx] is not None)


def _agg_sum(rows, col_idx, schema):
    """SUM: skip NULL; preserve input type (int+int=int, float+float=float)."""
    total = None
    for r in rows:
        v = r[col_idx]
        if v is None:
            continue
        total = v if total is None else total + v
    return total


def _agg_avg(rows, col_idx, schema):
    """AVG: skip NULL; force float result (D4)."""
    s = 0.0
    n = 0
    for r in rows:
        v = r[col_idx]
        if v is None:
            continue
        s += float(v)
        n += 1
    return (s / n) if n else None


def _agg_min(rows, col_idx, schema):
    """MIN: skip NULL; comparable types (int/float/text)."""
    best = None
    for r in rows:
        v = r[col_idx]
        if v is None:
            continue
        if best is None or v < best:
            best = v
    return best


def _agg_max(rows, col_idx, schema):
    """MAX: skip NULL; comparable types (int/float/text)."""
    best = None
    for r in rows:
        v = r[col_idx]
        if v is None:
            continue
        if best is None or v > best:
            best = v
    return best


_AGG_FUNCS = {
    "COUNT": _agg_count_star,    # default; COUNT(expr) handled by dispatcher
    "SUM":   _agg_sum,
    "AVG":   _agg_avg,
    "MIN":   _agg_min,
    "MAX":   _agg_max,
}


def _resolve_aggregate_arg(agg: AggregateCall) -> tuple:
    """Return ``('*' | ('column', colname), col_idx)``.

    ``col_name`` is None for ``COUNT(*)``; the executor dispatches to
    ``_agg_count_star`` in that case. For ``COUNT(expr)`` /
    ``SUM/AVG/MIN/MAX(col)`` the second element is the column name so the
    caller can look up its index in the schema.
    """
    if agg.arg == "*":
        return ("*", None)
    if (
        not isinstance(agg.arg, tuple)
        or len(agg.arg) != 2
        or agg.arg[0] != "column"
    ):
        raise ExecutionError(
            f"aggregate function {agg.func} requires a column reference",
        )
    return ("column", agg.arg[1])


def _compare(val, op: str, lit) -> bool:
    """NULL-safe comparison used by HAVING/ORDER.

    SQL three-valued logic: ``NULL <op> anything`` is UNKNOWN -> False.
    Supports ``= > < >= <= !=``. Unknown ops raise :class:`ExecutionError`.
    """
    if val is None:
        return False
    if op == "=":
        return val == lit
    if op == ">":
        return val > lit
    if op == "<":
        return val < lit
    if op == ">=":
        return val >= lit
    if op == "<=":
        return val <= lit
    if op == "!=":
        return val != lit
    raise ExecutionError(f"operator {op!r} not supported")


def apply_aggregation(raw_rows: list, stmt, schema) -> list:
    """Group ``raw_rows`` by ``stmt.group_by`` cols, then compute aggregates per group.

    Returns ``list[Row]`` where each Row has columns::

        [*group_by_cols, *aggregate_aliases]

    Per design doc:
      - If ``stmt.group_by`` is empty: a single group (refinement #2 - even
        empty input still produces one row, matching standard SQL semantics).
      - If ``stmt.group_by`` is non-empty: 0+ groups (empty input -> ``[]``).
    """
    from tinydb.database import Row  # local import to avoid cycle

    key_cols = stmt.group_by
    # schema is the v2 form [(name, type, type_params), ...]; we only need the name.
    name_to_idx = schema_name_index(schema)

    # Validate GROUP BY columns exist up front so an unknown column surfaces
    # before any partial grouping work.
    for c in key_cols:
        if c not in name_to_idx:
            raise ExecutionError(f"unknown column {c!r}")

    # 1) Group rows. Empty key_cols -> single sentinel group ``()`` so the
    # downstream loop still runs once even when raw_rows is empty.
    groups: dict = {}
    if key_cols:
        for row in raw_rows:
            key = tuple(row[name_to_idx[c]] for c in key_cols)
            groups.setdefault(key, []).append(row)
    else:
        groups[()] = raw_rows

    # 2) Aggregate per group; build one output Row per group.
    out_rows = []
    for key, group_rows in groups.items():
        values: list = []
        columns: list = []
        # Group-by key columns come first, preserving GROUP BY order.
        for col, val in zip(key_cols, key):
            columns.append(col)
            values.append(val)
        for si in stmt.select_items:
            if si.kind != "aggregate":
                continue
            agg = si.aggregate
            _, col_name = _resolve_aggregate_arg(agg)
            col_idx = None if col_name is None else name_to_idx.get(col_name)
            if col_name is not None and col_idx is None:
                raise ExecutionError(f"unknown column {col_name!r}")
            if agg.func == "COUNT" and agg.arg == "*":
                val = _agg_count_star(group_rows, col_idx, schema)
            elif agg.func == "COUNT":
                val = _agg_count_expr(group_rows, col_idx, schema)
            else:
                val = _AGG_FUNCS[agg.func](group_rows, col_idx, schema)
            # Default alias: ``count`` for COUNT(*) (no column), otherwise
            # ``<func>_<colname>`` matching design doc.
            alias = si.alias or _aggregate_default_alias(agg)
            columns.append(alias)
            values.append(val)
        out_rows.append(Row(values=tuple(values), columns=tuple(columns)))
    return out_rows


def apply_having(rows, having_expr, agg_aliases, group_cols, schema=None) -> list:
    """Filter aggregate rows by ``HAVING`` expression.

    ``having_expr`` is one of:
      - ``None`` - passthrough, returns ``rows`` unchanged.
      - ``AggregateCall`` - inline aggregate form is not supported here;
        callers must put the aggregate in the SELECT list and reference
        its alias from HAVING. We raise so misuse surfaces immediately.
      - ``(col, op, lit)`` tuple - filter rows by comparing the resolved
        column value against ``lit``.

    Three-stage column resolution (alias wins):
      1. ``col`` in ``agg_aliases`` AND in row.columns -> alias
      2. ``col`` in ``group_cols``  AND in row.columns -> group column
      3. else ``ExecutionError("unknown column 'X' in HAVING")``
    """
    if having_expr is None:
        return rows
    if isinstance(having_expr, AggregateCall):
        raise ExecutionError(
            "HAVING with inline aggregate not supported; "
            "use the SELECT-list alias instead",
        )

    col, op, lit = having_expr
    if not rows:
        return rows

    name_to_idx = row_name_index(rows[0])

    # Three-stage resolution: alias -> group col -> raise.
    if col in agg_aliases and col in name_to_idx:
        src_idx = name_to_idx[col]
    elif col in group_cols and col in name_to_idx:
        src_idx = name_to_idx[col]
    else:
        raise ExecutionError(f"unknown column {col!r} in HAVING")

    out = []
    for row in rows:
        val = row.values[src_idx]
        if _compare(val, op, lit):
            out.append(row)
    return out


def _project_aggregate_row(row, stmt, schema):
    """Project aggregate Row to SELECT list shape.

    - SELECT *: pass through row unchanged.
    - Otherwise: for each select_item, look up the corresponding column in row.

    Raises ExecutionError if SELECT references a column not present in the
    aggregate row (E3).
    """
    from tinydb.database import Row

    if any(si.kind == "star" for si in stmt.select_items):
        return row

    name_to_idx_row = row_name_index(row)
    out_cols: list = []
    out_vals: list = []

    for si in stmt.select_items:
        if si.kind == "star":
            continue
        if si.kind == "aggregate":
            src_name = si.alias or _aggregate_default_alias(si.aggregate)
            if src_name not in name_to_idx_row:
                raise ExecutionError(f"missing aggregate column {src_name!r}")
            out_cols.append(src_name)
            out_vals.append(row.values[name_to_idx_row[src_name]])
        else:  # column
            if si.name not in name_to_idx_row:
                raise ExecutionError(
                    f"column {si.name!r} must appear in GROUP BY clause or in an aggregate function",
                )
            out_cols.append(si.alias or si.name)
            out_vals.append(row.values[name_to_idx_row[si.name]])
    return Row(values=tuple(out_vals), columns=tuple(out_cols))


def _apply_limit_offset(stmt, rows):
    """Apply ``stmt.offset`` and ``stmt.limit`` to ``rows``.

    Shared by the legacy scan path and the aggregate 5-phase pipeline so
    both slicing idioms stay in sync. ``offset`` defaults to 0 (falsy) when
    unset; ``limit`` is only honored when explicitly set (slicing with
    ``None`` would not consume all rows, so we guard on ``is not None``).
    Returns the (possibly trimmed) list.
    """
    if stmt.offset:
        rows = rows[stmt.offset:]
    if stmt.limit is not None:
        rows = rows[:stmt.limit]
    return rows


def _project_legacy_row(
    stmt: "Select",
    vals: list,
    proj_idx: list[int],
) -> list:
    """Per-row projector for the legacy / scan SELECT path.

    Mirrors the inline pattern that used to live in ``_exec_select``:
    a ``SELECT *`` returns a copy of ``vals``; a named-column projection
    picks ``vals[i]`` for each index in ``proj_idx``. Pulled into a
    module-level function so ``_slice_and_project`` can be reused by
    both the indexed fast path and the full scan path.
    """
    if stmt.columns == ("*",):
        return list(vals)
    return [vals[i] for i in proj_idx]


def _neg_for_sort(v):
    """Negate numeric values for DESC ordering (works for int and float)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return -v
    return v


def apply_order_limit_phase1(rows: list, order_by, agg_aliases, group_cols) -> list:
    """Phase1 minimal ORDER BY: only IDENT keys, must be in GROUP BY cols or aliases."""
    if not rows or not order_by:
        return rows
    name_to_idx = row_name_index(rows[0])

    keys = []
    for ob in order_by:
        col = ob.column
        if col in agg_aliases or col in group_cols:
            if col not in name_to_idx:
                raise ExecutionError(f"ORDER BY column {col!r} not found in result")
            keys.append((col, name_to_idx[col], ob.descending))
        else:
            raise ExecutionError(
                f"ORDER BY column {col!r} must be a GROUP BY column or aggregate alias",
            )

    def sort_key(row):
        parts = []
        for col, idx, desc in keys:
            v = row.values[idx]
            if v is None:
                parts.append(((1, 0) if desc else (0, 0)))
            else:
                parts.append(((0, v) if not desc else (0, _neg_for_sort(v))))
        return tuple(parts)

    return sorted(rows, key=sort_key)


class Executor:
    """Drive AST statements against a (Pager, Catalog) pair.

    The Executor holds mutable references to the supplied Pager and Catalog;
    it does not own their lifetime — the caller is responsible for closing
    the Pager. Each ``execute`` call mutates the Catalog in-place and
    flushes page 1 (the catalog slot) back to disk.
    """

    def __init__(
        self,
        pager: Pager,
        catalog: Catalog,
        index_manager: Optional[IndexManager] = None,
    ) -> None:
        self.pager = pager
        self.catalog = catalog
        # IndexManager is optional for backward compatibility; default to
        # a fresh per-instance manager so unit tests that construct
        # Executor(pager, catalog) directly still work.
        self.index_manager = (
            index_manager if index_manager is not None else IndexManager(pager)
        )
        # Index pagers wrap the real Pager so the Executor can tell which
        # page ids are owned by B+tree nodes (root + leaves allocated by
        # splits) and skip them when extending the data-page chain.
        # Populated by :meth:`register_index_pager` (called from Database).
        self._index_pagers: list[_IndexPager] = []
        # Per-table list of data page ids. The classic ``ti.root_page_id``
        # / ``ti.next_page_id`` chain assumes contiguous pages, but DROP
        # reclamation can return pages with ids smaller than ``ti.root``
        # (free list is LIFO). We track the actual page list here so insert
        # and scan walk it instead of relying on ``pid += 1`` arithmetic.
        # Keyed by table name; absent entries are lazily initialized from
        # ``ti.root_page_id``. Cleared by ``_exec_drop_table``.
        self._table_data_pages: dict[str, list[int]] = {}
        # tinydb-acid (Task 6): transaction routing state.
        # ``_current_txn`` is None outside a BEGIN block; ``_next_txn_id``
        # increments monotonically so every Transaction has a unique id
        # for WAL bookkeeping. ``_page_buffer`` overlays the Pager's
        # main-file reads with pending in-memory writes so intra-txn
        # INSERTs see each other (a bare ``self.pager.read_page`` would
        # still see the pre-txn contents because the main file is only
        # flushed at COMMIT time).
        self._current_txn: Optional[Transaction] = None
        self._next_txn_id: int = 1
        self._page_buffer: dict[int, bytes] = {}
        # Snapshot of catalog + per-table data pages + index manager
        # used to revert in-memory state on ROLLBACK. The Pager's main
        # file is untouched until COMMIT, so we only need to revert the
        # mutable Python objects the Executor owns.
        self._txn_snapshot: Optional[dict] = None

    def register_index_pager(self, wrapper: "_IndexPager") -> None:
        """Track an _IndexPager so its allocated pages are skipped on data-chain extensions."""
        self._index_pagers.append(wrapper)

    def _make_index_pager(self, pager) -> "_IndexPager":
        """Build a fresh _IndexPager and register it for collision avoidance.

        Passes ``self`` as the executor back-reference so the wrapper
        routes every read/write/free through :meth:`_txn_read_page`,
        :meth:`_txn_write_page`, and :meth:`_txn_free_page` — the
        Executor's txn-aware entry points. Without this, B+tree writes
        would mutate the main file inside a BEGIN block and leave stale
        entries after ROLLBACK (Task 6 follow-up fix).
        """
        wrapper = _IndexPager(pager, executor=self)
        self.register_index_pager(wrapper)
        return wrapper

    def _index_pages(self) -> set[int]:
        """Union of every page id owned by every registered B+tree wrapper."""
        out: set[int] = set()
        for wrapper in self._index_pagers:
            out.update(wrapper._allocated)
        return out

    # --- public dispatch ----------------------------------------------------

    def execute(
        self, stmt: Any,
    ) -> Union[list, list[list[Any]]]:
        """Top-level dispatch: route txn-control vs. data statements.

        BEGIN / COMMIT / ROLLBACK bypass the autocommit wrapper because
        they manage ``self._current_txn`` directly. Everything else goes
        through ``_exec_in_txn`` which auto-opens an implicit txn when
        no explicit one is open.
        """
        if isinstance(stmt, Begin):
            return self._exec_begin(stmt)
        if isinstance(stmt, Commit):
            return self._exec_commit(stmt)
        if isinstance(stmt, Rollback):
            return self._exec_rollback(stmt)
        return self._exec_in_txn(stmt)

    # --- transaction routing (Task 6) ---------------------------------------

    def _txn_write_page(self, page_id: int, data: bytes) -> None:
        """Write ``data`` to ``page_id``: WAL+buffer inside a txn, main outside.

        Inside a txn: ``Transaction.write_page`` appends a PAGE_WRITE
        record to the WAL and buffers the page so subsequent reads
        (``_txn_read_page``) see the new state. The Pager's main file
        stays untouched until :meth:`Transaction.commit` flushes the
        buffered pages via ``Pager.write_main_page``.

        Outside a txn (autocommit before its implicit BEGIN, or the
        post-COMMIT fall-through): write straight to the main file —
        there's no txn to buffer for.
        """
        if self._current_txn is not None:
            self._current_txn.write_page(page_id, data)
            self._page_buffer[page_id] = data
        else:
            self.pager.write_page(page_id, data)

    def _txn_read_page(self, page_id: int) -> bytes:
        """Read ``page_id``: buffer override inside a txn, main otherwise.

        Mirrors :meth:`_txn_write_page` — intra-txn writes are buffered
        in ``_page_buffer`` and must shadow subsequent reads so two
        INSERTs inside the same txn both land in the right slot.
        """
        if self._page_buffer and page_id in self._page_buffer:
            return self._page_buffer[page_id]
        return self.pager.read_page(page_id)

    def _txn_free_page(self, page_id: int) -> None:
        """Free ``page_id`` through the txn layer so ROLLBACK can revert it.

        :meth:`Pager.free_page` writes two bytes-level mutations: the
        freed page's first 4 bytes hold the old free-list head, and
        page 0's ``free_list_head`` field is updated to point at the
        freed page. Both mutations must go through the WAL+buffer so a
        ROLLBACK of a DROP TABLE reverts the free-list head and the
        freed page contents. Outside a txn (legacy autocommit DROP that
        somehow bypasses the autocommit wrapper, or unit tests with
        standalone Executors) we fall through to ``Pager.free_page``.
        """
        if self._current_txn is None:
            self.pager.free_page(page_id)
            return
        # Read both pages through the txn layer so a previous buffered
        # write (e.g., CREATE TABLE inside the same txn) is visible.
        page0 = bytearray(self._txn_read_page(0))
        freed = bytearray(self._txn_read_page(page_id))
        # Pager stores free_list_head u32 at offset 9 of page 0.
        old_head = int.from_bytes(page0[9:13], "big")
        # Free chain: freed page's first 4 bytes -> old_head.
        freed[0:4] = old_head.to_bytes(4, "big")
        # Page 0 head -> newly freed page id.
        page0[9:13] = page_id.to_bytes(4, "big")
        self._txn_write_page(0, bytes(page0))
        self._txn_write_page(page_id, bytes(freed))

    def _snapshot_state(self) -> dict:
        """Capture the Executor's mutable state for ROLLBACK restore.

        Delegates to :func:`tinydb._executor_snapshot.snapshot_state`; this
        module-level helper lives in ``_executor_snapshot.py`` to keep
        ``executor.py`` under its line budget (Risk R7).
        """
        from tinydb._executor_snapshot import snapshot_state

        return snapshot_state(self)

    def _restore_state(self, snap: dict) -> None:
        """Replace the Executor's mutable state with ``snap``.

        Delegates to :func:`tinydb._executor_snapshot.restore_state`; see
        that module for the restore contract.
        """
        from tinydb._executor_snapshot import restore_state

        restore_state(self, snap)

    def _exec_begin(self, stmt: Begin) -> list:
        """Open an explicit transaction; reject nested BEGIN."""
        if self._current_txn is not None:
            raise ExecutionError("nested BEGIN not allowed")
        self._current_txn = Transaction(self._next_txn_id, self.pager)
        self._next_txn_id += 1
        self._txn_snapshot = self._snapshot_state()
        return []

    def _exec_commit(self, stmt: Commit) -> list:
        """Commit the active transaction; reject bare COMMIT."""
        if self._current_txn is None:
            raise ExecutionError("COMMIT without BEGIN")
        self._current_txn.commit()
        self._current_txn = None
        # Drop the buffered pages — they were flushed to main file by
        # ``Transaction.commit`` so subsequent reads must come from disk.
        self._page_buffer.clear()
        self._txn_snapshot = None
        return []

    def _exec_rollback(self, stmt: Rollback) -> list:
        """Roll back the active transaction; reject bare ROLLBACK."""
        if self._current_txn is None:
            raise ExecutionError("ROLLBACK without BEGIN")
        self._current_txn.rollback()
        self._current_txn = None
        # Discard buffered writes (WAL truncated by ``txn.rollback``) and
        # restore the catalog + table_data_pages + indexes snapshot so
        # the in-memory state matches the un-flushed main file.
        self._page_buffer.clear()
        if self._txn_snapshot is not None:
            self._restore_state(self._txn_snapshot)
            self._txn_snapshot = None
        return []

    def _exec_in_txn(self, stmt: Any) -> Union[list, list[list[Any]]]:
        """Run ``stmt`` inside a txn: explicit BEGIN or implicit autocommit.

        Autocommit semantics: when no txn is open we open one, run the
        statement, and auto-commit on success / auto-rollback on
        exception. This way a single failed INSERT leaves no half-
        applied writes in the main file (the page buffer is discarded
        on rollback, the WAL is truncated, and the in-memory catalog
        snapshot is restored).

        Cleanup robustness (Task 6 follow-up): all teardown steps
        (clearing ``_current_txn``, ``_page_buffer``, and restoring
        ``_txn_snapshot``) live inside a single ``finally`` block so
        they run even if ``Transaction.rollback`` itself raises. If
        rollback fails, the rollback error is surfaced with the
        original statement error chained as ``__context__`` — a data
        integrity concern that must not be silently swallowed.
        """
        auto = self._current_txn is None
        if auto:
            self._current_txn = Transaction(self._next_txn_id, self.pager)
            self._next_txn_id += 1
            self._txn_snapshot = self._snapshot_state()
        try:
            result = self._exec_stmt(stmt)
        except Exception as original_exc:
            rollback_exc = None
            try:
                self._current_txn.rollback()
            except Exception as e:
                rollback_exc = e
            finally:
                # Cleanup MUST run regardless of rollback outcome —
                # otherwise a rollback failure leaves the Executor in a
                # half-state where the next statement sees a stale txn
                # reference and a dirty page buffer.
                self._current_txn = None
                self._page_buffer.clear()
                if self._txn_snapshot is not None:
                    try:
                        self._restore_state(self._txn_snapshot)
                    finally:
                        self._txn_snapshot = None
            if rollback_exc is not None:
                # Both the statement AND rollback failed. Surface the
                # rollback error (more critical for data integrity) and
                # chain the original statement error as context.
                raise rollback_exc from original_exc
            raise original_exc
        if auto:
            self._current_txn.commit()
            self._current_txn = None
            self._page_buffer.clear()
            self._txn_snapshot = None
        return result

    def _exec_stmt(self, stmt: Any) -> Union[list, list[list[Any]]]:
        """Dispatch a non-txn-control statement to its ``_exec_*`` handler."""
        if isinstance(stmt, CreateTable): return self._exec_create_table(stmt)
        if isinstance(stmt, DropTable):   return self._exec_drop_table(stmt)
        if isinstance(stmt, Insert):      return self._exec_insert(stmt)
        if isinstance(stmt, Select):      return self._exec_select(stmt)
        if isinstance(stmt, Delete):      return self._exec_delete(stmt)
        if isinstance(stmt, Update):      return self._exec_update(stmt)
        raise ExecutionError(f"unsupported statement: {type(stmt).__name__}")

    # --- DDL: CREATE / DROP TABLE ------------------------------------------

    def _exec_create_table(self, stmt: CreateTable) -> list:
        """Create an empty table and persist the catalog entry.

        Maps ``stmt.columns`` (parser AST: ``tuple[ColumnDefinition, ...]``)
        into a ``tuple[catalog.Column, ...]`` before calling
        ``catalog.create_table``. The explicit bridge is the R1 裁决:
        the parser does NOT import ``catalog``, the catalog does NOT
        import the parser.
        """
        from tinydb.catalog import Column  # local import avoids cycle noise

        if self.catalog.get_table(stmt.name) is not None:
            raise ExecutionError(f"table {stmt.name!r} already exists")

        cols: list[Column] = []
        seen: set = set()
        for cd in stmt.columns:
            if cd.name in seen:
                raise ExecutionError(f"duplicate column {cd.name}")
            seen.add(cd.name)
            cols.append(Column(
                name=cd.name,
                type=cd.type,
                type_params=cd.type_params,
                nullable=cd.nullable,
                unique=cd.unique,
                primary_key=cd.primary_key,
            ))

        root_id = self.pager.alloc_page()
        page = SlottedPage.empty(root_id)
        self._txn_write_page(root_id, page.to_bytes())

        # MVP: next_page_id == root_page_id.
        self.catalog.create_table(
            stmt.name, tuple(cols),
            root_page_id=root_id, next_page_id=root_id,
        )
        # Seed the per-table data page list with the just-allocated root
        # so subsequent INSERTs and SELECTs walk it directly. Without this
        # entry, ``_insert_inline_only`` and ``_scan_table`` would fall
        # back to contiguous ``pid += 1`` walking which DROP reclamation
        # breaks.
        self._table_data_pages[stmt.name] = [root_id]

        # Initialize empty B+tree indexes for this table's indexed columns
        # (PK + UNIQUE). INSERTs will populate them incrementally.
        self.index_manager.rebuild_for_table(self.catalog.get_table(stmt.name))
        # Wrap each new B+tree's pager so subsequent B+tree allocations
        # (root + future splits) are tracked by Executor's collision
        # avoidance. Database.__init__ installs wrappers for pre-existing
        # tables; mid-session CREATE TABLE has to do it here.
        self._on_table_created(stmt.name)

        self._txn_write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        return []

    def _on_table_created(self, table_name: str) -> None:
        """Install _IndexPager wrappers for a table created mid-session.

        Bypasses the wrapper if the B+tree already uses one (idempotent).
        Database._install_index_pagers is the canonical entrypoint; this
        method exists so the Executor doesn't have to reach into Database
        state directly. Standalone Executor instances (constructed in
        unit tests without a Database) skip this step — their data pages
        are allocated without collision avoidance.
        """
        db = getattr(self, "_database_ref", None)
        if db is not None:
            db._install_index_pagers(table_name)

    def _exec_drop_table(self, stmt: DropTable) -> list:
        """Drop a table and reclaim its data + index pages via the free list.

        Thin delegator over :func:`tinydb._executor_drop.exec_drop_table`;
        the implementation lives in ``_executor_drop.py`` to keep
        ``executor.py`` under its line budget. See that module's docstring
        for the full reclamation contract.
        """
        from tinydb._executor_drop import exec_drop_table

        return exec_drop_table(self, stmt)

    # --- DML: INSERT / SELECT / DELETE -------------------------------------

    def _exec_insert(self, stmt: Insert) -> list:
        """Insert row(s) into a table with the constraints pipeline.

        Pipeline (Task 7 裁决 3 方案 A — per-row validation, no tx):
          1. table exists (raises ExecutionError)
          2. column list is non-empty, unique, all known (parser guarantees;
             executor defensively re-checks)
          3. row value count == explicit column count
          4. normalize row into schema order (omitted -> None)
          5. NOT NULL + PK NULL rejection (ConstraintViolation kind='null')
          6. type validation on non-NULL values (existing path)
          7. UNIQUE / PK duplicate scan (Task 10)
          8. encode + insert
        """
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        if not stmt.columns:
            raise ExecutionError("INSERT column list must be non-empty")

        cols = ti.columns
        name_to_idx: dict[str, int] = ti_name_index(ti)

        # Defensive executor-side validation; parser also enforces these.
        seen: set[str] = set()
        for cname in stmt.columns:
            if cname not in name_to_idx:
                raise ExecutionError(f"unknown column {cname!r}")
            if cname in seen:
                raise ExecutionError(f"duplicate column {cname!r}")
            seen.add(cname)

        # Per-batch UNIQUE dedup state (Task 10). Keyed by UniqueGroup
        # so multiple distinct UNIQUE columns are tracked independently
        # within the same INSERT statement.
        session_keys: dict = defaultdict(set)
        try:
            for row_vals in stmt.values:
                if len(row_vals) != len(stmt.columns):
                    raise ExecutionError(
                        f"value count mismatch: got {len(row_vals)}, expected {len(stmt.columns)}"
                    )

                # 4. Normalize to schema order, omitted columns -> None.
                normalized: list = [None] * len(cols)
                for cname, val in zip(stmt.columns, row_vals):
                    normalized[name_to_idx[cname]] = val
                normalized_tuple = tuple(normalized)

                # 5. NOT NULL + PK NULL rejection.
                for i, c in enumerate(cols):
                    if normalized_tuple[i] is None and (not c.nullable or c.primary_key):
                        raise ConstraintViolation(kind="null", column=c.name, value=normalized_tuple[i])

                # 6. Type validation via the codec registry (Task 17 wires
                #    all 15 types through ``codec_for``). ``c.type_params``
                #    carries parametric info (VARCHAR(N), CHAR(N), DECIMAL(p, s)).
                #    Codec errors (TypeError / ValueError / OverflowError)
                #    propagate naturally — the codec is the canonical source
                #    of validation truth.
                validated: list = []
                for c, v in zip(cols, normalized_tuple):
                    if v is None:
                        validated.append(None)
                        continue
                    codec_for(c.type, c.type_params).validate(v)
                    validated.append(v)

                # 7. UNIQUE / PK duplicate check (Task 10). The session_keys
                #    dict is keyed by the unique-group identity so a single
                #    INSERT statement can have multiple distinct UNIQUE columns
                #    tracked independently.
                self._validate_unique_keys(
                    normalized_tuple, ti, name_to_idx, session_keys,
                )

                # 8. Encode + insert. Use schema_v2 so codec dispatch in row_codec
                #    receives the type_params tuple for parametric types.
                row_bytes = encode_row(validated, ti.schema_v2)
                slot_ref = self._insert_row_into_chain(ti, row_bytes)

                # 9. Index maintenance (Task 7): for each indexed column
                #    (PK + UNIQUE), encode the new key and add (key, slot_ref)
                #    to the B+tree. NULL members skip per R9 裁决 9.
                self._index_row(ti, validated, slot_ref)
        finally:
            session_keys.clear()
        return []

    # --- UNIQUE / duplicate_pk validation helpers (Task 10) -----------------

    def _validate_unique_keys(
        self,
        row: tuple,
        ti: TableInfo,
        name_to_idx: dict,
        session_keys: dict,
    ) -> None:
        """Reject duplicate UNIQUE / PRIMARY KEY values for ``row``.

        Each INSERT statement maintains a per-call session set of accepted
        keys via ``session_keys`` so same-batch duplicates
        (``INSERT INTO t VALUES (1,'a'), (2,'a')``) are caught before
        any row hits disk. NULL members skip the check (R9 裁决 9 — SQL
        standard semantics that treats NULL as unknown, not equal to
        anything, including itself).
        """
        for group in self._unique_groups(ti):
            key_value = tuple(row[name_to_idx[c]] for c in group.columns)
            if any(v is None for v in key_value):
                continue
            seen_in_table = self._scan_unique_keys(ti, group.columns)
            if key_value in session_keys[group] or key_value in seen_in_table:
                raise ConstraintViolation(
                    kind=group.kind,
                    columns=group.columns,
                    value=key_value,
                )
            session_keys[group].add(key_value)

    def _unique_groups(self, ti: TableInfo) -> list:
        """Compute the set of unique-key groups for ``ti``.

        R4 裁决 4: PRIMARY KEY groups (single or composite) take priority
        over any same-column UNIQUE groups; a single PRIMARY KEY column
        is never also reported as a UNIQUE column. Single-column UNIQUE
        clauses each form their own group. Multi-column ``UNIQUE (a, b)``
        is not yet supported in this change.
        """
        from collections import namedtuple
        UniqueGroup = namedtuple("UniqueGroup", ["columns", "kind"])
        groups: list = []
        pk_cols = tuple(c.name for c in ti.columns if c.primary_key)
        if pk_cols:
            groups.append(UniqueGroup(columns=pk_cols, kind="duplicate_pk"))
        for c in ti.columns:
            if c.unique and c.name not in pk_cols:
                groups.append(UniqueGroup(columns=(c.name,), kind="unique"))
        return groups

    def _scan_unique_keys(self, ti: TableInfo, columns: tuple) -> set:
        """Linear-scan the table and return the set of existing key tuples.

        Walks the on-disk data pages via :meth:`_scan_table`, projects
        only the requested ``columns`` into a tuple, and drops any tuple
        containing a NULL member (per R9 裁决 9).
        """
        name_to_idx = ti_name_index(ti)
        col_idxs = tuple(name_to_idx[c] for c in columns)
        seen: set = set()
        for _sid, vals, _pid in self._scan_table(ti):
            key = tuple(vals[i] for i in col_idxs)
            if any(v is None for v in key):
                continue
            seen.add(key)
        return seen

    def _insert_row_into_chain(self, ti: TableInfo, row_bytes: bytes) -> tuple[int, int]:
        """Dispatch to spill path or inline path; return (page_id, slot_id).

        Rows larger than ``MAX_INLINE_PAYLOAD`` cannot fit in a single data
        page slot, so we split them across a chain of ``page_type=2``
        overflow pages. Smaller rows take the original linear-probing path.
        The (page_id, slot_id) pair is the B+tree slot reference used to
        satisfy SELECT fast-path reads (Task 7).
        """
        if len(row_bytes) > MAX_INLINE_PAYLOAD:
            return self._insert_with_overflow(ti, row_bytes)
        return self._insert_inline_only(ti, row_bytes)

    def _insert_inline_only(self, ti: TableInfo, row_bytes: bytes) -> tuple[int, int]:
        """Walk the data-page chain, allocating a new page when full.

        Walks the table's tracked data page list (see ``_table_data_pages``)
        rather than relying on contiguous ``pid += 1`` arithmetic — DROP
        reclamation can return pages with ids smaller than ``ti.root`` from
        the LIFO free list, breaking the contiguous assumption. The list
        is initialized lazily from ``ti.root_page_id`` on first access.

        Skip-pages check (Task 7): any page id in ``_index_pages()`` (owned
        by a B+tree) is skipped, even if it appears in the tracked list.
        """
        index_pages = self._index_pages()
        data_pages = self._table_data_pages.setdefault(
            ti.name, [ti.root_page_id],
        )
        for pid in data_pages:
            if pid in index_pages:
                continue
            raw = self._txn_read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            try:
                sid = page.insert(row_bytes)
            except PageFull:
                continue
            self._txn_write_page(pid, page.to_bytes())
            self.pager.flush()
            return (pid, sid)
        # All current pages full (or empty list); allocate a fresh page and
        # append to the tracked list. ``_alloc_data_page`` filters out
        # B+tree pages so the new pid won't collide with any index.
        new_pid = self._alloc_data_page()
        data_pages.append(new_pid)
        ti.next_page_id = new_pid
        self._txn_write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        page = SlottedPage.from_bytes(new_pid, self._txn_read_page(new_pid))
        sid = page.insert(row_bytes)
        self._txn_write_page(new_pid, page.to_bytes())
        self.pager.flush()
        return (new_pid, sid)

    def _rebuild_data_pages_from_chain(self, ti: TableInfo) -> list[int]:
        """Walk ``ti``'s contiguous data chain and return the actual data page ids.

        Used by :meth:`Database.__init__` to rebuild the per-table data
        page list after a file reopen. The in-memory list maintained by
        :meth:`_insert_inline_only` is session-scoped, so persistence only
        carries the catalog's ``root_page_id`` / ``next_page_id``. We
        walk the catalog range with ``pid += 1`` (the chain is contiguous
        on a fresh open — reclamation hasn't happened yet) and skip any
        page id owned by a B+tree wrapper.

        If the chain is empty (``root_page_id == 0`` or ``next < root``),
        returns ``[]``.
        """
        if ti.root_page_id == 0 or ti.next_page_id < ti.root_page_id:
            return []
        index_pages = self._index_pages()
        data_pages: list[int] = []
        pid = ti.root_page_id
        end = ti.next_page_id
        while pid <= end:
            if pid not in index_pages:
                data_pages.append(pid)
            pid += 1
        return data_pages

    def _alloc_data_page(self) -> int:
        """Allocate a fresh data page that does NOT collide with any B+tree page.

        The Executor's data page chain and the IndexManager's B+tree pages
        share the pager's address space. A naive ``pager.alloc_page`` can
        return a page id already used by a B+tree root or leaf, causing
        the data write that follows to corrupt the index. We detect that
        and free the colliding page (push it back onto the free list)
        until we land on a page no B+tree owns.
        """
        reserved = self._index_pages()
        while True:
            pid = self.pager.alloc_page()
            if pid not in reserved:
                return pid
            # Collision: push this page back onto the free list and retry.
            self.pager.free_page(pid)
            reserved.add(pid)

    def _insert_with_overflow(self, ti: TableInfo, row_bytes: bytes) -> tuple[int, int]:
        """Spill a row exceeding MAX_INLINE into an overflow chain.

        The first chunk lands inline so the data page carries the slot entry (with
        FLAG_SPILL_START set) plus the first chunk of the row. Subsequent chunks go
        into overflow pages (page_type=2) linked via overflow_next.
        """
        first_chunk = row_bytes[:_CHUNK_SIZE]
        rest = row_bytes[_CHUNK_SIZE:]
        pid_first, sid_first = self._insert_inline_only(ti, first_chunk)
        # Mark SPILL_START on the slot that now holds the first chunk.
        page = SlottedPage.from_bytes(pid_first, self._txn_read_page(pid_first))
        page.slots[sid_first].flags |= FLAG_SPILL_START
        self._txn_write_page(pid_first, page.to_bytes())
        # Chain overflow pages; nxt placeholder is patched on the next iteration
        # (or stays NULL_PAGE_ID on the final page).
        prev_pid, prev_buf = pid_first, bytearray(self._txn_read_page(pid_first))
        while rest:
            chunk = rest[:_CHUNK_SIZE]
            rest = rest[_CHUNK_SIZE:]
            ov_pid = self.pager.alloc_page()
            nxt = NULL_PAGE_ID if not rest else 0
            ov_buf = bytearray(PAGE_SIZE)
            ov_buf[0] = 2  # page_type = overflow
            ov_buf[2:4] = (PAGE_SIZE - len(chunk)).to_bytes(2, "big")
            ov_buf[4:8] = nxt.to_bytes(4, "big")
            ov_buf[HEADER_SIZE:HEADER_SIZE + len(chunk)] = chunk
            self._txn_write_page(ov_pid, bytes(ov_buf))
            prev_buf[4:8] = ov_pid.to_bytes(4, "big")
            self._txn_write_page(prev_pid, bytes(prev_buf))
            prev_pid, prev_buf = ov_pid, ov_buf
        self.pager.flush()
        return (pid_first, sid_first)

    def _read_overflow_chain(self, start_pid: int) -> bytes:
        """Follow ``overflow_next`` from ``start_pid``; concatenate raw[16:] per page."""
        chunks: list[bytes] = []
        pid = int.from_bytes(self._txn_read_page(start_pid)[4:8], "big")
        while pid != NULL_PAGE_ID:
            raw = self._txn_read_page(pid)
            chunks.append(raw[HEADER_SIZE:])
            pid = int.from_bytes(raw[4:8], "big")
        return b"".join(chunks)

    def _free_overflow_chain(self, start_pid: int) -> None:
        """Mark every overflow page in the chain free (``page_type=0``); guard page_type==2."""
        nxt = int.from_bytes(self._txn_read_page(start_pid)[4:8], "big")
        while nxt != NULL_PAGE_ID:
            pid = nxt
            ov = bytearray(self._txn_read_page(pid))
            if ov[0] != 2:
                raise RuntimeError(f"overflow chain corruption: page {pid} page_type={ov[0]}, expected 2")
            nxt = int.from_bytes(ov[4:8], "big")
            ov[0] = 0
            self._txn_write_page(pid, bytes(ov))

    def _read_slot_row_bytes(
        self, page: "SlottedPage", sid: int, pid: int,
    ) -> Optional[bytes]:
        """Return the full row bytes for ``(page, sid)`` or ``None`` if absent.

        Single source of truth for the tombstone + spill-handling rules
        that used to be inlined in both :meth:`_scan_table` and
        :meth:`_read_row_by_slot`. Returns ``None`` when the slot is
        tombstoned, missing, or out of range; otherwise returns the
        decoded row bytes (with the overflow chain appended when the
        slot carries ``FLAG_SPILL_START``).
        """
        slot = page.slots[sid]
        if slot.flags & FLAG_TOMBSTONE:
            return None
        row_bytes = page.get(sid)
        if row_bytes is None:
            return None
        if slot.flags & FLAG_SPILL_START:
            row_bytes = row_bytes + self._read_overflow_chain(pid)
        return row_bytes

    def _scan_table(self, ti: TableInfo) -> list[tuple[int, list[Any], int]]:
        """Linear-scan all data pages, filtering tombstones.

        Walks the table's tracked data page list (see ``_table_data_pages``)
        rather than relying on contiguous ``pid += 1`` arithmetic from
        ``ti.root_page_id`` to ``ti.next_page_id`` — DROP reclamation can
        leave the chain non-monotonic in id. Each surviving slot is decoded
        via :func:`row_codec.decode_row` and returned as a 3-tuple of
        ``(slot_id, decoded_values, page_id)``. Slots with FLAG_SPILL_START
        have their inline first chunk concatenated with the overflow chain.
        """
        index_pages = self._index_pages()
        data_pages = self._table_data_pages.get(ti.name)
        if data_pages is None:
            # Legacy fallback (executor constructed without
            # ``_table_data_pages`` init): walk the catalog's contiguous
            # range. Used by standalone unit tests that don't go through
            # ``_exec_create_table``.
            data_pages = list(range(ti.root_page_id, ti.next_page_id + 1))
        results: list[tuple[int, list[Any], int]] = []
        for pid in data_pages:
            if pid in index_pages:
                continue
            raw = self._txn_read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            for sid in range(page.num_slots):
                row_bytes = self._read_slot_row_bytes(page, sid, pid)
                if row_bytes is None:
                    continue
                results.append((sid, decode_row(row_bytes, ti.schema_v2), pid))
        return results

    def _exec_select(self, stmt: Select):
        """Read rows from a table, with optional WHERE / GROUP BY / HAVING / ORDER BY / LIMIT / OFFSET / projection.

        Engine-v1 semantics (legacy / non-aggregate path):
          * WHERE supports the full Expr AST (EqualsExpr | AndExpr | OrExpr |
            NotExpr) via ``eval_expr``. AND/OR short-circuit on the first
            decisive branch (Python ``and``/``or``).
          * Literal type mismatches against the column's declared type raise
            :class:`TypeError` (preserves MVP behavior; spec REQ-PARSE-005-SCN-04).
          * Unknown columns raise :class:`ExecutionError`.
          * ``SELECT *`` projects every schema column in schema order; a
            named-column list projects in the order given by ``stmt.columns``.
          * Chain order: filter -> order_by -> offset -> limit -> project.
          * ORDER BY uses Python stable sort with multi-key comparator.
          * Negative LIMIT/OFFSET raise ExecutionError.
          * B+tree fast path: single-equality WHERE on an indexed column.

        tinydb-aggregation 5-phase pipeline (aggregate path):
          Phase 1: WHERE filter on raw rows.
          Phase 2: GROUP BY + aggregate.
          Phase 3: HAVING filter on aggregate rows.
          Phase 4: ORDER BY + LIMIT + OFFSET.
          Phase 5: Project to SELECT list shape.

        The aggregate path is taken iff ``stmt.aggregate_aliases`` is
        non-empty OR ``stmt.group_by`` is non-empty. Empty raw input on the
        aggregate path with no GROUP BY yields one row (standard SQL
        refinement); with GROUP BY it yields zero rows.

        Returns ``list[Row]`` on the aggregate path and ``list[list[Any]]``
        on the legacy path. ``database.py`` wraps both into ``Row`` based
        on the parser's projected column names (``stmt.columns``).
        """
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        # Use schema_v2 (3-tuple with type_params) so codec dispatch in
        # eval_expr / _stable_sort honors parametric types (Task 17).
        schema = ti.schema_v2

        proj_idx, aggregate_path = self._validate_select(stmt, ti, schema)

        # B+tree fast path (non-aggregate only).
        if not aggregate_path and stmt.where is not None:
            fast = self._exec_indexed_select(stmt, ti, schema, proj_idx)
            if fast is not None:
                return fast

        # Aggregate path (5-phase pipeline).
        if aggregate_path:
            return self._exec_aggregate_select(stmt, ti, schema)

        # Legacy / non-aggregate path.
        return self._exec_scan_select(stmt, ti, schema, proj_idx)

    def _validate_select(
        self,
        stmt: Select,
        ti: TableInfo,
        schema: list[tuple],
    ) -> tuple[list[int], bool]:
        """Validate SELECT clauses that must hold for any path.

        Returns ``(proj_idx, aggregate_path)``:
          * ``proj_idx``: ordered list of column indices for the
            named-column projection, or empty list for ``SELECT *``.
            Empty on the aggregate path (projection handled by
            ``_project_aggregate_row``).
          * ``aggregate_path``: True iff this query uses aggregates or
            GROUP BY and should be routed to the aggregation pipeline.
        """
        # Validate LIMIT/OFFSET non-negative.
        if stmt.offset is not None and stmt.offset < 0:
            raise ExecutionError(f"OFFSET must be non-negative, got {stmt.offset}")
        if stmt.limit is not None and stmt.limit < 0:
            raise ExecutionError(f"LIMIT must be non-negative, got {stmt.limit}")

        uses_agg = bool(stmt.aggregate_aliases)
        uses_group = bool(stmt.group_by)
        aggregate_path = uses_agg or uses_group

        # Validate ORDER BY columns up front so an unknown column surfaces
        # before sort. Aggregate-path order_by is validated in apply_order_limit_phase1.
        if stmt.order_by and not aggregate_path:
            name_to_idx_sort = schema_name_index(schema)
            for it in stmt.order_by:
                if it.column not in name_to_idx_sort:
                    raise ExecutionError(
                        f"unknown column {it.column!r} in ORDER BY"
                    )

        # Named-column projection: validate all column names up front so an
        # unknown column surfaces before we return any partial result.
        # Aggregation paths validate projection via _project_aggregate_row.
        proj_idx: list[int] = []
        if stmt.columns != ("*",) and not aggregate_path:
            name_to_idx = schema_name_index(schema)
            for cname in stmt.columns:
                if cname not in name_to_idx:
                    raise ExecutionError(f"unknown column {cname!r}")
                proj_idx.append(name_to_idx[cname])

        return proj_idx, aggregate_path

    def _exec_indexed_select(
        self,
        stmt: Select,
        ti: TableInfo,
        schema: list[tuple],
        proj_idx: list[int],
    ) -> Optional[list[list[Any]]]:
        """B+tree indexed-lookup fast path. Returns ``None`` to defer to scan.

        Single-equality WHERE on an indexed column short-circuits the full
        scan: encode the literal with the column's codec, look it up in the
        per-(table,col) B+tree, and read at most one row by (page_id,
        slot_id). OFFSET/LIMIT still apply; ORDER BY is a no-op for 1 row.

        Returns ``None`` when the WHERE is not a single-equality on an
        indexed column so the caller can fall through to scan.
        """
        if not self._is_single_eq_on_indexed(stmt.where, ti):
            return None
        col_name, lit_value = self._parse_single_eq(stmt.where)
        if col_name is None:
            return None
        col_obj = next((c for c in ti.columns if c.name == col_name), None)
        if col_obj is None:
            return None
        bt = self.index_manager.get_btree(ti.name, col_name)
        if bt is None:
            return None
        try:
            key = codec_for(col_obj.type, col_obj.type_params).encode_py(lit_value)
            ref = self.index_manager.lookup_key(ti.name, col_name, key)
        except CodecError:
            ref = None
        if ref is None:
            return []
        fast_rows = self._read_row_by_slot(ti, ref)
        return self._slice_and_project(
            stmt, fast_rows, proj_idx, _project_legacy_row,
        )

    def _exec_aggregate_select(
        self,
        stmt: Select,
        ti: TableInfo,
        schema: list[tuple],
    ) -> list:
        """Aggregate 5-phase pipeline.

        Phase 1: WHERE filter on raw rows.
        Phase 2: GROUP BY + aggregate.
        Phase 3: HAVING filter on aggregate rows.
        Phase 4: ORDER BY + LIMIT + OFFSET (phase1 minimal).
        Phase 5: project to SELECT list shape.
        """
        if not stmt.select_items:
            raise ExecutionError(
                "SELECT * with GROUP BY requires explicit select_items",
            )
        # Phase 1: WHERE filter on raw rows.
        raw_rows: list[list[Any]] = []
        for _sid, vals, _pid in self._scan_table(ti):
            if stmt.where is not None and not eval_expr(stmt.where, vals, schema):
                continue
            raw_rows.append(list(vals))
        # Phase 2: GROUP BY + aggregate
        agg_rows = apply_aggregation(raw_rows, stmt, schema)
        # Phase 3: HAVING
        if stmt.having is not None:
            agg_rows = apply_having(
                agg_rows, stmt.having, stmt.aggregate_aliases, stmt.group_by,
                schema,
            )
        # Phase 4: ORDER BY + LIMIT + OFFSET (phase1 minimal)
        if stmt.order_by:
            agg_rows = apply_order_limit_phase1(
                agg_rows, stmt.order_by, stmt.aggregate_aliases, stmt.group_by,
            )
        agg_rows = _apply_limit_offset(stmt, agg_rows)
        # Phase 5: project to SELECT list shape; returns Row objects.
        return [_project_aggregate_row(r, stmt, schema) for r in agg_rows]

    def _exec_scan_select(
        self,
        stmt: Select,
        ti: TableInfo,
        schema: list[tuple],
        proj_idx: list[int],
    ) -> list[list[Any]]:
        """Legacy scan path: WHERE filter -> ORDER BY -> OFFSET/LIMIT -> project."""
        rows: list[tuple[int, list[Any], int]] = []
        for sid, vals, pid in self._scan_table(ti):
            if stmt.where is not None and not eval_expr(stmt.where, vals, schema):
                continue
            rows.append((sid, vals, pid))

        if stmt.order_by:
            rows = self._stable_sort(rows, stmt.order_by, schema)

        return self._slice_and_project(
            stmt, rows, proj_idx, _project_legacy_row,
        )

    def _slice_and_project(
        self,
        stmt: Select,
        rows: list[tuple[int, list[Any], int]],
        proj_idx: list[int],
        project,
    ) -> list[list[Any]]:
        """Apply OFFSET / LIMIT / projection to a list of (sid, vals, pid) rows.

        ``project`` is the per-row projector (e.g. ``_project_legacy_row``)
        so callers can supply the legacy-row or aggregate-row projection
        without duplicating the slice-and-iterate scaffold.
        """
        rows = _apply_limit_offset(stmt, rows)
        results: list[list[Any]] = []
        for _sid, vals, _pid in rows:
            results.append(project(stmt, vals, proj_idx))
        return results

    def _stable_sort(
        self,
        rows: list[tuple[int, list[Any], int]],
        items: tuple,
        schema: list[tuple[str, str, tuple]],
    ) -> list[tuple[int, list[Any], int]]:
        """Stable multi-key sort by OrderByItem list.

        Delegates to :func:`tinydb._executor_sort.stable_sort`; the
        implementation lives in ``_executor_sort.py`` to keep
        ``executor.py`` under its line budget (Risk R7).
        """
        from tinydb._executor_sort import stable_sort

        return stable_sort(rows, items, schema)

    def _exec_delete(self, stmt: Delete) -> list:
        """Delete rows matching the WHERE clause (or every row if no WHERE).

        MVP semantics:
          * WHERE supports only ``col = literal`` (same restriction as SELECT).
          * Literal type mismatches raise :class:`TypeError` (spec).
          * Each match is marked as a tombstone via :meth:`SlottedPage.delete`
            — pages are rewritten and flushed in a batch. ``(page_id, slot_id)``
            pairs are collected first so the scan does not observe mid-flight
            mutations of pages that have not yet been re-scanned.
          * Returns ``[]`` (mutation DML has no result data).
        """
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema_v2

        # Collect (page_id, slot_id, vals) triples first to avoid mutating pages
        # while we are still scanning them. The vals are needed for index
        # maintenance (Task 7): DELETE must clear the B+tree entry, which
        # requires encoding the indexed-column key BEFORE the slot is
        # tombstoned.
        to_delete: list[tuple[int, int, list]] = []
        for sid, vals, pid in self._scan_table(ti):
            if stmt.where is None or eval_expr(stmt.where, vals, schema):
                to_delete.append((pid, sid, vals))

        for pid, sid, vals in to_delete:
            raw = self._txn_read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            if page.slots[sid].flags & FLAG_SPILL_START:
                self._free_overflow_chain(pid)
            page.delete(sid)
            self._txn_write_page(pid, page.to_bytes())
            # Tombstone the B+tree entry for every indexed column.
            self._unindex_row(ti, vals)
        if to_delete:
            self.pager.flush()
        return []

    def _exec_update(self, stmt: Update) -> list:
        """UPDATE <table> SET <col=lit>[, ...] [WHERE <expr>]. Returns [].

        Algorithm:
          1. Validate SET columns and literal types against the schema.
          2. Scan matching (sid, vals, pid) rows via eval_expr.
          3. Group matches by page id; process each page independently.
          4. For each match: build new row bytes, try in-place update on the
             same slot; if grew, free the overflow chain (if any), delete
             the slot, then re-insert in the same page (PageFull triggers a
             queue to chain-insert after page flush).
          5. Flush page, then drain pending chain inserts.

        Returns ``[]`` (DML protocol).
        """
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema_v2

        # 1) Validate SET columns + literal types
        col_name_to_idx = self._validate_update_sets(stmt, schema)

        # 2-3) Collect matches and group by page
        matches = self._collect_update_matches(stmt, ti, schema)
        by_page = _group_matches_by_page(matches)

        # 4-5) Apply updates page by page; drain pending chain inserts after each flush
        for pid, sid_vals_list in by_page.items():
            page = SlottedPage.from_bytes(pid, self._txn_read_page(pid))
            pending_chain_inserts = self._apply_page_updates(
                ti, pid, page, sid_vals_list, stmt, schema, col_name_to_idx,
            )
            # Flush this page before chain inserts (may advance next_page_id).
            self._txn_write_page(pid, page.to_bytes())
            for old_vals, new_vals, new_bytes in pending_chain_inserts:
                new_pid, new_sid = self._insert_row_into_chain(ti, new_bytes)
                # Old slot is tombstoned (different page now); delete old
                # index keys, insert new with the new (pid, sid) ref.
                self._update_index_for_row(
                    ti, old_vals, new_vals, (new_pid, new_sid),
                )

        self.pager.flush()
        return []

    def _validate_update_sets(
        self, stmt: Update, schema: list[tuple],
    ) -> dict[str, int]:
        """Validate SET clauses against the schema. Returns name->index map.

        For each ``col = literal`` assignment:
          * column must exist in the schema
          * right-hand side must be an ``EqualsExpr`` literal (no expressions)
          * literal value must satisfy the column's codec
        """
        col_name_to_idx = schema_name_index(schema)
        for col_name, expr in stmt.sets:
            if col_name not in col_name_to_idx:
                raise ExecutionError(f"unknown column {col_name!r}")
            if not isinstance(expr, EqualsExpr):
                raise ExecutionError("SET right-hand side must be a literal")
            col_type, col_params = col_type_and_params(
                schema[col_name_to_idx[col_name]],
            )
            try:
                codec_for(col_type, col_params).validate(expr.value)
            except CodecError as e:
                raise TypeError(
                    f"{col_type} vs {_python_type_to_db_type(expr.value)}: {e}"
                ) from e
        return col_name_to_idx

    def _collect_update_matches(
        self,
        stmt: Update,
        ti: TableInfo,
        schema: list[tuple],
    ) -> list[tuple[int, int, list[Any]]]:
        """Return ``[(page_id, slot_id, vals), ...]`` for rows matching WHERE."""
        matches: list[tuple[int, int, list[Any]]] = []
        for sid, vals, pid in self._scan_table(ti):
            if stmt.where is None or eval_expr(stmt.where, vals, schema):
                matches.append((pid, sid, vals))
        return matches

    def _apply_page_updates(
        self,
        ti: TableInfo,
        pid: int,
        page: "SlottedPage",
        sid_vals_list: list[tuple[int, list[Any]]],
        stmt: Update,
        schema: list[tuple],
        col_name_to_idx: dict[str, int],
    ) -> list[tuple[list, list, bytes]]:
        """Apply in-place updates on a single page; return chain-insert queue.

        For each (sid, vals):
          * build new row bytes from ``stmt.sets``
          * try in-place update; on PageFull, fall back to delete+insert
          * if the fallback also overflows the page, queue for chain insert
        Updates ``page`` in-place (caller flushes). The ``pid`` parameter
        is needed for index maintenance and overflow-chain reclamation
        because both reference the underlying page id.
        """
        pending_chain_inserts: list[tuple[list, list, bytes]] = []
        for sid, vals in sid_vals_list:
            old_vals = list(vals)
            new_vals = list(vals)
            for col_name, expr in stmt.sets:
                new_vals[col_name_to_idx[col_name]] = expr.value
            new_bytes = encode_row(new_vals, schema)

            old_slot = page.slots[sid]
            grew = len(new_bytes) > old_slot.length
            if not grew:
                try:
                    page.update(sid, new_bytes)
                    # In-place: slot_ref stays (pid, sid).
                    self._update_index_for_row(
                        ti, old_vals, new_vals, (pid, sid),
                    )
                    continue
                except PageFull:
                    grew = True
            # Fallback: grew == True -> delete + insert (or chain)
            if old_slot.flags & FLAG_SPILL_START:
                self._free_overflow_chain(pid)
            page.delete(sid)
            try:
                new_sid = page.insert(new_bytes)
                self._update_index_for_row(
                    ti, old_vals, new_vals, (pid, new_sid),
                )
            except PageFull:
                pending_chain_inserts.append((old_vals, new_vals, new_bytes))
        return pending_chain_inserts

    # --- B+tree fast-path helpers (Task 7) ----------------------------------

    def _index_row(
        self, ti: TableInfo, row_vals: list, slot_ref: tuple[int, int],
    ) -> None:
        """Insert (key, slot_ref) into the B+tree for every indexed column.

        NULL values are NOT indexed (R9 裁决 9 — SQL standard semantics that
        treats NULL as unknown, never equal to anything, including itself).
        Indexed columns are PK + UNIQUE columns (per IndexManager.indexed_columns).
        """
        name_to_idx = {c.name: i for i, c in enumerate(ti.columns)}
        for col in self.index_manager.indexed_columns(ti):
            val = row_vals[name_to_idx[col.name]]
            if val is None:
                continue
            key = self.index_manager.key_for(col, val)
            self.index_manager.insert(ti.name, col.name, key, slot_ref)

    def _unindex_row(
        self, ti: TableInfo, row_vals: list,
    ) -> None:
        """Remove (key, _) from the B+tree for every indexed column.

        Tombstones the entry but does not free the underlying B+tree page;
        the entry is reclaimed on a future split/merge (out of scope for MVP).
        NULL values were never indexed, so they are skipped.
        """
        name_to_idx = {c.name: i for i, c in enumerate(ti.columns)}
        for col in self.index_manager.indexed_columns(ti):
            val = row_vals[name_to_idx[col.name]]
            if val is None:
                continue
            key = self.index_manager.key_for(col, val)
            self.index_manager.delete(ti.name, col.name, key)

    def _update_index_for_row(
        self,
        ti: TableInfo,
        old_vals: list,
        new_vals: list,
        new_slot_ref: tuple[int, int],
    ) -> None:
        """Reconcile B+tree entries for a row whose values changed.

        For each indexed column (PK + UNIQUE):
          * old == new: no-op (already points at the right slot).
          * both non-NULL differ: delete old key, insert new key.
          * old non-NULL, new NULL: delete old key (NULL not indexed).
          * old NULL, new non-NULL: insert new key.
          * both NULL: no-op (NULL never indexed).

        Delete-then-insert ordering matters when the new key collides with
        a DIFFERENT row's old key: tombstoning first lets B+tree.insert
        upsert the entry under the new key without confusion.
        """
        name_to_idx = {c.name: i for i, c in enumerate(ti.columns)}
        for col in self.index_manager.indexed_columns(ti):
            i = name_to_idx[col.name]
            old_v = old_vals[i]
            new_v = new_vals[i]
            if old_v == new_v:
                continue
            if old_v is not None:
                old_key = self.index_manager.key_for(col, old_v)
                self.index_manager.delete(ti.name, col.name, old_key)
            if new_v is not None:
                new_key = self.index_manager.key_for(col, new_v)
                self.index_manager.insert(
                    ti.name, col.name, new_key, new_slot_ref,
                )

    def _is_single_eq_on_indexed(self, expr: Any, ti: TableInfo) -> bool:
        """True iff ``expr`` is a single ``EqualsExpr`` on a column with a B+tree."""
        if not isinstance(expr, EqualsExpr):
            return False
        return self.index_manager.get_btree(ti.name, expr.column) is not None

    def _parse_single_eq(self, expr: Any) -> tuple[Optional[str], Any]:
        """Return (column_name, literal_value) for an ``EqualsExpr``, else (None, None)."""
        if isinstance(expr, EqualsExpr):
            return expr.column, expr.value
        return None, None

    def _read_row_by_slot(
        self, ti: TableInfo, slot_ref: tuple[int, int],
    ) -> list[tuple[int, list[Any], int]]:
        """Read one row from a (page_id, slot_id) reference.

        Returns ``[(slot_id, decoded_values, page_id)]`` on hit; ``[]`` if
        the slot has been tombstoned since the index was last updated
        (defensive — should not happen with correct maintenance).
        """
        page_id, slot_id = slot_ref
        raw = self._txn_read_page(page_id)
        page = SlottedPage.from_bytes(page_id, raw)
        row_bytes = self._read_slot_row_bytes(page, slot_id, page_id)
        if row_bytes is None:
            return []
        return [(slot_id, decode_row(row_bytes, ti.schema_v2), page_id)]


def _group_matches_by_page(
    matches: list[tuple[int, int, list[Any]]],
) -> dict[int, list[tuple[int, list[Any]]]]:
    """Group UPDATE matches by page id (drops the page id from each triple)."""
    by_page: dict[int, list[tuple[int, list[Any]]]] = {}
    for pid, sid, vals in matches:
        by_page.setdefault(pid, []).append((sid, vals))
    return by_page