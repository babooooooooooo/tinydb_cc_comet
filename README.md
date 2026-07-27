# tinydb (v0.1.1)

> Minimal embedded relational database for teaching and embedding. ACID, 15 SQL types, stdlib REPL.

> **Status:** MVP complete with full ACID (`tinydb-acid`), 15 column types (`tinydb-types`), and a zero-dependency REPL (`tinydb-repl`). The latest patch (**v0.1.1**) closes seven contract gaps in the prior codec-exception-consistency fix: every codec's `encode_py` now delegates to `self.validate(value)`, so `CodecError` is the single canonical exception for *every* type-level validation failure, including `DECIMAL` precision overflow, `CHAR(N)` length overflow, and indexed-path WHERE predicates with out-of-range literals.
>
> See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) for the full scope.

## Highlights

- **DDL/DML**: `CREATE TABLE` / `DROP TABLE` / `INSERT` / `SELECT` / `UPDATE` / `DELETE` against a typed catalog
- **15 column types** — `INT` / `SMALLINT` / `BIGINT` / `FLOAT` / `DOUBLE` / `REAL` / `TEXT` / `VARCHAR(N)` / `CHAR(N)` / `BOOL` / `DECIMAL(p, s)` / `DATE` / `TIME` / `TIMESTAMP`
- **ACID**: autocommit + explicit `BEGIN` … `COMMIT` / `ROLLBACK`, WAL-backed crash recovery, single-statement atomicity
- **REPL**: zero external runtime deps, multi-line SQL, reverse-generated `.schema`, `.tables`, `.read <file>`
- **Single source of truth for type contracts**: every codec speaks `encode_py` / `decode_bytes` / `validate`. `encode_py` *always* delegates to `self.validate(value)` as its first statement (v0.1.1), so any wrong-type / out-of-range / wrong-precision input surfaces through one canonical `CodecError` — no `AttributeError`, `TypeError`, or `OverflowError` leak.
- **Pure Python 3.11+ stdlib** — no pip dependencies for users; `hypothesis` / `pytest-cov` are dev-time only

## Quick start

```python
import tinydb
with tinydb.Database(":memory:") as db:
    db.execute("CREATE TABLE users(id INT PRIMARY KEY, name TEXT, age INT)")
    db.execute("INSERT INTO users(id, name, age) VALUES (1, 'alice', 30)")
    db.execute("INSERT INTO users(id, name, age) VALUES (2, 'bob',   25)")
    for row in db.execute("SELECT * FROM users WHERE age >= 26"):
        print(row.id, row.name, row.age)
```

Grammar notes:

