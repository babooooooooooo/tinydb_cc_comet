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
from tinydb.index_manager import IndexManager
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
from tinydb.btree import InternalNode, NODE_TYPE_INTERNAL
from tinydb.type_system import (
    codec_for,
    infer_literal_type,
    validate_compare_types,
)

# MAX_INLINE_PAYLOAD = 4078; subtract SLOT_SIZE so an inline first chunk on
# an empty page leaves room for the slot directory entry (no overlap).
_CHUNK_SIZE = MAX_INLINE_PAYLOAD - SLOT_SIZE  # 4072


class _IndexPager:
    """Wrap a Pager to record every page the IndexManager/BTree allocates.

    B+tree splits allocate new pages via ``self.pager.alloc_page()`` inside
    ``BTree.insert`` / ``BTree._insert_into_parent`` — pages that the
    Executor's data-page chain would happily walk into and corrupt on the
    next ``PageFull``-driven ``pid += 1`` step. By tracking every page id
    the index side hands out, :meth:`Executor._alloc_data_page` (and the
    skip loop in :meth:`Executor._insert_inline_only`) can guarantee the
    data chain never collides with a B+tree node.

    Forwarded methods: ``read_page``, ``write_page``, ``flush``, ``close``,
    ``alloc_page``, ``free_page`` (the last two update the tracker).
    """

    def __init__(self, pager):
        self._pager = pager
        self._allocated: set[int] = set()

    def read_page(self, page_id: int) -> bytes:
        return self._pager.read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        self._pager.write_page(page_id, data)

    def alloc_page(self) -> int:
        pid = self._pager.alloc_page()
        self._allocated.add(pid)
        return pid

    def free_page(self, page_id: int) -> None:
        # BTree never frees pages, but keep the wrapper symmetric.
        self._pager.free_page(page_id)
        self._allocated.discard(page_id)

    def flush(self) -> None:
        self._pager.flush()

    def close(self) -> None:
        self._pager.close()

    @property
    def allocated(self) -> set[int]:
        return set(self._allocated)


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

    def register_index_pager(self, wrapper: "_IndexPager") -> None:
        """Track an _IndexPager so its allocated pages are skipped on data-chain extensions."""
        self._index_pagers.append(wrapper)

    def _make_index_pager(self, pager) -> "_IndexPager":
        """Build a fresh _IndexPager and register it for collision avoidance."""
        wrapper = _IndexPager(pager)
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

        self.pager.write_page(1, self.catalog.to_bytes())
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

        Walks the table's contiguous data page chain (skipping any page id
        owned by a B+tree wrapper) plus any per-page overflow chains for
        spilled rows; frees them all. Then walks every B+tree index for
        the table's PK + UNIQUE columns, frees their nodes via the per-
        BTree ``_IndexPager`` wrapper (which clears the wrapper's
        ``_allocated`` set), and finally forgets the B+trees in
        :class:`IndexManager`. The catalog is persisted inline (single
        page) to stay consistent with ``_exec_create_table``.

        Task 8 of ``tinydb-engine-v2`` (DROP TABLE reclamation).
        """
        ti = self.catalog.get_table(stmt.name)
        if ti is None:
            raise ExecutionError(f"table {stmt.name!r} does not exist")

        # Collect page ids BEFORE removing from catalog (we need ``ti``).
        data_pids = self._collect_table_data_pages(ti)
        index_pids = self._collect_index_pages(ti)

        # Drop from catalog first so subsequent persistence writes a
        # consistent catalog. The page ids are already captured above.
        self.catalog.drop_table(stmt.name)
        # Drop the per-table data page list so a future CREATE TABLE with
        # the same name starts with a fresh entry (no stale page ids).
        self._table_data_pages.pop(stmt.name, None)

        # Free data pages via the raw pager (data chain has no wrapper).
        for pid in data_pids:
            self.pager.free_page(pid)

        # Free index pages. When a BTree's pager is an ``_IndexPager``
        # wrapper (the Database-installed path), use the wrapper's
        # ``free_page`` so its ``_allocated`` tracking is cleared; this
        # prevents phantom "owned" entries from polluting the Executor's
        # collision avoidance after the BTree is forgotten. When no
        # wrapper is present (e.g., standalone Executor in unit tests),
        # fall back to freeing via the raw pager.
        for col in ti.columns:
            if not (col.primary_key or col.unique):
                continue
            bt = self.index_manager.get_btree(stmt.name, col.name)
            if bt is None:
                continue
            wrapper = bt.pager if type(bt.pager).__name__ == "_IndexPager" else None
            if wrapper is not None:
                for pid in list(wrapper._allocated):
                    wrapper.free_page(pid)
            else:
                for pid in index_pids:
                    self.pager.free_page(pid)

        # Forget B+trees for this table. After this the IndexManager has
        # no record of the dropped table; the corresponding wrapper
        # instances are left in ``self._index_pagers`` with empty
        # ``_allocated`` sets (harmless).
        self.index_manager.forget_table(stmt.name)

        # Persist catalog. We use the inline format ``write_page(1,
        # to_bytes())`` to stay consistent with ``_exec_create_table`` and
        # ``_insert_inline_only`` (which both write inline format). The
        # chain-format writer ``Pager.write_catalog_chain`` is reserved
        # for future multi-page overflow support; mixing the two
        # formats breaks ``Catalog.from_bytes`` on subsequent opens.
        self.pager.write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        return []

    def _collect_table_data_pages(self, ti: TableInfo) -> list[int]:
        """Return every page id used by ``ti``'s data chain + spill chains.

        Walks the range ``[ti.root_page_id, ti.next_page_id]`` and collects
        only DATA pages, skipping any page id that is currently tracked by
        a B+tree ``_IndexPager`` wrapper. The data chain is NOT contiguous
        in the presence of indexes — ``_insert_inline_only`` advances by
        ``pid += 1`` while skipping index pages — so the catalog's
        ``next_page_id`` may equal a page id owned by a B+tree.

        For each data page, follows its ``overflow_next`` link to pick up
        any overflow pages used for spilled rows. We defensively treat
        both ``0`` and ``NULL_PAGE_ID`` as "no overflow chain" because
        freshly-allocated data pages haven't had ``overflow_next``
        initialized; we additionally require the target page to have
        ``page_type == 2`` (overflow) before following it.
        """
        pids: list[int] = []
        seen: set[int] = set()
        if ti.root_page_id == 0 or ti.next_page_id < ti.root_page_id:
            return pids
        # Index page ids are owned by B+tree wrappers — they live in the
        # address space between data pages and must not be freed as data.
        index_pages = self._index_pages()
        pid = ti.root_page_id
        end = ti.next_page_id
        while pid <= end:
            if pid in index_pages:
                pid += 1
                continue
            if pid not in seen:
                seen.add(pid)
                pids.append(pid)
                # Follow the per-page overflow chain (bytes 4:8 of every
                # data page hold the next overflow page id, 0 or
                # ``NULL_PAGE_ID`` on the tail).
                nxt = int.from_bytes(self.pager.read_page(pid)[4:8], "big")
                while nxt > 0 and nxt != NULL_PAGE_ID and nxt not in seen:
                    target_raw = self.pager.read_page(nxt)
                    if target_raw[0] != 2:
                        break
                    seen.add(nxt)
                    pids.append(nxt)
                    nxt = int.from_bytes(target_raw[4:8], "big")
            pid += 1
        return pids

    def _collect_index_pages(self, ti: TableInfo) -> list[int]:
        """Return every page id used by every B+tree index for ``ti``.

        Walks each indexed column's B+tree by iterative descent: pop a
        page off the stack, deserialize it, and push internal-node
        children until only leaves remain. Leaf pages are collected but
        not descended into. Duplicate page ids (which can't occur in a
        well-formed B+tree) are skipped defensively.
        """
        pids: list[int] = []
        seen: set[int] = set()
        for col in ti.columns:
            if not (col.primary_key or col.unique):
                continue
            bt = self.index_manager.get_btree(ti.name, col.name)
            if bt is None or bt.root_page_id is None:
                continue
            stack: list[int] = [bt.root_page_id]
            while stack:
                pid = stack.pop()
                if pid in seen:
                    continue
                seen.add(pid)
                pids.append(pid)
                page = self.pager.read_page(pid)
                if page[0] == NODE_TYPE_INTERNAL:
                    node = InternalNode.deserialize(page)
                    stack.extend(node.children)
        return pids

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
        name_to_idx = {c.name: i for i, c in enumerate(ti.columns)}
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
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            try:
                sid = page.insert(row_bytes)
            except PageFull:
                continue
            self.pager.write_page(pid, page.to_bytes())
            self.pager.flush()
            return (pid, sid)
        # All current pages full (or empty list); allocate a fresh page and
        # append to the tracked list. ``_alloc_data_page`` filters out
        # B+tree pages so the new pid won't collide with any index.
        new_pid = self._alloc_data_page()
        data_pages.append(new_pid)
        ti.next_page_id = new_pid
        self.pager.write_page(1, self.catalog.to_bytes())
        self.pager.flush()
        page = SlottedPage.from_bytes(new_pid, self.pager.read_page(new_pid))
        sid = page.insert(row_bytes)
        self.pager.write_page(new_pid, page.to_bytes())
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
        page = SlottedPage.from_bytes(pid_first, self.pager.read_page(pid_first))
        page.slots[sid_first].flags |= FLAG_SPILL_START
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
        return (pid_first, sid_first)

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

        # --- B+tree fast path ------------------------------------------------
        # Single-equality WHERE on an indexed column short-circuits the full
        # scan: encode the literal with the column's codec, look it up in the
        # per-(table,col) B+tree, and read at most one row by (page_id,
        # slot_id). OFFSET/LIMIT still apply; ORDER BY is a no-op for 1 row.
        # INDEX MAINTENANCE DEPENDENCY: this returns [] if the key is not in
        # the index, so INSERT/DELETE/UPDATE must keep indexes in sync.
        if stmt.where is not None and self._is_single_eq_on_indexed(stmt.where, ti):
            col_name, lit_value = self._parse_single_eq(stmt.where)
            if col_name is not None:
                col_obj = next((c for c in ti.columns if c.name == col_name), None)
                if col_obj is not None:
                    bt = self.index_manager.get_btree(ti.name, col_name)
                    if bt is not None:
                        try:
                            key = codec_for(
                                col_obj.type, col_obj.type_params
                            ).encode_py(lit_value)
                            ref = self.index_manager.lookup_key(
                                ti.name, col_name, key
                            )
                        except (TypeError, ValueError, OverflowError):
                            ref = None
                        if ref is None:
                            return []
                        fast_rows = self._read_row_by_slot(ti, ref)
                        # Apply OFFSET / LIMIT / projection.
                        if stmt.offset:
                            fast_rows = fast_rows[stmt.offset:]
                        if stmt.limit is not None:
                            fast_rows = fast_rows[:stmt.limit]
                        results: list[list[Any]] = []
                        for _sid, vals, _pid in fast_rows:
                            if stmt.columns == ("*",):
                                results.append(list(vals))
                            else:
                                results.append([vals[i] for i in proj_idx])
                        return results

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
            raw = self.pager.read_page(pid)
            page = SlottedPage.from_bytes(pid, raw)
            if page.slots[sid].flags & FLAG_SPILL_START:
                self._free_overflow_chain(pid)
            page.delete(sid)
            self.pager.write_page(pid, page.to_bytes())
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
        #    For each updated row, compute (old_vals, new_vals) index deltas
        #    and apply them AFTER the write so the index always references
        #    a live slot. In-place updates keep the same (pid, sid); the
        #    fallback path (delete + reinsert or chain) gets a NEW (pid, sid).
        for pid, sid_vals_list in by_page.items():
            page = SlottedPage.from_bytes(pid, self.pager.read_page(pid))
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
                # Fallback: grew == True → delete + insert (or chain)
                if old_slot.flags & FLAG_SPILL_START:
                    self._free_overflow_chain(pid)
                page.delete(sid)
                try:
                    new_sid = page.insert(new_bytes)
                    # Delete old index entries (slot gone), insert new.
                    self._update_index_for_row(
                        ti, old_vals, new_vals, (pid, new_sid),
                    )
                except PageFull:
                    pending_chain_inserts.append((old_vals, new_vals, new_bytes))

            # Flush this page before chain inserts (may advance next_page_id).
            self.pager.write_page(pid, page.to_bytes())
            for old_vals, new_vals, new_bytes in pending_chain_inserts:
                new_pid, new_sid = self._insert_row_into_chain(ti, new_bytes)
                # Old slot is tombstoned (different page now); delete old
                # index keys, insert new with the new (pid, sid) ref.
                self._update_index_for_row(
                    ti, old_vals, new_vals, (new_pid, new_sid),
                )

        self.pager.flush()
        return []

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
        raw = self.pager.read_page(page_id)
        page = SlottedPage.from_bytes(page_id, raw)
        slot = page.slots[slot_id]
        if slot.flags & FLAG_TOMBSTONE:
            return []
        row_bytes = page.get(slot_id)
        if row_bytes is None:
            return []
        if slot.flags & FLAG_SPILL_START:
            row_bytes = row_bytes + self._read_overflow_chain(page_id)
        return [(slot_id, decode_row(row_bytes, ti.schema_v2), page_id)]