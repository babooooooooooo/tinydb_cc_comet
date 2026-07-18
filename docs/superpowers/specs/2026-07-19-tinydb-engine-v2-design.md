# tinydb-engine-v2 Design Doc

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the storage layer with multi-page catalog overflow chain, free list page reclamation, and B+tree indexes for PRIMARY KEY / UNIQUE columns. SQL syntax and parser/executor contract unchanged.

**Architecture:** Three coupled storage-layer capabilities (free list, multi-page catalog, B+tree) added under a single change because they share `Pager.alloc/free_page` as the unifying API. IndexManager wraps B+tree per (table, indexed-column) pair and routes SELECT WHERE / INSERT / UPDATE / DELETE through indexed lookups instead of O(n) scans.

**Tech Stack:** Python 3.10+ stdlib only. No new external dependencies.

**Pre-req:** `tinydb-constraints` (PRIMARY KEY / UNIQUE metadata already in `Column`).

---

## 1. Context

MVP + 3 subsequent changes (engine-v1, constraints, types, aggregation) share three storage-layer bottlenecks:

1. **Catalog single-page (4KB)** — holds table metadata as JSON on page 1; > 50 tables overflows the page.
2. **No page reclamation** — `DROP TABLE` removes the catalog entry but the table's data pages leak; file size grows monotonically.
3. **O(n) unique/PK checks** — `_validate_unique_keys` walks every row on every INSERT. SELECT WHERE on indexed columns also scans every row.

These three are tightly coupled: free list serves both DROP reclamation and B+tree node allocation; multi-page catalog and B+tree persistence share the same page-level API.

## 2. Goals / Non-Goals

**Goals:**
- Pager header grows to `schema_version=0x02` + `free_list_head: u32`. Old v1 files auto-upgrade on open.
- `Pager.alloc_page()` consults free list head before extending the file; `Pager.free_page()` head-inserts.
- Catalog spans multiple pages via overflow chain when JSON exceeds one page.
- B+tree self-implemented (one node per 4KB page, fanout=16). Tombstone on delete (no merge).
- `IndexManager` indexes PRIMARY KEY and UNIQUE columns. INSERT/UPDATE/DELETE maintain the B+tree. SELECT WHERE on an indexed column uses index lookup; misses return empty.
- Constraint validation (UNIQUE / PK) uses B+tree lookup instead of O(n) scan.
- DROP TABLE frees data + index + overflow chain pages via `Pager.free_page()`.
- Backward compat: existing `.db` files auto-upgrade to v2 on open; indexes built lazily on first access.

**Non-Goals:**
- ACID / WAL / transactions (→ `tinydb-acid`)
- Concurrency / multi-threading
- Composite / multi-column indexes
- Reverse indexes / full-text / hash indexes
- ANALYZE / statistics
- B+tree merge / underflow rebalancing (tombstone only)
- Index-only scans (read all columns from index)
- Periodic compaction / garbage collection of tombstone-dense pages

## 3. Architecture

### 3.1 Pager v2 header + free list

**File header (page 0):**
```
bytes  0-7:   magic          b'TINYDB\x00\x02'   # version 0x02
byte   8:     schema_version 0x02
bytes  9-12:  free_list_head u32                  # head of free page chain, 0 = empty
bytes 13-4095: reserved (zeros)
```

**Free list semantics:**
- A free page's first 4 bytes (offset 0) are interpreted as `u32 next_free_page_id` (0 = end of chain).
- Free page contents beyond byte 4 are unused while free.
- `Pager.alloc_page()`: if `free_list_head != 0`, read the head page, extract `next_free`, update header; return head page id. Otherwise append a new page and return its id.
- `Pager.free_page(page_id)`: write `next_free = free_list_head` at page[0:4]; update header to `page_id`. The page contents are otherwise left intact (best-effort).

**Backward compat (v1 → v2):**
- On `Pager.open()`, if `schema_version=0x01`: rewrite byte 8 to 0x02, write `free_list_head=0` at bytes 9-12. No data migration.
- IndexManager rebuilds lazily on first INSERT/SELECT for each table (one-time full scan to build B+tree).

**Module line budget:** `pager.py ≤ 400` (was 169; +~120 lines).

### 3.2 Catalog overflow chain

**On-disk format:**
- Page 1 = chain head. Each chain page holds one JSON segment: `{"tables": {...}, "_seg_index": N, "_seg_count": M}`.
- Each non-final page starts with `u32 next_page` at offset 0 (4 bytes), then the JSON segment.
- Final page: next_page = 0.
- Overflow trigger: adding the next table entry would push page payload over `PAGE_SIZE - 16` bytes (16 = chain metadata + safety).

