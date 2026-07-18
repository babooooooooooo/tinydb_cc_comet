"""AST -> storage executor. Owns Pager+Catalog; all I/O lives here. <= 400 lines.

The Executor is the single bridge between parsed SQL statements and the
on-disk storage layer (Pager + Catalog + SlottedPage). It dispatches each
AST node to a dedicated ``_exec_*`` method. DDL (CREATE/DROP TABLE) is
fully implemented; DML (INSERT/SELECT/DELETE) is wired in and Task 19
finishes SELECT projection/WHERE/DELETE tombstone on top of Task 18's
INSERT + linear scan helper.
"""
from collections import defaultdict
from typing import Any, Optional, Union
from functools import cmp_to_key

from tinydb.catalog import Catalog, TableInfo
from tinydb.errors import ConstraintViolation, ExecutionError, PageFull
from tinydb.pager import Pager
from tinydb.parser import (
    CreateTable, DropTable, Insert, Select, Delete, Update,
    EqualsExpr, AndExpr, OrExpr, NotExpr, OrderByItem,
)
from tinydb.row_codec import decode_row, encode_row
from tinydb.slotted_page import (
    FLAG_SPILL_START, FLAG_TOMBSTONE, HEADER_SIZE, MAX_INLINE_PAYLOAD,
    NULL_PAGE_ID, PAGE_SIZE, SLOT_SIZE, SlottedPage,
)
from tinydb.type_system import (
    codec_for,
    infer_literal_type,
    validate_compare_types,
)

# MAX_INLINE_PAYLOAD = 4078; subtract SLOT_SIZE so an inline first chunk on
# an empty page leaves room for the slot directory entry (no overlap).
_CHUNK_SIZE = MAX_INLINE_PAYLOAD - SLOT_SIZE  # 4072


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
            (i for i, (n, _, *_) in enumerate(schema) if n == expr.column),
            None,
        )
        if col_idx is None:
            raise ExecutionError(f"unknown column {expr.column!r}")
        col_type = schema[col_idx][1]
        col_params = schema[col_idx][2] if len(schema[col_idx]) >= 3 else ()
        # Strict same-type check first (Design D6 / Task 18): if the parsed
        # literal's inferred DB type or its params disagree with the column
        # declaration, raise TypeError before any byte encoding happens.
        lit_type, lit_params = infer_literal_type(expr.value)
        validate_compare_types(col_type, col_params, lit_type, lit_params)
        try:
            codec_for(col_type, col_params).validate(expr.value)
        except (TypeError, ValueError, OverflowError) as e:
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


