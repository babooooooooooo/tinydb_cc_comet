# Changelog

All notable changes to tinydb are documented in this file. Versions follow
[Semantic Versioning](https://semver.org/). The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — concurrency-control

- **`Database.__init__` accepts `locking: bool = True` keyword argument.**
  Default behavior: per-instance `threading.RLock` for thread safety plus
  cross-process `fcntl.flock(LOCK_EX)` on the underlying DB file fd.
  Opt-out via explicit `locking=False` for single-threaded workloads and
  Windows / macOS deployments where `fcntl` is unavailable or unreliable.

- **`Pager.__init__` accepts the same `locking` keyword argument** (storage
  layer mirror of the `Database` parameter). `Pager` itself does not hold
  a `threading.RLock`; thread safety is the `Database` layer's responsibility.

- **`tinydb.DatabaseLocked` exception** (subclass of `TinydbError`) carrying
  a `path: str` attribute. Raised from `Pager.__init__` when `fcntl.flock`
  returns `EWOULDBLOCK` / `EAGAIN` / `EINVAL` — i.e. another process already
  holds the DB file. Exported from both `tinydb` top-level and
  `tinydb.errors`.

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
  `execute()` / `explain_plan()` raise `RuntimeError("Database is closed")`.

- **New private module `tinydb._filelock`** exposing `FileLock` (per-fd
  `fcntl.flock` wrapper with `try_acquire` / `release` / context-manager).
  Implementation detail — not part of the public API.

### Notes

- Default behavior change is **non-breaking**: existing `Database(path)`
  callers gain concurrency safety transparently. Applications with external
  coordination can opt out via `Database(path, locking=False)`.
- Lock state is **not persisted** in the on-disk v3 schema header; existing
  `.db` files need no migration.
- Linux / WSL2 is the supported target platform. Windows requires
  `locking=False`; macOS is supported only with `locking=False` because
  `flock` semantics are not guaranteed there.

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

### Compatibility

- `NO_COLOR` and `TERM=dumb` disable interactive syntax highlighting and ANSI
  color output.
- Existing `tinydb-repl --database PATH` usage and the `~/.tinydb_history`
  history path remain supported in rich mode.

## 0.1.1

See [`docs/superpowers/reports/2026-07-21-v0.1.1-verify.md`](docs/superpowers/reports/2026-07-21-v0.1.1-verify.md)
for the previous release changes.
