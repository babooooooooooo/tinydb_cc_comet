"""AST -> storage executor. Owns Pager+Catalog; all I/O lives here. <= 400 lines.

The Executor is the single bridge between parsed SQL statements and the
on-disk storage layer (Pager + Catalog + SlottedPage). It dispatches each
AST node to a dedicated ``_exec_*`` method. DDL (CREATE/DROP TABLE) is
fully implemented; DML (INSERT/SELECT/DELETE) is wired in but raises
``NotImplementedError`` until later tasks land.
"""
from tinydb.catalog import Catalog, TableInfo
from tinydb.errors import ExecutionError, PageFull
from tinydb.pager import Pager
from tinydb.parser import CreateTable, DropTable, Insert, Select, Delete
from tinydb.row_codec import decode_row, encode_row
from tinydb.slotted_page import SlottedPage
from tinydb.type_system import py_to_db


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

    def execute(self, stmt: object) -> list:
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

        Allocates one fresh page for the table's root (data) page,
        initializes it as an empty ``SlottedPage``, registers the table in
        the catalog, then writes page 1 back to disk. Duplicate table
        names raise ``ExecutionError`` (the user-facing error mapping).
        """
        if self.catalog.get_table(stmt.name) is not None:
            raise ExecutionError(f"table {stmt.name!r} already exists")

        # Allocate a root page for the table's data and initialize it as
        # an empty slotted page. Order matters: alloc_page -> empty ->
        # write -> catalog.register -> catalog flush.
        root_id = self.pager.alloc_page()
        page = SlottedPage.empty(root_id)
        self.pager.write_page(root_id, page.to_bytes())

        # MVP: next_page_id == root_page_id (no overflow yet). Task 21
        # will teach the catalog to advance next_page_id on page split.
        self.catalog.create_table(
            stmt.name, stmt.columns,
            root_page_id=root_id, next_page_id=root_id,
        )

        # Persist catalog change to page 1 and flush mmap to disk.
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

    # --- DML placeholders (Task 18 / Task 19) -------------------------------

    def _exec_insert(self, stmt: Insert) -> list:
        """Insert row(s) into a table.

        MVP behavior: ``stmt.columns`` is ignored — values are inserted in
        schema order. Type validation runs through ``type_system.py_to_db``
        so invalid Python types surface as :class:`ExecutionError`. Each
        row walks the data-page chain until it lands on a page with free
        space (or allocates a new one) and is encoded via
        :func:`row_codec.encode_row`.
        """
        ti = self.catalog.get_table(stmt.table)
        if ti is None:
            raise ExecutionError(f"table {stmt.table!r} does not exist")
        schema = ti.schema

        for row_vals in stmt.values:
            validated: list = []
            for (_name, col_type), v in zip(schema, row_vals):
                # py_to_db returns the encoded bytes for valid types and
                # raises TypeError/ValueError for invalid ones — we only
                # need the side effect, so the return value is discarded.
                try:
                    py_to_db(v, col_type)
                except (TypeError, ValueError) as e:
                    raise ExecutionError(f"column {_name}: {e}") from e
                validated.append(v)
            row_bytes = encode_row(validated, schema)
            self._insert_row_into_chain(ti, row_bytes)
        return []

    def _insert_row_into_chain(self, ti: TableInfo, row_bytes: bytes) -> int:
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

    def _scan_table(self, ti: TableInfo) -> list[tuple[int, list, int]]:
        """Linear-scan all data pages, filtering tombstones.

        Iterates page ids from ``ti.root_page_id`` through
        ``ti.next_page_id`` inclusive. Each surviving slot is decoded via
        :func:`row_codec.decode_row` and returned as a 3-tuple of
        ``(slot_id, decoded_values, page_id)`` so Task 19 can use the
        page/slot coordinates to drive WHERE matching and DELETE.
        """
        pid = ti.root_page_id
        results: list = []
        while True:
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            for sid in range(page.num_slots):
                row_bytes = page.get(sid)
                if row_bytes is None:  # tombstone or out-of-range slot
                    continue
                results.append((sid, decode_row(row_bytes, ti.schema), pid))
            if pid == ti.next_page_id:
                break
            pid += 1
        return results

    def _exec_select(self, stmt: Select) -> list:
        """Read rows from a table. Implemented in Task 19."""
        raise NotImplementedError("SELECT implemented in Task 19")

    def _exec_delete(self, stmt: Delete) -> list:
        """Delete rows from a table. Implemented in Task 19."""
        raise NotImplementedError("DELETE implemented in Task 19")