- `INSERT` requires an explicit column list (no positional shorthand).
- `WHERE` accepts conjunctions/disjunctions of `column = literal` / `column op literal` (see [§ Codec contract](#codec-contract)).
- `SELECT` columns may be `*` or a comma-separated list; multi-row results come back as immutable `Row` objects accessed by column name or unpacking.

## ACID

Each `execute()` runs inside an implicit transaction (autocommit). A successful statement commits to disk via `fsync`; a failed statement auto-rolls back so the database never holds a half-applied mutation. Wrap multiple statements in an explicit `BEGIN` … `COMMIT` (or `ROLLBACK`) block to control them as a single atomic unit.

Guarantees:

- **Atomicity** — a multi-row INSERT/UPDATE/DELETE either commits every row or none.
- **Durability** — `COMMIT` performs `fsync(main)` after writing the WAL commit record.
- **Crash recovery** — on open, the WAL is replayed: committed transactions are re-applied; uncommitted transactions are discarded.

Not provided:

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

    db.execute("BEGIN")
    db.execute("DELETE FROM accounts WHERE id = 2")
    db.execute("ROLLBACK")  # id=2 is back
```

Schema upgrade notes:

- v3-schema `.db` files (`tinydb-acid`) are the current on-disk format.
- v2-schema files (from `tinydb-engine-v2`) auto-upgrade on open if no `<db>.wal` sidecar is present.
- v2-schema files WITH a WAL sidecar raise `SchemaMismatch` and require explicit migration.

## Codec contract

All column-type validation runs through the canonical codec registry in `tinydb.type_system`. Every codec exposes three methods:

| Method | Used by | Purpose |
|---|---|---|
| `encode_py(value) → bytes` | `executor`, `parse_literal` round-trip | validate Python `value`, then pack to wire bytes |
| `decode_bytes(buf, offset) → (value, next_offset)` | `executor`, scan paths | read wire bytes back into a Python value |
| `validate(value) → None` | internal anchor | throw `CodecError` if `value` violates the codec's contract |

All validation failures raise the **same** exception class, `tinydb.type_system.CodecError`. Every codec's `encode_py` calls `self.validate(value)` as its first statement (v0.1.1), so the only way to fail type validation — wrong Python type, value out of range, length overflow, precision overflow, NaN/Inf in FLOAT/DOUBLE — is `CodecError`. There are no `AttributeError`, `TypeError`, `OverflowError`, or `ValueError` leaks.

```python
codec = tinydb.type_system.codec_for("INT", ())
codec.encode_py(2**31)              # raises CodecError: INT out of range: 2147483648
codec.encode_py(3.14)               # raises CodecError: expected int for INT, got float
codec.validate(True)                # raises CodecError: expected int for INT, got bool (bool ⊂ int)

v = tinydb.type_system.codec_for("VARCHAR", (10,))
v.encode_py("x" * 11)               # raises CodecError: VARCHAR(10) length 11 exceeds max
v.encode_py("x" * 10)               # OK, packed with length prefix

c = tinydb.type_system.codec_for("CHAR", (5,))
c.encode_py("abcdef")               # raises CodecError: CHAR(5) length 6 exceeds max (v0.1.1)
d = tinydb.type_system.codec_for("DECIMAL", (5, 2))
d.encode_py(1000.00)                # raises CodecError: DECIMAL(5,2) value 1000.0 out of range (v0.1.1)
```

`CodecError` is intentionally multi-inheriting `(TypeError, ValueError, OverflowError)` so existing `except (TypeError, ValueError, OverflowError)` blocks continue to catch. New code should catch `CodecError` directly. Note: after v0.1.1, the multi-inheritance is no longer load-bearing — it exists only as a backward-compat shim.

Live REPL:

```text
tinydb> CREATE TABLE t(i INT, v VARCHAR(3), c CHAR(3));
OK
tinydb> INSERT INTO t(i, v, c) VALUES (1, 'ok', 'ok');
OK
tinydb> INSERT INTO t(i, v, c) VALUES (2147483648, 'ok', 'ok');
ERROR: CodecError: INT out of range: 2147483648
tinydb> INSERT INTO t(i, v, c) VALUES (1, 'toolong', 'ok');
ERROR: CodecError: VARCHAR(3) length 7 exceeds max
```

Cross-type `WHERE` comparisons also raise `CodecError` (the new uniform surface across all type-mismatch failures), so a single `try/except CodecError` covers boundary violations, range overflows, and length-exceeded inputs.

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

See [`docs/操作手册.md`](docs/操作手册.md) for a guided tutorial.

## Types

15 column types are supported:

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

**Literal prefixes**: `DATE '2026-07-16'`, `TIME '14:30:00'`, `TIMESTAMP '2026-07-16 14:30:00'`, `DECIMAL '99.99'`.

**Strict same-type comparison**: `WHERE col = literal` requires exact type match. Cross-type comparisons raise `CodecError`. Distinct types are not interchangeable:
- `INT` ≠ `SMALLINT` ≠ `BIGINT`
- `FLOAT` ≠ `DOUBLE`
- `VARCHAR(N)` ≠ `TEXT`
- `DATE` ≠ `TIMESTAMP`
- `DECIMAL(10, 2)` ≠ `DECIMAL(10, 4)`

**Rejected values**: `FLOAT` / `DOUBLE` reject `Infinity` and `NaN` at all paths (literal parse, encode, validate). `DECIMAL` enforces precision/scale overflow at encode. `VARCHAR(N)` / `CHAR(N)` enforce max length.

See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) § tinydb-types for the complete contract.

## Development

```bash
pip install -e ".[dev]"
pytest -q             # 689 tests
pyflakes src/tinydb/
pytest --cov=src/tinydb
```

Coverage is enforced at 85% minimum (configured in `pyproject.toml`); current measurement is ~93.5%.

Reports for each release change are at `docs/superpowers/reports/`; the latest is `2026-07-21-v0.1.1-verify.md`.

## Module map (current sizes)

| Module | Lines | Responsibility |
|---|---|---|
| `type_system.py` | 431 | 15 codecs + registry; one canonical `CodecError` for all type-level validation failures; every codec's `encode_py` delegates to `self.validate(value)` (v0.1.1) |
| `pager.py` | 491 | 4 KB page I/O, mmap + BufferedRandom paths, free-list |
| `slotted_page.py` | 208 | single-page layout, slotted record pointers, overflow chain |
| `catalog.py` | 305 | table/column metadata; `Catalog.create_table` rejects non-Column inputs |
| `tokenizer.py` | 162 | SQL lexer including typed-literal prefixes |
| `parser.py` | 1192 | recursive descent parser + AST nodes |
| `executor.py` | 1717 | AST → storage; split into 4 helper modules for transportability |
| `_executor_drop.py` | 192 | DROP-table helper |
| `_executor_snapshot.py` | 48 | snapshot/replay glue |
| `_executor_sort.py` | 56 | ORDER BY helpers |
| `_index_pager.py` | 96 | B+tree page allocation wrapper (workaround for page-id collision) |
| `_schema.py` | 92 | typed schema introspection |
| `btree.py` | 339 | B+tree (split, range, delete) |
| `index_manager.py` | 74 | B+tree lifetime, executor index maintenance hook |
| `row_codec.py` | 70 | row-level (page payload) wire format |
| `wal.py` | 185 | write-ahead log records |
| `recovery.py` | 88 | WAL → Pager replay on open |
| `transaction.py` | 58 | active-txn bookkeeping |
| `database.py` | 133 | public `Database.execute()` surface |
| `repl.py` | 302 | zero-dep interactive shell |
| `errors.py` | 79 | `TinydbError`, `ConstraintViolation`, `InvalidDatabaseFile`, … |

See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) for what MVP does NOT do.

## 多表 JOIN（v0.2 新增）

支持 `INNER` / `LEFT` / `RIGHT` / `FULL` / `CROSS JOIN`；连接条件可选 `ON` / `USING (col, ...)` / `NATURAL`；支持表别名与限定列引用 `u.id`；JOIN 结果继续走现有 `WHERE` / `GROUP BY` / `HAVING` / `ORDER BY` / `LIMIT` / `OFFSET` 与聚合函数。

示例（结合 GROUP BY + HAVING + ORDER BY + LIMIT）：

```sql
SELECT u.id, o.id, SUM(o.total) AS s
FROM users u
JOIN orders o ON u.id = o.uid
GROUP BY u.id
HAVING SUM(o.total) > 100
ORDER BY s DESC
LIMIT 10;
```

USING / NATURAL 合并键采用 `Coalesce` 语义：左边非空取左边，否则取右边，两边都 `NULL` 则为 `NULL`。合并键标签为非限定的列名。

错误诊断：未知表 → `UnknownSource`；歧义裸列 → `AmbiguousColumn`；USING 列缺失 → `MissingUsingKey`；类型不兼容 → `IncompatibleKeyTypes`。所有解析错误都是 `tinydb.ResolutionError`（`ExecutionError` 的子类）。

已知限制（`docs/MVP_LIMITATIONS.md` § v0.2 JOIN 内存限制）：nested-loop + 物化宽行策略无硬性行数上限；极大结果集（百万级 × 百万级 CROSS JOIN）会消耗大量内存。建议在 application 层显式加 `LIMIT` 或在 `WHERE` 中加过滤条件。

## Concurrency（v0.2 新增）

tinydb 默认提供双层并发保护：

1. **进程内**: `Database` 实例持有 `threading.RLock`（粗粒度、可重入），串行化 `execute()` 与 `explain_plan()`。
2. **跨进程**: `Pager` 在打开 DB 文件后立即获取 `fcntl.flock(LOCK_EX)`，第二个进程打开同一 DB 文件立即抛 `DatabaseLocked`。

### 用法

```python
import tinydb
from tinydb import DatabaseLocked

# 默认：双层锁均启用
db = tinydb.Database("/path/to/file.db")

# 单线程 / 外部已有并发控制：显式 opt-out（零开销）
db = tinydb.Database("/path/to/file.db", locking=False)

# :memory: 模式：仅线程锁，无文件锁
db = tinydb.Database(":memory:")

# 跨进程争用
# 进程 A: db = tinydb.Database("/x.db")  →  持有 flock
# 进程 B: db = tinydb.Database("/x.db")  →  抛 DatabaseLocked("/x.db")
try:
    db = tinydb.Database("/x.db")
except DatabaseLocked as e:
    print(f"DB {e.path} is locked by another process")
```

### 平台

- **Linux / WSL2**: `fcntl.flock` 完整支持，默认行为即双层锁。
- **Windows / 缺少 fcntl 的平台**: `locking=True` 在 `Pager.__init__` 立即抛 `ImportError("tinydb concurrency control requires fcntl (Linux/WSL only)")`。需要在该平台运行时应显式传 `locking=False` 走单线程路径。
- **macOS**: `fcntl` 模块存在但 `flock` 语义不可靠；建议在 macOS 上同样显式 `locking=False`，由应用层提供并发控制。

### 限制

- **读并发被牺牲**：无 MVCC / reader-writer split，所有 `execute()`（包括 `SELECT`）都走 EX 锁，长操作会阻塞其他线程。
- **持锁时间含 fsync**：每次写提交 `fsync(main)` 在持锁期间执行；嵌入式 OLTP 单语句 fsync 通常 < 10ms 可接受。
- **`:memory:` 仅线程安全**：内存 dict 跨进程天然隔离（私有内存），不获取文件锁。
- **`Database.close()` 后不可用**：再调用 `execute()` / `explain_plan()` 抛 `RuntimeError("Database is closed")`；`close()` 自身幂等。
- **`_REPLAY_IN_PROGRESS` 模块级 guard** 是已知 workaround（见 `docs/superpowers/specs/concurrency-control.md` § 已知偏差）。

完整公开契约见 [`docs/superpowers/specs/concurrency-control.md`](docs/superpowers/specs/concurrency-control.md)。