**Write path (`Catalog.to_bytes` → chain):**
1. Serialize JSON. If fits in `PAGE_SIZE - 16` bytes, write to page 1 directly (no chain).
2. Else, segment greedily into pages, allocate new chain pages via `Pager.alloc_page()`, write each segment.

**Read path (`Catalog.from_bytes`):**
1. Walk chain head → tail.
2. Concatenate JSON segments (strip `_seg_index` / `_seg_count` metadata, or keep as debugging aid).
3. `json.loads` once.

**Module line budget:** `catalog.py ≤ 200` (was 169; +~80 lines).

### 3.3 B+tree

**Page layout (4KB = 4096 bytes):**
```
byte 0:      node_type     u8     (1 = leaf, 2 = internal)
byte 1:      reserved      u8     (0)
bytes 2-3:   key_count     u16
bytes 4-5:   reserved      u16    (0)
bytes 6-9:   next_leaf_id  u32    (leaf only; 0 = no next leaf; internal = 0)
bytes 10-4095: payload     4086 bytes
```

**Internal node payload:**
- `keys[0..key_count-1]` (each = key_size bytes, fixed-width for fixed-width types; variable for VARCHAR/TEXT)
- `children[0..key_count]` (each = u32 page_id; key_count+1 children)

**Leaf node payload:**
- `keys[0..key_count-1]` (each = key_size bytes, same encoding as internal)
- `values[0..key_count-1]` (each = u32 row_page_id << 32 | slot_id, packed u64)
- Tombstones: an additional bit per entry, packed into the `reserved` bytes after key_count (16 bits; current design uses in-band flag — see § 3.3.3)

**3.3.1 Key encoding** (per Q1 answer — `codec_for()`):

| Type | Encoding | Width |
|------|----------|-------|
| `SMALLINT` | big-endian signed i16 | 2 bytes (fixed) |
| `INT` / `INTEGER` | big-endian signed i32 | 4 bytes (fixed) |
| `BIGINT` | big-endian signed i64 | 8 bytes (fixed) |
| `FLOAT` / `REAL` | big-endian IEEE 754 single (u32 bits) | 4 bytes (fixed) |
| `DOUBLE` | big-endian IEEE 754 double (u64 bits) | 8 bytes (fixed) |
| `DATE` | big-endian signed i32 (days since 1970-01-01 UTC) | 4 bytes (fixed) |
| `TIME` | big-endian unsigned u32 (seconds since midnight UTC) | 4 bytes (fixed) |
| `TIMESTAMP` | big-endian signed i64 (seconds since 1970-01-01 UTC) | 8 bytes (fixed) |
| `VARCHAR(N)` | u16 length prefix + UTF-8 bytes | 2 + utf8_len (variable) |
| `CHAR(N)` | fixed N bytes (right-space padded) | N bytes (fixed) |
| `TEXT` | u32 length prefix + UTF-8 bytes | 4 + utf8_len (variable) |
| `DECIMAL(p,s)` | big-endian signed i64 (scaled) | 8 bytes (fixed) |
| `BOOL` / `BOOLEAN` | u8 (0 / 1) | 1 byte (fixed) |

NULL values are not indexed (R9 SQL standard semantics from constraints change). Variable-width keys (VARCHAR/TEXT) require a separate leaf payload format — each entry stores `(u16 key_len, key_bytes, u64 value)` instead of `(fixed_key, u64 value)`.

**3.3.2 Operations:**
- `insert(key, slot_ref)`: descend tree, insert into leaf; if leaf full → split at median, promote median to parent; recurse up. Root split allocates new root page.
- `search(key) -> SlotRef | None`: descend tree by key comparison.
- `range(start, end) -> Iterable[SlotRef]`: descend to start leaf, iterate via `next_leaf_id` until key > end.
- `delete(key)`: descend to leaf, mark entry as tombstone (in-band flag bit; no merge — see § 3.3.3).

**3.3.3 Tombstone semantics** (per Q3 answer — no merge):
- Each entry in a leaf has a 1-bit tombstone flag stored in the entry's metadata byte (immediately before the key). Marked tombstone: entry stays in place but `search` skips it.
- Periodic compaction (rebuild tombstone-dense pages) is explicitly out of scope.

**Module line budget:** `btree.py ≤ 400` (new file, ~350 lines).

### 3.4 IndexManager + executor routing

**`IndexManager`** owns `dict[(table_name, column_name), BTreeRootPageId]`.

