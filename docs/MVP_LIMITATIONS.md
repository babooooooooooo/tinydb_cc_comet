# MVP Limitations

> **Scope:** This document covers the **engine** layer (`database.py`, `executor.py`, `parser.py`, `pager.py`, `slotted_page.py`, `catalog.py`, `type_system.py`, `row_codec.py`, `tokenizer.py`). The interactive shell `tinydb-repl` (`src/tinydb/repl.py`) is a thin stdlib-only wrapper over `Database.execute(sql)`; it adds no engine surface and inherits the limitations below. For REPL behavior contract, see `README.md` § REPL and `openspec/specs/repl-shell/spec.md`.

tinydb MVP is a teaching-grade embedded database. It explicitly does NOT provide:

- **ACID / crash safety**: pages are written best-effort. Process kill mid-write MAY corrupt the file. There is no write-ahead log, no fsync barrier, no recovery. The on-disk format uses magic `b'TINYDB\x00\x01'` and `SCHEMA_VERSION = 0x01` (see `src/tinydb/pager.py`) so a truncated file will refuse to open rather than silently misread, but it cannot be repaired.
- **Transactions**: no `BEGIN` / `COMMIT` / `ROLLBACK`. Every `execute()` mutates storage immediately. Transactional semantics live in the follow-up `tinydb-acid` package.
- **Concurrency**: single-threaded, single-process. Two processes opening the same file will step on each other and silently corrupt pages. There is no file lock, no shared-memory coordination, no advisory lock.
- **UPDATE**: not supported. The MVP delete-and-reinsert idiom is `DELETE ... WHERE ...` followed by `INSERT INTO ...`. `UPDATE ... SET ...` is a follow-up.
- **Schema-level constraints (post `tinydb-constraints`)**: column-level `NOT NULL` / `UNIQUE` / `PRIMARY KEY` are parsed and enforced at INSERT time. The catalog persists each column's `nullable` / `unique` / `primary_key` flags; legacy `[name, type]` schemas auto-load with `nullable=True, unique=False, primary_key=False`. UNIQUE validation is a full table O(n) scan per INSERT — `tinydb-engine-v2` will swap to B-tree indexes. CHECK / FOREIGN KEY / DEFAULT / table-level `UNIQUE (a, b)` / table-level `PRIMARY KEY (a, b)` / `ALTER TABLE` / `DROP CONSTRAINT` remain unsupported.
- **WHERE combinators**: only `col = literal`. No `AND`, `OR`, `IN`, `LIKE`, `BETWEEN`, `IS NULL`. Multi-row predicates require client-side filtering.
- **ORDER BY / LIMIT / OFFSET**: not supported. Callers must sort and slice the result list themselves.
- **Aggregation**: no `COUNT` / `SUM` / `AVG` / `MIN` / `MAX` / `GROUP BY` / `HAVING`. Aggregation is client-side.
- **Joins**: only single-table SELECT. There is no `FROM t1 JOIN t2 ON ...`.
- **Indexes**: linear scan only. Every `SELECT` and `DELETE` walks the table's page chain from `root_page_id` to `next_page_id`. Performance degrades linearly with row count.
- **Type coercion**: strict mode. The literal in a WHERE clause must match the column's declared DB type exactly — `'5'` will not compare with an `INT` column (raises `TypeError`), and a `FLOAT` literal will not match an `INT` column. Booleans are `TRUE` / `FALSE` keywords, not 0/1 integers.
- **Column types**: only `INT` / `TEXT` / `FLOAT` / `BOOL`. No `VARCHAR`, `BIGINT`, `DECIMAL`, `DATE`, `TIMESTAMP`, `BLOB`, nullable types. (MVP columns are implicitly non-null; an omitted INSERT value is a parse error, not NULL.)

## tinydb-types (2026-07-18)

- 15 types supported: `INT` / `INTEGER` / `SMALLINT` / `BIGINT` / `FLOAT` / `REAL` / `DOUBLE` / `TEXT` / `VARCHAR(N)` / `CHAR(N)` / `BOOL` / `BOOLEAN` / `DECIMAL(p, s)` / `DATE` / `TIME` / `TIMESTAMP`
- Type parameters: `VARCHAR(N)` / `CHAR(N)` require `N >= 1`; `DECIMAL(p, s)` requires `1 <= p <= 18`, `0 <= s < p`
- **FLOAT = 4-byte single precision** (≈7 significant digits); `DOUBLE` = 8-byte double precision. `REAL` is alias for `FLOAT`. (`DOUBLE PRECISION` is registered as an alias but the parser tokenizes it as two identifiers and cannot reach it through SQL.)
- **CHAR(N) PAD SPACE**: writes `'ab'` to `CHAR(5)` stored as `'ab   '` (padding preserved on read)
- **DATETIME UTC unified**: `DATE` / `TIME` / `TIMESTAMP` stored as UTC; no timezone support
- **FLOAT / DOUBLE reject inf/nan** at all paths (`parse_literal`, `encode_py`, `validate`)
- **DECIMAL scaled int64**: `DECIMAL(p,s)` internal = `int(value * 10^s)`; max precision 18
- **Strict same-type comparison**: `VARCHAR != TEXT`, `INT != SMALLINT != BIGINT`, `FLOAT != DOUBLE`, `DATE != TIMESTAMP`, `DECIMAL(10,2) != DECIMAL(10,4)`. Cross-type comparison raises `TypeError` per Design D6.
- **No implicit widening**: cross-type comparison requires explicit `CAST` (out of scope; future change)
- **Module line budget**: `type_system.py` is 508 lines (Design §F6 budget was ≤350). The codec framework + 15 codecs + legacy helpers (132 lines preserved per §F2) + validation helpers exceed the original budget. A refactor split (legacy_helpers.py + codec_registry.py + codecs.py) is deferred to a follow-up change.
- **No `BLOB` / `JSON` / `UUID` / `INET` / `INTERVAL`**: out of scope
- **Catalog size**: the catalog is a single 4 KB page (page 1) holding JSON-serialized table metadata. Beyond ~100 tables the catalog may overflow; v2 moves to a multi-page catalog.
- **Page size**: fixed at 4 KB (`PAGE_SIZE = 4096` in `src/tinydb/pager.py`). Larger pages are a v2 change.
- **DROP TABLE**: best-effort. The table's entry is removed from the catalog and its root/overflow pages are leaked on disk; there is no free-page list in MVP. Reclaiming those pages lands in `tinydb-engine-v2`.

All of the above are scoped to follow-up changes: `tinydb-acid` (transactions, crash safety), `tinydb-engine-v2` (indexes, joins, multi-page catalog, page recycling, UPDATE).