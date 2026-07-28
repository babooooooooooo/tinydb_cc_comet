# tinydb_comet — REPL IO / FORMAT Code Review (2026-07-28)

> **Scope:** `repl.py`, `_repl_io.py`, `_repl_format.py`, `_repl_meta.py`
> **HEAD:** `08a9ca5`
> **Working tree:** uncommitted in `_repl_meta.py` (dead-`else` removal from `08a9ca5`)

## Verification of 2026-07-27 prior review findings

Of 22 prior findings (F-01..F-22), **17 are STILL PRESENT** at HEAD.

**Fixed in commit `08a9ca5` (verified):**
- F-04 FallbackReplIO buffer leak (`_repl_io.py:218-223`, `_buf = ""` before returning meta line) — FIXED
- F-06 handle_meta multi-dot prefix (`_repl_meta.py:313-317`) — FIXED
- `_cmd_stats` silent-exception handling — FIXED (`_repl_meta.py:184-204`)
- F-22 `_cmd_indexes` "?" placeholder — STILL PRESENT (not in top-15)

## New findings (not in prior review)

### T-REPL-NEW-01 [HIGH] — performance — `_cmd_read` O(n²) on 16 MiB files

**File:** `/home/lz/projects/tinydb_comet/src/tinydb/_repl_meta.py:128-136`

```python
buf = ""
for char in text:
    buf += char                         # ← O(n²)
    if char == ";" and not _is_unterminated(buf):
        _run_sql(db, buf.strip(), state)
        buf = ""
```

**Failure scenario:** `.read my-5mb-migration.sql` runs ~1.25×10¹³ char copies. The documented `MAX_READ_FILE_BYTES = 16 * 1024 * 1024` (16 MiB) is unreachable in practice.

**Why missed:** Prior review was quality + simplification focused; this is performance. Tests use sub-KB files.

**Fix (5 LOC):**
```python
buf_parts: list[str] = []
for char in text:
    buf_parts.append(char)
    if char == ";" and not _is_unterminated(buf := "".join(buf_parts)):
        _run_sql(db, buf.strip(), state)
        buf_parts = []
```

Or scan with `text.find(';', start)` and use a streaming lexer state.

---

### T-REPL-NEW-02 [MED] — over-catching — `_run_sql` masks programming errors

**File:** `/home/lz/projects/tinydb_comet/src/tinydb/repl.py:152-154`

```python
try:
    rows = db.execute(sql, ...)
except Exception as exc:
    print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
```

**Failure scenario:** A bug in `format_rows` (e.g., missing key) raises `KeyError` mid-render. The REPL prints `ERROR: KeyError: 'foo'` and continues — the actual bug (typo in format code) is hidden from the developer. Same with any AttributeError, NameError, or TypeError raised by executor internals.

**Fix (3 LOC):**
```python
from tinydb.errors import TinydbError
try:
    rows = db.execute(sql, ...)
except TinydbError as exc:
    print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
```

Catching only `TinydbError` (or specific subclasses) lets programming errors propagate to the outer handler.

---

## Findings still present from prior review (top 11)

### `_repl_meta.py`

| # | Sev | Lines | Description |
|---|-----|-------|-------------|
| F-03 | HIGH | 247-263, 323-327 | `_cmd_color` is the only handler that takes `io`. Dispatcher special-cases `cmd == "color"` and bypasses `META_COMMANDS[cmd](...)`. The `Callable[[List[str], Database, ReplState], bool]` annotation is a lie. |
| F-04 | HIGH | 92-94, 106-108, 150-152, 230-232, 239-241, 253-255 | Six commands duplicate the same `if not args / if not args or args[0] not in (...)` boilerplate. |
| F-13 | MED | 251, 294 | `io: "object \| None" = None` is a string forward-ref to a type that doesn't exist. Replace with `ReplIOProtocol \| None`. |
| F-17 | LOW | 15 | `field` imported but unused. |
| F-09 | LOW | module + all `_cmd_*` docstrings | Mixed Chinese / English docstrings — pick one language (English matches repl.py / _repl_io.py). |

### `_repl_io.py`

| # | Sev | Lines | Description |
|---|-----|-------|-------------|
| F-05 | HIGH | 40-100 | `_is_unterminated` CC=27, 60 LOC, 4 boolean flags + paren counter. Verified CORRECT for SQLite-style SQL, but maintainability risk is high. Decompose into `_scan_string(buf, i, quote)`, `_scan_line_comment(buf, i)`, `_scan_block_comment(buf, i)`. |
| F-08 | MED | 103-114 | `ReplIOProtocol` declares 3 methods; `add_history` / `save_history` are no-ops on both concrete impls; `@runtime_checkable` but never used as isinstance check. Drop or trim. |

### `repl.py`

| # | Sev | Lines | Description |
|---|-----|-------|-------------|
| F-14 | HIGH | 15, 178 | `_format_table` imported + re-exported but never called in `repl.py`. Only test consumer. |
| F-02 | HIGH | 32, 176 | `HISTORY_LENGTH = 1000` is module-level and exported, but no code path reads it. Either wire into FileHistory or drop. |
| F-07 | MED | 40 | `_ExitRepl = _ExitReplSignal` alias not exported, not referenced anywhere. |
| F-11 | MED | 37, 59, 62, 182 | Module-level `_state = ReplState()` reassigned via `global _state` inside `main()`. Anti-pattern per `common/coding-style.md` immutability rule. |
| F-10 | MED | 43-97 | `main()` CC=13 — 4 jobs: CLI parsing, IO selection, DB lifecycle, loop invocation. Extract `_parse_db_arg`, `_select_io`. The check `_repl_io._HAS_PROMPT_TOOLKIT and _HAS_PROMPT_TOOLKIT` (line 75) is doubly redundant. |
| F-06 | MED | 149 | `VALID_OUTPUT_FORMATS = ("table", "csv", "json")` defined 3× across repl.py / _repl_meta.py / _repl_format.py. |

## Highest-ROI fixes (impact per LOC)

| # | Fix | LOC saved | Risk |
|---|-----|-----------|------|
| 1 | T-REPL-NEW-01 — `_cmd_read` list-accumulator | +5 | LOW (single function) |
| 2 | T-REPL-NEW-02 — `_run_sql` narrow exception | -3 | LOW (improves diagnostics) |
| 3 | F-02 + F-07 + F-11 + F-14 — drop dead aliases / globals | -6 + 1 test deletion | LOW |
| 4 | F-06 — single `VALID_FORMATS` source | -3 | LOW |
| 5 | F-04 — centralize arg validation in `MetaCommand.__call__` | ~-20 | MED (registration-site shape change) |

**Net:** ~25 LOC reduction + critical performance fix, in ~150 LOC of changes.