**On `Database.open()`:**
- Load catalog (walk overflow chain).
- For each table, call `rebuild_for_table(table)`: full scan of all data pages, for each indexed column (PK or UNIQUE), `BTree.insert(encoded_key, SlotRef(page_id, slot_id))`.

**On `INSERT`:**
- After slot write succeeds: `index_manager.insert(table, col, encoded_key, slot_ref)` for each indexed column.
- Pre-write check: `index_manager.lookup(...)` for each indexed column; if found → raise `ConstraintViolation` (replaces O(n) `_scan_unique_keys`).
- Failure rollback: remove slot, `index_manager.delete(...)` for the partial insert.

**On `DELETE`:**
- Identify slot(s) via `index_manager.lookup(...)` filtered by WHERE clause.
- Remove slot, `index_manager.delete(...)` for each indexed column.

**On `UPDATE`:**
- For each indexed column whose value changes: `delete(old_key)`, then `insert(new_key)`.

**On `SELECT WHERE col = lit`:** (per Q4 answer — always use index, no fallback)
- If `(table, col)` is in `index_manager` and WHERE is single equality (no AND/OR): call `index_manager.lookup(...)` for the slot_ref, read that one slot.
- Index miss → empty result (no stderr warning).

**Module line budget:** `index_manager.py ≤ 200` (new file, ~150 lines). `executor.py ≤ 920` (was 707; +~100 lines for index lookup paths).

### 3.5 DROP TABLE reclamation

**On `DROP TABLE`:**
1. Walk data pages (read root, descend chain) → collect data page IDs.
2. For each indexed column: walk its B+tree (read root, descend via child pointers) → collect index page IDs.
3. `Pager.free_page()` each collected page id.
4. Remove table from catalog.
5. Catalog re-serialized to overflow chain (may shrink if it was on its own page).

**Module line budget:** included in `executor.py` (above).

## 4. Spec decisions (D1–D6)

### D1: Index key encoding
Per § 3.3.1 — `codec_for(type, type_params).encode_py(value)` produces fixed-width sortable bytes. NULL values not indexed.

### D2: Fanout
Fanout = 16 keys per leaf / internal node. With max key size 8 bytes (BIGINT / DECIMAL / DOUBLE / TIMESTAMP) + 4-byte child pointer, internal nodes fit ~16 keys per 4KB page. Smaller keys (INT = 4 bytes) pack more keys per page.

### D3: Tombstone on delete (no merge)
Per Q3 answer — entries marked deleted via in-band flag. Tree shape unchanged. Compaction deferred.

### D4: Always use index (no fallback) (per Q4 answer)
SELECT WHERE on indexed column never falls back to scan. Non-indexed columns continue to scan.

### D5: Full reclaim on DROP (per Q6 answer)
Data + index + overflow chain pages all returned to free list on DROP.

### D6: Auto-upgrade v1 → v2 on open (per Q5 answer)
`Pager.open()` rewrites header byte 8 to 0x02 and writes `free_list_head=0`. Indexes built lazily on first INSERT/SELECT for each table.

## 5. Capabilities

### New Capabilities

- `storage-free-list`: Pager maintains free list (head page id + chain); alloc consults, free head-inserts.
- `storage-multi-page-catalog`: Catalog spans overflow chain when JSON exceeds one page.
- `index-btree-primary`: B+tree on PRIMARY KEY column; INSERT/DELETE/UPDATE maintain; SELECT WHERE PK = lit uses index.
- `index-btree-unique`: B+tree on any UNIQUE column (one B-tree per UNIQUE column).

### Modified Capabilities

- `storage-engine` (from MVP): `Pager.alloc_page` / `Pager.free_page` reworked; v1 → v2 auto-upgrade on open.
- `schema-column-constraints` (from `tinydb-constraints`): UNIQUE / PRIMARY KEY validation switches from O(n) scan to O(log n) B+tree lookup.
- `sql-update-statement` (from `tinydb-engine-v1`): UPDATE WHERE path uses index when applicable.
- DROP TABLE behavior: reclaims data + index + overflow chain pages.

## 6. File / module impact

