# tinydb (MVP)

> Minimal embedded relational database for teaching and embedding. **MVP: now ACID-capable via `tinydb-acid`.**

> **Status:** MVP complete — `CREATE` / `DROP` / `INSERT` / `SELECT` / `DELETE` / `UPDATE` over `INT` / `TEXT` / `FLOAT` / `BOOL` (and 11 more types via `tinydb-types`). ACID transactions (`BEGIN` / `COMMIT` / `ROLLBACK`) ship with the `tinydb-acid` change. See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) for the full scope.
>
> The 15-type extension (`tinydb-types` change) adds `VARCHAR(N)`, `CHAR(N)`, `DECIMAL(p, s)`, `DOUBLE`, `REAL`, `BOOLEAN`, `INTEGER`, `SMALLINT`, `BIGINT`, `DATE`, `TIME`, `TIMESTAMP` — see [§ Types](#types).
>
> A stdlib-only interactive shell (`tinydb-repl`) ships with the package — see [§ REPL](#repl).

## Quick start

```python
import tinydb
with tinydb.Database(":memory:") as db:
    db.execute("CREATE TABLE users(id INT, name TEXT)")
    db.execute("INSERT INTO users(id, name) VALUES (1, 'alice')")
    for row in db.execute("SELECT * FROM users"):
        print(row.id, row.name)
```

Notes on the grammar shown above:

- `INSERT` requires an explicit column list (no positional shorthand).
- `WHERE` accepts only `column = literal`.
- `SELECT` columns may be `*` or a comma-separated list; multi-row results come back as immutable `Row` objects accessed by column name or unpacking.

## ACID

Each `execute()` call runs inside an implicit transaction (autocommit): a successful statement commits to disk via `fsync`, and a failed statement auto-rolls-back so the database never holds a half-applied mutation. Wrap multiple statements in an explicit `BEGIN` ... `COMMIT` (or `ROLLBACK`) block to control them as a single atomic unit.

Guarantees provided:

- **Atomicity** — a multi-row INSERT/UPDATE/DELETE either commits every row or none. A failure on any row (constraint violation, type mismatch, etc.) rolls back the entire statement.
- **Durability** — `COMMIT` performs an `fsync(main)` after writing the WAL commit record. A power loss or `kill -9` after a successful COMMIT leaves the committed data intact.
- **Crash recovery** — on open, the WAL is replayed: committed transactions are re-applied to the main file; uncommitted transactions are discarded.

Not provided (see [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) § tinydb-acid):

- **Isolation / concurrency** — single-Executor, single-process. Concurrent transactions from multiple processes or threads are not supported.

Usage:

```python
import tinydb

# Autocommit: every execute() is its own implicit transaction.
with tinydb.Database("data.db") as db:
    db.execute("CREATE TABLE accounts(id INT PRIMARY KEY, balance INT)")
    db.execute("INSERT INTO accounts(id, balance) VALUES (1, 100)")
    # The next INSERT collides on PK — the entire statement rolls back
    # atomically; no partial state is left behind.
    try:
        db.execute("INSERT INTO accounts(id, balance) VALUES (2, 50), (1, 25)")
    except tinydb.errors.ConstraintViolation:
        pass  # accounts still has only (1, 100)

# Explicit BEGIN ... COMMIT / ROLLBACK
with tinydb.Database("data.db") as db:
    db.execute("BEGIN")
    db.execute("INSERT INTO accounts(id, balance) VALUES (2, 50)")
    db.execute("INSERT INTO accounts(id, balance) VALUES (3, 75)")
    db.execute("COMMIT")   # both rows visible after this point
    # Or: db.execute("ROLLBACK") to discard both rows

    db.execute("BEGIN")
    db.execute("DELETE FROM accounts WHERE id = 2")
    db.execute("ROLLBACK")  # id=2 is back
```

Schema upgrade notes:

- v3-schema `.db` files (`tinydb-acid`) are the current on-disk format.
- v2-schema files (from `tinydb-engine-v2`) auto-upgrade on open if no `<db>.wal` sidecar is present.
- v2-schema files WITH a WAL sidecar raise `SchemaMismatch` and require explicit migration.

## Run the demo

A runnable end-to-end example lives at [`examples/demo.py`](examples/demo.py) — it covers CREATE, INSERT, SELECT `*`, SELECT `WHERE`, DELETE, and SELECT after delete, against an in-memory database:

```bash
python3 examples/demo.py
```

## REPL

After `pip install -e ".[dev]"`, start an in-memory shell:

```bash
tinydb-repl
```

Open or create a file-backed database with `--database`:

```bash
tinydb-repl --database data.db
```

The REPL supports multi-line SQL: an unterminated single quote or open parenthesis shows `...>` to continue the statement. SELECT renders an aligned table, DDL/DML prints `OK`, and execution errors print a single `ERROR: <Class>: <message>` line without exiting the session.

| Meta command | Effect |
|---|---|
| `.exit` / `.quit` | exit cleanly |
| `.help` | show meta-command help |
| `.tables` | list table names (one per line) |
| `.schema <name>` | print reverse-generated `CREATE TABLE` |
| `.read <path>` | execute a UTF-8 SQL script |

On Unix-like platforms with `readline`, history persists at `~/.tinydb_history` (missing file or write failures are silent). Platforms without `readline` (e.g. default Windows) fall back to built-in `input()`: SQL, meta-commands, and output all work, but history is not loaded or saved.

## Types

15 column types are supported (the 4 MVP types + 11 new types):

| Type | Params | Notes |
|------|--------|-------|
| `INT` / `INTEGER` | — | 4-byte signed |
| `SMALLINT` | — | 2-byte signed (range ±32767) |
| `BIGINT` | — | 8-byte signed |
| `FLOAT` / `REAL` | — | **4-byte IEEE 754 single** (≈7 digits) |
| `DOUBLE` | — | 8-byte IEEE 754 double |
| `TEXT` | — | length-prefixed UTF-8 |
| `VARCHAR(N)` | N ≥ 1 | length-prefixed UTF-8, max N bytes |
| `CHAR(N)` | N ≥ 1 | right-space padded to N bytes on write (`PAD SPACE`) |
| `BOOL` / `BOOLEAN` | — | 1 byte |
| `DECIMAL(p, s)` | 1 ≤ p ≤ 18, 0 ≤ s < p | scaled int64 (max precision 18 digits) |
| `DATE` | — | 4-byte days since 1970-01-01 UTC |
| `TIME` | — | 4-byte seconds since midnight UTC |
| `TIMESTAMP` | — | 8-byte seconds since 1970-01-01 UTC (naive datetime) |

**Literal prefixes** for typed values: `DATE '2026-07-16'`, `TIME '14:30:00'`, `TIMESTAMP '2026-07-16 14:30:00'`, `DECIMAL '99.99'`.

**Strict same-type comparison (Design D6):** `WHERE col = literal` requires exact type match. Cross-type comparisons raise `TypeError`:
- `INT` ≠ `SMALLINT` ≠ `BIGINT`
- `FLOAT` ≠ `DOUBLE`
- `VARCHAR(N)` ≠ `TEXT`
- `DATE` ≠ `TIMESTAMP`
- `DECIMAL(10, 2)` ≠ `DECIMAL(10, 4)`

**Rejected values:** `FLOAT` / `DOUBLE` reject `Infinity` and `NaN` at all paths (literal parse, encode, validate). `DECIMAL` enforces precision/scale overflow at encode. `VARCHAR(N)` / `CHAR(N)` enforce max length.

See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) § tinydb-types for the complete contract.

## Module map (line budgets per proposal)

| Module | Budget | Responsibility |
|--------|--------|----------------|
| `type_system.py` | 150 | INT/TEXT/FLOAT/BOOL codecs |
| `pager.py` | 250 | 4KB pages, mmap/bytearray |
| `slotted_page.py` | 220 | single page layout |
| `catalog.py` | 100 | table metadata |
| `tokenizer.py` | 200 | SQL lexer |
| `parser.py` | 600 | recursive descent parser |
| `executor.py` | 400 | AST -> storage |
| `database.py` | 100 | public API |
| `repl.py` | 350 | interactive SQL shell (zero runtime deps) |

Budgets reflect the proposal's `<= N` line ceilings. See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) for what MVP does NOT do.