class Executor:
    """Drive AST statements against a (Pager, Catalog) pair.

    The Executor holds mutable references to the supplied Pager and Catalog;
    it does not own their lifetime — the caller is responsible for closing
    the Pager. Each ``execute`` call mutates the Catalog in-place and
    flushes page 1 (the catalog slot) back to disk.
    """

    def __init__(self, pager: Pager, catalog: Catalog) -> None:
        self.pager = pager
        self.catalog = catalog

    # --- public dispatch ----------------------------------------------------

    def execute(
        self, stmt: Union[CreateTable, DropTable, Insert, Select, Delete, Update],
    ) -> Union[list, list[list[Any]]]:
        """Dispatch ``stmt`` to its ``_exec_*`` handler.

        Returns the handler's result (DDL returns ``[]``; DML returns row
        lists). Unknown AST types raise ``ExecutionError``.
        """
        dispatch = {
            CreateTable: self._exec_create_table,
            DropTable:   self._exec_drop_table,
            Insert:      self._exec_insert,
            Select:      self._exec_select,
            Delete:      self._exec_delete,
            Update:      self._exec_update,
        }
        handler = dispatch.get(type(stmt))
        if handler is None:
            raise ExecutionError(f"unsupported statement: {type(stmt).__name__}")
        return handler(stmt)

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
        self.pager.write_page(root_id, page.to_bytes())

        # MVP: next_page_id == root_page_id.
        self.catalog.create_table(
            stmt.name, tuple(cols),
            root_page_id=root_id, next_page_id=root_id,
        )

        self.pager.write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        return []

    def _exec_drop_table(self, stmt: DropTable) -> list:
        """Remove a table from the catalog.

        MVP behavior: best-effort drop that leaks the table's root page(s).
        Page recycling lands in Task 21 (overflow chain + free-page list).
        The missing-table case surfaces as ``ExecutionError`` so callers
        see a single uniform error type at the execution boundary.
        """
        if self.catalog.get_table(stmt.name) is None:
            raise ExecutionError(f"table {stmt.name!r} does not exist")

        # MVP: best-effort, leak page (Task 21 will reclaim).
        self.catalog.drop_table(stmt.name)
        self.pager.write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        return []

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
        name_to_idx: dict[str, int] = {c.name: i for i, c in enumerate(cols)}

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
                self._insert_row_into_chain(ti, row_bytes)
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
        name_to_idx = {c.name: i for i, c in enumerate(ti.columns)}
        col_idxs = tuple(name_to_idx[c] for c in columns)
        seen: set = set()
        for _sid, vals, _pid in self._scan_table(ti):
            key = tuple(vals[i] for i in col_idxs)
            if any(v is None for v in key):
                continue
            seen.add(key)
        return seen

    def _insert_row_into_chain(self, ti: TableInfo, row_bytes: bytes) -> int:
        """Dispatch to spill path or inline path.

        Rows larger than ``MAX_INLINE_PAYLOAD`` cannot fit in a single data
        page slot, so we split them across a chain of ``page_type=2``
        overflow pages. Smaller rows take the original linear-probing path.
        """
        if len(row_bytes) > MAX_INLINE_PAYLOAD:
            return self._insert_with_overflow(ti, row_bytes)
        return self._insert_inline_only(ti, row_bytes)

    def _insert_inline_only(self, ti: TableInfo, row_bytes: bytes) -> int:
        """Walk the data-page chain, allocating a new page when full.

        Starts at ``ti.root_page_id`` and tries ``SlottedPage.insert`` on
        each page in turn. On :class:`PageFull`, advances to the next page
        id; when the chain tail (``ti.next_page_id``) is reached, a fresh
        page is allocated, ``ti.next_page_id`` is advanced, and the catalog
        is persisted. Returns the page id that accepted the row.
        """
        pid = ti.root_page_id
        while True:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            try:
                page.insert(row_bytes)
            except PageFull:
                if pid == ti.next_page_id:
                    new_pid = self.pager.alloc_page()
                    ti.next_page_id = new_pid
                    self.pager.write_page(1, self.catalog.to_bytes())
                    self.pager.flush()
                    pid = new_pid
                    continue
                pid += 1
                continue
            self.pager.write_page(pid, page.to_bytes())
            self.pager.flush()
            return pid

    def _insert_with_overflow(self, ti: TableInfo, row_bytes: bytes) -> int:
        """Spill a row exceeding MAX_INLINE into an overflow chain.

        The first chunk lands inline so the data page carries the slot entry (with
        FLAG_SPILL_START set) plus the first chunk of the row. Subsequent chunks go
        into overflow pages (page_type=2) linked via overflow_next.
        """
        first_chunk = row_bytes[:_CHUNK_SIZE]
        rest = row_bytes[_CHUNK_SIZE:]
        pid_first = self._insert_inline_only(ti, first_chunk)
        # Mark SPILL_START on the slot that now holds the first chunk.
        page = SlottedPage.from_bytes(pid_first, self.pager.read_page(pid_first))
        page.slots[page.num_slots - 1].flags |= FLAG_SPILL_START
        self.pager.write_page(pid_first, page.to_bytes())
        # Chain overflow pages; nxt placeholder is patched on the next iteration
        # (or stays NULL_PAGE_ID on the final page).
        prev_pid, prev_buf = pid_first, bytearray(self.pager.read_page(pid_first))
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
            self.pager.write_page(ov_pid, bytes(ov_buf))
            prev_buf[4:8] = ov_pid.to_bytes(4, "big")
            self.pager.write_page(prev_pid, bytes(prev_buf))
            prev_pid, prev_buf = ov_pid, ov_buf
        self.pager.flush()
        return pid_first

    def _read_overflow_chain(self, start_pid: int) -> bytes:
        """Follow ``overflow_next`` from ``start_pid``; concatenate raw[16:] per page."""
        chunks: list[bytes] = []
        pid = int.from_bytes(self.pager.read_page(start_pid)[4:8], "big")
        while pid != NULL_PAGE_ID:
            raw = self.pager.read_page(pid)
            chunks.append(raw[HEADER_SIZE:])
            pid = int.from_bytes(raw[4:8], "big")
        return b"".join(chunks)

    def _free_overflow_chain(self, start_pid: int) -> None:
        """Mark every overflow page in the chain free (``page_type=0``); guard page_type==2."""
        nxt = int.from_bytes(self.pager.read_page(start_pid)[4:8], "big")
        while nxt != NULL_PAGE_ID:
            pid = nxt
            ov = bytearray(self.pager.read_page(pid))
            if ov[0] != 2:
                raise RuntimeError(f"overflow chain corruption: page {pid} page_type={ov[0]}, expected 2")
            nxt = int.from_bytes(ov[4:8], "big")
            ov[0] = 0
            self.pager.write_page(pid, bytes(ov))

    def _scan_table(self, ti: TableInfo) -> list[tuple[int, list[Any], int]]:
        """Linear-scan all data pages, filtering tombstones.

        Iterates page ids from ``ti.root_page_id`` through
        ``ti.next_page_id`` inclusive. Each surviving slot is decoded via
        :func:`row_codec.decode_row` and returned as a 3-tuple of
        ``(slot_id, decoded_values, page_id)``. Slots with FLAG_SPILL_START
        have their inline first chunk concatenated with the overflow chain.
        """
        pid = ti.root_page_id
        results: list[tuple[int, list[Any], int]] = []
        while True:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            for sid in range(page.num_slots):
                slot = page.slots[sid]
                if slot.flags & FLAG_TOMBSTONE:
                    continue
                row_bytes = page.get(sid)
                if row_bytes is None:  # tombstone or out-of-range slot
                    continue
                if slot.flags & FLAG_SPILL_START:
                    row_bytes = row_bytes + self._read_overflow_chain(pid)
                results.append((sid, decode_row(row_bytes, ti.schema_v2), pid))
            if pid == ti.next_page_id:
                break
            pid += 1
        return results

    def _exec_select(self, stmt: Select) -> list[list[Any]]:
        """Read rows from a table, applying WHERE filter, ORDER BY, OFFSET, LIMIT, projection.

        Engine-v1 semantics:
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

        Returns ``list[list]`` of decoded values — Task 20 wraps each row in
        a ``Row`` object; until then the raw lists are the public contract.
        """
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        # Use schema_v2 (3-tuple with type_params) so codec dispatch in
        # eval_expr / _stable_sort honors parametric types (Task 17).
        schema = ti.schema_v2

        # Validate LIMIT/OFFSET non-negative.
        if stmt.offset is not None and stmt.offset < 0:
            raise ExecutionError(f"OFFSET must be non-negative, got {stmt.offset}")
        if stmt.limit is not None and stmt.limit < 0:
            raise ExecutionError(f"LIMIT must be non-negative, got {stmt.limit}")

        # Validate ORDER BY columns up front so an unknown column surfaces
        # before sort (which would otherwise never check on a 1-row table).
        if stmt.order_by:
            name_to_idx_sort = {n: i for i, (n, _, *_) in enumerate(schema)}
            for it in stmt.order_by:
                if it.column not in name_to_idx_sort:
                    raise ExecutionError(
                        f"unknown column {it.column!r} in ORDER BY"
                    )

        # Named-column projection: validate all column names up front so an
        # unknown column surfaces before we return any partial result.
        proj_idx: list[int] = []
        if stmt.columns != ("*",):
            name_to_idx = {n: i for i, (n, _, *_) in enumerate(schema)}
            for cname in stmt.columns:
                if cname not in name_to_idx:
                    raise ExecutionError(f"unknown column {cname!r}")
                proj_idx.append(name_to_idx[cname])

        # filter + collect (sid, vals, pid) for stable sort
        rows: list[tuple[int, list[Any], int]] = []
        for sid, vals, pid in self._scan_table(ti):
            if stmt.where is not None and not eval_expr(stmt.where, vals, schema):
                continue
            rows.append((sid, vals, pid))

        if stmt.order_by:
            rows = self._stable_sort(rows, stmt.order_by, schema)

        if stmt.offset:
            rows = rows[stmt.offset:]
        if stmt.limit is not None:
            rows = rows[:stmt.limit]

        results: list[list[Any]] = []
        for _sid, vals, _pid in rows:
            if stmt.columns == ("*",):
                results.append(list(vals))
            else:
                results.append([vals[i] for i in proj_idx])
        return results

    def _stable_sort(
        self,
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
        name_to_idx = {n: i for i, (n, _, *_) in enumerate(schema)}

        def cmp(r1: tuple, r2: tuple) -> int:
            for it in items:
                if it.column not in name_to_idx:
                    raise ExecutionError(
                        f"unknown column {it.column!r} in ORDER BY"
                    )
                i = name_to_idx[it.column]
                v1, v2 = r1[1][i], r2[1][i]
                col_type = schema[i][1]
                col_params = schema[i][2] if len(schema[i]) >= 3 else ()
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

        # Collect (page_id, slot_id) pairs first to avoid mutating pages
        # while we are still scanning them.
        to_delete: list[tuple[int, int]] = []
        for sid, vals, pid in self._scan_table(ti):
            if stmt.where is None or eval_expr(stmt.where, vals, schema):
                to_delete.append((pid, sid))

        for pid, sid in to_delete:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            if page.slots[sid].flags & FLAG_SPILL_START:
                self._free_overflow_chain(pid)
            page.delete(sid)
            self.pager.write_page(pid, page.to_bytes())
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
        col_name_to_idx = {n: i for i, (n, _, *_) in enumerate(schema)}
        for col_name, expr in stmt.sets:
            if col_name not in col_name_to_idx:
                raise ExecutionError(f"unknown column {col_name!r}")
            if not isinstance(expr, EqualsExpr):
                raise ExecutionError("SET right-hand side must be a literal")
            col_type = schema[col_name_to_idx[col_name]][1]
            col_params = schema[col_name_to_idx[col_name]][2] if len(schema[col_name_to_idx[col_name]]) >= 3 else ()
            try:
                codec_for(col_type, col_params).validate(expr.value)
            except (TypeError, ValueError, OverflowError) as e:
                raise TypeError(
                    f"{col_type} vs {_python_type_to_db_type(expr.value)}: {e}"
                ) from e

        # 2) Collect matches
        matches: list[tuple[int, int, list[Any]]] = []
        for sid, vals, pid in self._scan_table(ti):
            if stmt.where is None or eval_expr(stmt.where, vals, schema):
                matches.append((pid, sid, vals))

        # 3) Group by page
        by_page: dict[int, list[tuple[int, list[Any]]]] = {}
        for pid, sid, vals in matches:
            by_page.setdefault(pid, []).append((sid, vals))

        # 4) Per-page in-place update + fallback
        for pid, sid_vals_list in by_page.items():
            page = SlottedPage.from_bytes(pid, self.pager.read_page(pid))
            pending_chain_inserts: list[bytes] = []
            for sid, vals in sid_vals_list:
                new_vals = list(vals)
                for col_name, expr in stmt.sets:
                    new_vals[col_name_to_idx[col_name]] = expr.value
                new_bytes = encode_row(new_vals, schema)

                old_slot = page.slots[sid]
                grew = len(new_bytes) > old_slot.length
                if not grew:
                    try:
                        page.update(sid, new_bytes)
                        continue
                    except PageFull:
                        grew = True
                # Fallback: grew == True → delete + insert (or chain)
                if old_slot.flags & FLAG_SPILL_START:
                    self._free_overflow_chain(pid)
                page.delete(sid)
                try:
                    page.insert(new_bytes)
                except PageFull:
                    pending_chain_inserts.append(new_bytes)

            # Flush this page before chain inserts (may advance next_page_id).
            self.pager.write_page(pid, page.to_bytes())
            for new_bytes in pending_chain_inserts:
                self._insert_row_into_chain(ti, new_bytes)

        self.pager.flush()
        return []