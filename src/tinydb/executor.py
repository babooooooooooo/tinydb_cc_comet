"""AST -> storage executor. Owns Pager+Catalog; all I/O lives here. <= 400 lines.

The Executor is the single bridge between parsed SQL statements and the
on-disk storage layer (Pager + Catalog + SlottedPage). It dispatches each
AST node to a dedicated ``_exec_*`` method. DDL (CREATE/DROP TABLE) is
fully implemented; DML (INSERT/SELECT/DELETE) is wired in but raises
``NotImplementedError`` until later tasks land.
"""
from tinydb.errors import ExecutionError
from tinydb.parser import CreateTable, DropTable, Insert, Select, Delete
from tinydb.slotted_page import SlottedPage


class Executor:
    """Drive AST statements against a (Pager, Catalog) pair.

    The Executor holds mutable references to the supplied Pager and Catalog;
    it does not own their lifetime — the caller is responsible for closing
    the Pager. Each ``execute`` call mutates the Catalog in-place and
    flushes page 1 (the catalog slot) back to disk.
    """

    def __init__(self, pager, catalog):
        self.pager = pager
        self.catalog = catalog

    # --- public dispatch ----------------------------------------------------

    def execute(self, stmt):
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

    def _exec_create_table(self, stmt: CreateTable):
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

    def _exec_drop_table(self, stmt: DropTable):
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

    def _exec_insert(self, stmt: Insert):
        """Insert row(s) into a table. Implemented in Task 18."""
        raise NotImplementedError("INSERT implemented in Task 18")

    def _exec_select(self, stmt: Select):
        """Read rows from a table. Implemented in Task 19."""
        raise NotImplementedError("SELECT implemented in Task 19")

    def _exec_delete(self, stmt: Delete):
        """Delete rows from a table. Implemented in Task 19."""
        raise NotImplementedError("DELETE implemented in Task 19")