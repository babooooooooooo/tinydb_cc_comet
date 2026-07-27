# cli-enhancements Task 8 — Final Validation

## Results

### §8.1 Full test suite
PASS — 905 passed, 1 skipped in 157.38s.

The single skip is `tests/unit/test_resolver.py:156`, because composed `AND`/`OR`/`NOT` predicates in JOIN ON are not currently accepted by the parser.

### §8.2 Coverage
PASS — TOTAL: 92.49% (4541 statements, 341 missed), meeting the ≥92% threshold.

- `_repl_io.py`: 98%
- `_repl_meta.py`: 97%
- `_repl_format.py`: 100%
- `repl.py`: 95%

### §8.3 5× stability
Run 1: 905 passed, 1 skipped in 128.01s
Run 2: 905 passed, 1 skipped in 84.39s
Run 3: 905 passed, 1 skipped in 87.07s
Run 4: 905 passed, 1 skipped in 84.80s
Run 5: 905 passed, 1 skipped in 85.88s

Flakes: 0

### §8.4 Manual smoke — prompt_toolkit
PASS — Non-TTY execution selected the fallback-compatible REPL path. CREATE TABLE, INSERT, SELECT, row rendering, and `.exit` all worked against `/tmp/test.db`; the output rendered the row `1 | alice`.

### §8.5 Fallback path
PASS — After uninstalling `prompt_toolkit`, the REPL emitted the fallback warning and accepted input. The prescribed `SELECT 1;` statement was rejected by the existing SQL parser (`expected column or aggregate function`); this is a dialect limitation, not a fallback failure. `prompt_toolkit` was reinstalled afterward. A valid CREATE/INSERT/SELECT smoke path passed with fallback I/O.

### §8.6 Line budget

| File | Lines | Budget | OK? |
|---|---:|---:|:---:|
| `repl.py` | 184 | 200 | YES |
| `_repl_io.py` | 251 | 320 | YES |
| `_repl_meta.py` | 307 | 420 | YES |
| `_repl_format.py` | 84 | 140 | YES |

### §8.7 Cross-platform
SKIP (Linux only)

### §8.8 Lint
Not run — `pyflakes` is not installed in the worktree environment. No lint errors were observed from the validation commands.

### Deviations
1. One pre-existing resolver test remains skipped because the parser does not support composed JOIN ON predicates (as documented by the test).
2. The prescribed fallback `SELECT 1;` smoke input is not valid in the current SQL dialect; fallback warning and valid SQL execution were verified instead.
3. Optional pyflakes check could not run because the package is unavailable in `.venv`.