| File | Status | Line budget | Notes |
|------|--------|-------------|-------|
| `src/tinydb/pager.py` | modify | ≤ 400 | +free list, v2 header, v1 upgrade |
| `src/tinydb/catalog.py` | modify | ≤ 250 | +overflow chain serialization + walk |
| `src/tinydb/btree.py` | new | ≤ 400 | B+tree node, insert, search, range, delete (tombstone) |
| `src/tinydb/index_manager.py` | new | ≤ 200 | (table, col) → BTree root mapping; rebuild_for_table |
| `src/tinydb/executor.py` | modify | ≤ 920 | +index lookup paths in INSERT/UPDATE/DELETE/SELECT; +DROP reclamation |
| `src/tinydb/database.py` | modify | (no new budget) | Initialize IndexManager on open |
| `tests/unit/test_btree.py` | new | — | ~20 tests: insert/split/search/range/delete/tombstone |
| `tests/unit/test_free_list.py` | new | — | ~10 tests: alloc/free cycle, chain walk |
| `tests/unit/test_index_manager.py` | new | — | ~10 tests: rebuild, lookup, insert, delete |
| `tests/integration/test_pager_v2_header.py` | new | — | ~5 tests: v1 upgrade, magic check |
| `tests/integration/test_catalog_overflow.py` | new | — | ~10 tests: chain, walk, persist across reopen |
| `tests/integration/test_select_uses_index.py` | new | — | ~10 tests: PK/UNIQUE routing, no fallback |
| `tests/integration/test_drop_reclaims_pages.py` | new | — | ~5 tests: data + index + chain reclamation |
| `tests/perf/test_index_vs_scan.py` | new | — | ~3 benchmarks: PK lookup vs full scan at n=10000 |
| Existing tests | unchanged | — | MVP / engine-v1 / constraints / types / aggregation all continue to pass |

**External API:** `Database.execute()` signature unchanged. `Pager.read_page()` / `write_page()` / `alloc_page()` / `free_page()` signature stable (only alloc/free semantics change).

## 7. Out of Scope

- ACID / WAL / transactions → `tinydb-acid`
- Composite indexes (multi-column B+tree) → future
- Reverse / full-text / hash indexes → permanent
- ANALYZE / statistics → permanent
- B+tree merge / underflow rebalancing → future compaction change
- Index-only scans → future
- Periodic compaction of tombstone-dense pages → future
- Concurrent B+tree access → permanent (single-thread scope)

## 8. Testing strategy

**Unit tests:**
- `test_btree.py`: insert/split/search/range/delete on small trees (5-50 keys); split at root; multi-page leaves; tombstone marks.
- `test_free_list.py`: alloc/free cycle; chain walk; free list persistence across reopen.
- `test_index_manager.py`: rebuild_for_table; lookup miss/hit; insert/delete maintains.

**Integration tests:**
- `test_pager_v2_header.py`: open v1 file → header upgraded; open v2 file → no change; bad magic raises.
- `test_catalog_overflow.py`: catalog with 100 tables persists across reopen; chain walk; pages reclaimed on DROP.
- `test_select_uses_index.py`: SELECT WHERE on PK uses index; on UNIQUE uses index; non-indexed falls through to scan.
- `test_drop_reclaims_pages.py`: DROP returns data + index pages to free list; subsequent INSERT recycles.

**Performance benchmarks** (in `tests/perf/`):
- `test_index_vs_scan.py`: PK lookup at n=10000 rows < full scan / 100 (acceptance criterion per §F6 of proposal).

**Regression:**
- Full existing test suite (MVP / engine-v1 / constraints / types / aggregation) continues to pass without modification.
- Coverage ≥ 90% overall; new code 100%.

## 9. Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| B+tree split bugs (off-by-one in median, lost child pointers) | Med | Comprehensive unit tests with explicit invariants (key count, sorted order) after every operation |
| v1 → v2 upgrade leaves existing files inconsistent (free_list_head set but pages not actually free) | Low | Upgrade only writes header byte; never touches data pages. No code path interprets "old data page" as free. |
| IndexManager.rebuild slow on large tables | Med | First-time cost is acceptable (~1ms per 10k rows); deferred optimization: incremental rebuild on INSERT |
| Tombstone accumulation degrades search | Low | Out-of-scope: periodic compaction. Document as known limitation in `MVP_LIMITATIONS.md`. |
| Pager line budget overrun (was 169 → could exceed 400) | Low | Split out `_page_alloc.py` / `_free_list.py` if needed; defer to follow-up |

## 10. Acceptance criteria

- All existing tests pass (575+).
- New tests: ~50 unit + ~30 integration + ~3 perf.
- Coverage ≥ 90% overall; 100% on `btree.py`, `index_manager.py`.
- Module line budgets respected.
- v1 `.db` files open without errors; first INSERT/SELECT triggers IndexManager rebuild.
- DROP frees all data + index + overflow pages (verified via `page_count()` decrease).
- PK lookup at n=10000 < full scan / 100.