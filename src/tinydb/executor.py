"""AST -> storage executor. Owns Pager+Catalog; all I/O lives here. <= 400 lines.

The Executor is the single bridge between parsed SQL statements and the
on-disk storage layer (Pager + Catalog + SlottedPage). It dispatches each
AST node to a dedicated ``_exec_*`` method. DDL (CREATE/DROP TABLE) is
fully implemented; DML (INSERT/SELECT/DELETE) is wired in and Task 19
finishes SELECT projection/WHERE/DELETE tombstone on top of Task 18's
INSERT + linear scan helper.
"""
from typing import Any, Optional, Union

from tinydb.catalog import Catalog, TableInfo
from tinydb.errors import ExecutionError, PageFull
from tinydb.pager import Pager
from tinydb.parser import CreateTable, DropTable, Insert, Select, Delete
from tinydb.row_codec import decode_row, encode_row
from tinydb.slotted_page import (
    FLAG_SPILL_START, FLAG_TOMBSTONE, HEADER_SIZE, MAX_INLINE_PAYLOAD,
    NULL_PAGE_ID, PAGE_SIZE, SLOT_SIZE, SlottedPage,
)
from tinydb.type_system import py_to_db

# MAX_INLINE_PAYLOAD = 4078; subtract SLOT_SIZE so an inline first chunk on
# an empty page leaves room for the slot directory entry (no overlap).
_CHUNK_SIZE = MAX_INLINE_PAYLOAD - SLOT_SIZE  # 4072


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
        self, stmt: Union[CreateTable, DropTable, Insert, Select, Delete],
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
        from tinydb.errors import ConstraintViolation

        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        if not stmt.columns:
            raise ExecutionError("INSERT column list must be non-empty")

        cols = ti.columns
        name_to_idx = {c.name: i for i, c in enumerate(cols)}

        # Defensive executor-side validation; parser also enforces these.
        seen: set = set()
        for cname in stmt.columns:
            if cname not in name_to_idx:
                raise ExecutionError(f"unknown column {cname!r}")
            if cname in seen:
                raise ExecutionError(f"duplicate column {cname!r}")
            seen.add(cname)

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
                    raise ConstraintViolation(
                        kind="null", column=c.name, value=None,
                    )

            # 6. Type validation (existing path: only non-None values).
            validated: list = []
            for c, v in zip(cols, normalized_tuple):
                if v is None:
                    validated.append(None)
                    continue
                try:
                    py_to_db(v, c.type)
                except (TypeError, ValueError) as e:
                    raise ExecutionError(f"column {c.name}: {e}") from e
                validated.append(v)

            # 8. Encode + insert (Task 10 inserts unique check between 7 and 8).
            row_bytes = encode_row(validated, ti.schema)
            self._insert_row_into_chain(ti, row_bytes)
        return []

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
                results.append((sid, decode_row(row_bytes, ti.schema), pid))
            if pid == ti.next_page_id:
                break
            pid += 1
        return results

    def _resolve_where(
        self,
        stmt_where: Optional[tuple[str, str, Any]],
        schema: list[tuple[str, str]],
    ) -> Optional[tuple[int, str, str, Any]]:
        """Validate WHERE clause and return ``(col_idx, col_type, op, lit)``.

        Returns ``(None, None, None, None)`` when ``stmt_where`` is None.
        Raises :class:`TypeError` on literal/column type mismatch (spec
        §REQ-PARSE-005-SCN-04 mandates TypeError, not ExecutionError).
        Raises :class:`ExecutionError` on unknown column or unsupported op.
        """
        if stmt_where is None:
            return (None, None, None, None)
        col_name, op, lit = stmt_where
        col_idx = next(
            (i for i, (n, _) in enumerate(schema) if n == col_name), None,
        )
        if col_idx is None:
            raise ExecutionError(f"unknown column {col_name!r}")
        col_type = schema[col_idx][1]
        # MVP guard: parser already restricts to '='; re-check defensively.
        if op != "=":
            raise ExecutionError(
                f"operator {op!r} not supported; MVP supports only ="
            )
        try:
            py_to_db(lit, col_type)
        except (TypeError, ValueError) as e:
            raise TypeError(
                f"{col_type} vs {_python_type_to_db_type(lit)}: {e}"
            ) from e
        return (col_idx, col_type, op, lit)

    def _exec_select(self, stmt: Select) -> list[list[Any]]:
        """Read rows from a table, applying WHERE filter and column projection.

        MVP semantics:
          * WHERE supports only ``col = literal`` (parser already restricts
            other operators per REQ-PARSE-005-SCN-04; this layer defensively
            re-checks).
          * Literal type mismatches against the column's declared type raise
            :class:`TypeError` with messages shaped like ``"INT vs TEXT: ..."``
            (spec §REQ-PARSE-005-SCN-04).
          * ``SELECT *`` projects every schema column in schema order; a
            named-column list projects in the order given by ``stmt.columns``.
          * Unknown table or column names raise :class:`ExecutionError`.

        Returns ``list[list]`` of decoded values — Task 20 wraps each row in
        a ``Row`` object; until then the raw lists are the public contract.
        """
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema
        col_idx, _col_type, _op, lit = self._resolve_where(stmt.where, schema)

        # Named-column projection: validate all column names up front so an
        # unknown column surfaces before we return any partial result.
        proj_idx: list[int] = []
        if stmt.columns != ["*"]:
            name_to_idx = {n: i for i, (n, _) in enumerate(schema)}
            for cname in stmt.columns:
                if cname not in name_to_idx:
                    raise ExecutionError(f"unknown column {cname!r}")
                proj_idx.append(name_to_idx[cname])

        results: list[list[Any]] = []
        for _sid, vals, _pid in self._scan_table(ti):
            if col_idx is not None and vals[col_idx] != lit:
                continue
            # `list(vals)` copies so each row stands on its own (no shared refs).
            if stmt.columns == ["*"]:
                results.append(list(vals))
            else:
                results.append([vals[i] for i in proj_idx])
        return results

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
        schema = ti.schema
        col_idx, _col_type, _op, lit = self._resolve_where(stmt.where, schema)

        # Collect (page_id, slot_id) pairs first to avoid mutating pages
        # while we are still scanning them.
        to_delete: list[tuple[int, int]] = []
        for sid, vals, pid in self._scan_table(ti):
            if col_idx is None or vals[col_idx] == lit:
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