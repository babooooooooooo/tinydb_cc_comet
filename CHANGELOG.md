# Changelog

All notable changes to tinydb are documented in this file. Versions follow
[Semantic Versioning](https://semver.org/). The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [v0.20] — 2026-07-28

### Added — concurrency-control

- **`Database.__init__` accepts `locking: bool = True` keyword argument.**
  Default behavior: per-instance `threading.RLock` for thread safety plus
  cross-process `fcntl.flock(LOCK_EX)` on the underlying DB file fd.
  Opt-out via explicit `locking=False` for single-threaded workloads and
  Windows / macOS deployments where `fcntl` is unavailable or unreliable.

- **`Pager.__init__` accepts the same `locking` keyword argument** (storage
  layer mirror of the `Database` parameter). `Pager` itself does not hold
  a `threading.RLock`; thread safety is the `Database` layer's responsibility.

- **`tinydb.DatabaseLocked` exception** (subclass of `TinydbError`, **and
  `ExecutionError` as of v0.20** — `review-fixes` Item 8 / T7) carrying a
  `path: str` attribute. Raised from `Pager.__init__` when `fcntl.flock`
  returns `EWOULDBLOCK` / `EAGAIN` / `EINVAL` — i.e. another process already
  holds the DB file. Exported from both `tinydb` top-level and
  `tinydb.errors`. `except ExecutionError` now catches it.

- **Cross-process exclusive access via `fcntl.flock`.** The lock is held on
  the DB file fd itself (not a sidecar `<db>.lock` file); released by OS on
  fd close, so process crashes do not strand the lock. A second opener
  fails within ~100 ms (`LOCK_NB`) instead of blocking.

- **Per-instance `threading.RLock` on `Database`.** Coarse-grained,
  reentrant — wraps the entire `tokenize → parse → execute → return` path
  in `execute()` / `explain_plan()`. Reentrant so helpers that internally
  invoke other locked methods do not deadlock.

- **`:memory:` mode acquires only the thread lock.** No `fcntl.flock` call
  is made for `Database(":memory:")` — the file does not exist and memory
  is private to the process anyway.

- **`Database.close()` is idempotent** and releases all locks (RLock state
  reset + `Pager.close()` closes fd, releasing flock). After `close()`,
  `execute()` / `explain_plan()` raise `RuntimeError("Database is closed")`
  — race-safe `_is_closed` flag hardened in v0.20 (`review-fixes` Item 4).

- **New private module `tinydb._filelock`** exposing `FileLock` (per-fd
  `fcntl.flock` wrapper with `try_acquire` / `release` / context-manager).
  Implementation detail — not part of the public API.

### Added — cli-enhancements

- Added the `tinydb-repl` command with multiline SQL input, including
  parenthesis, quote, comment, and semicolon-aware continuation, plus
  prompt-toolkit line editing and Pygments SQL syntax highlighting when the
  optional packages are available.
- Added 12 REPL meta-command names: `.exit`, `.quit`, `.help`, `.tables`,
  `.schema`, `.read`, `.explain`, `.indexes`, `.stats`, `.timer`, `.format`,
  and `.color`.
- Added three query output formats: aligned `table`, RFC 4180 `csv`, and
  JSON-array `json`.
- Added `.timer on|off` for query timing and `.color on|off` for the session's
  color preference.
- Added a soft fallback to the standard-library `input()` adapter when
  `prompt_toolkit` is not installed. SQL and meta commands remain available
  without syntax highlighting or advanced line editing.

### Added — review-fixes (v0.20 架构级 hardening, 9 项 HIGH)

v0.20 是一次 review-driven hardening change，闭合 9 个 HIGH review findings
（`tinydb-review-2026-07-28-fixes`，merged via `ab25451`）。每项修复对应
RED → GREEN 测试；3 个 deviation 记录在 `tasks.md §9` + verify report。

- **`wal.py` write-ahead commit ordering** (Item 1) — commit record 必须在
  `fsync(main)` 之前落盘，避免 crash 时丢已提交事务。
- **`btree.py` leaf split right.next_leaf_id patch** (Item 2) — split 后
  立刻 patch `right.next_leaf_id` 指向新的最右 leaf，否则 range scan 跨
  split 边界会漏行。
- **`catalog.py` overflow 真分裂 (greedy)** (Item 3) — multi-page catalog
  不再依赖 `_table_data_pages` workaround；silent truncation 删除。
- **`database.py` race-safe `_is_closed` + init cleanup** (Items 4 + 6) —
  `_is_closed` 标志位在并发路径上原子可见；`__init__` 失败路径上已部分构造
  资源会被清理（file handles / pager / filelock）。
- **`_repl_meta.py` `.read` O(n²) → O(n)** (Item 5) — `buf += char`
  → list-append + `''.join()`；新增 3 个 perf 基准 (`-m slow`，pytest-cov
  默认排除 6× overhead)。
- **`type_system.py` codec round-trip 对称性 + VARCHAR/CHAR decode 边界**
  (Items 7 + 9) — 不再静默接受超过声明长度的字节；`codec_for(type_name,
  params)` 参数缺失/类型错误直接抛 `CodecError`。
- **`errors.py` `DatabaseLocked(ExecutionError)` parent alignment**
  (Item 8) — 让统一 `except ExecutionError` 能捕获跨进程争用和 SQL 执行
  错误。
- **`database.py` `close()` 后 `execute()` 抛 `RuntimeError`** (part of
  Item 4) — `close()` 幂等；race-safe 标志位。

### Notes

- Default behavior change is **non-breaking**: existing `Database(path)`
  callers gain concurrency safety transparently. Applications with external
  coordination can opt out via `Database(path, locking=False)`.
- Lock state is **not persisted** in the on-disk v3 schema header; existing
  `.db` files need no migration.
- Linux / WSL2 is the supported target platform. Windows requires
  `locking=False`; macOS is supported only with `locking=False` because
  `flock` semantics are not guaranteed there.
- `pytest -m slow` triggers perf benchmarks (REPL `.read` 5 MB / 16 MB
  load + small correctness); default run excludes them via `-m 'not slow'`.

### Compatibility

- `NO_COLOR` and `TERM=dumb` disable interactive syntax highlighting and ANSI
  color output.
- `tinydb.errors.DatabaseLocked` 现在是 `ExecutionError` 的子类（之前
  `TinydbError`）。Non-breaking: `except TinydbError` 仍捕获。父类变更与
  `ConstraintViolation` 等用户错误一致。
- Existing `tinydb-repl --database PATH` usage and the `~/.tinydb_history`
  history path remain supported in rich mode.
- v0.20 review-fixes 不引入破坏性变更；所有 RED→GREEN 测试在原 spec
  边界内通过。

### Test results

- **993 passed + 2 skipped + 3 deselected** (default `-m 'not slow'`)
- **996 passed + 2 skipped** (with `-m slow`)
- **92.66% coverage** (`pytest --cov=tinydb`)，≥85% threshold
- pyflakes clean
- 完整 verify 报告: `docs/superpowers/reports/2026-07-28-review-fixes-verify.md`

## [0.1.1] — 2026-07-21

See [`docs/superpowers/reports/2026-07-21-v0.1.1-verify.md`](docs/superpowers/reports/2026-07-21-v0.1.1-verify.md)
for the previous release changes.

### Added — codec-exception-consistency (followup)

- All 15 codecs' `encode_py` delegate to `self.validate(value)` as their first
  statement, so `CodecError` is the single canonical exception for *every*
  type-level validation failure (`DECIMAL` precision overflow, `CHAR(N)`
  length overflow, indexed-path `WHERE` predicates with out-of-range literals).
- `Catalog.create_table` now rejects non-Column inputs at the type-system
  layer via `TableInfo.__post_init__`.
- Removed `parse_bool_literal` dead code (replaced by tokenizer inline +
  `_BoolCodec.parse_literal`).
- Test contract `pytest.raises(OverflowError)` rewritten to
  `pytest.raises(CodecError)` (4 tests).

### Compatibility

- `CodecError` continues to multi-inherit `(TypeError, ValueError,
  OverflowError)` as a backward-compat shim; `except (TypeError,
  ValueError, OverflowError)` still catches. New code should catch
  `CodecError` directly.

### Test results

- **689 passed** (100%), 93.53% coverage.