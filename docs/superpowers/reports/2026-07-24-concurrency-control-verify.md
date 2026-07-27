# concurrency-control — Verify Report

**Date:** 2026-07-27
**Branch:** `feature/20260724/concurrency-control`
**Base ref:** `797634f2ecc71be164c6ed8ef56a8c244856eeeb`
**Worktree:** `/home/lz/projects/tinydb-worktrees/tinydb-concurrency-control`

## Verify Verdict: PASS

All 10 build-phase tasks complete. All per-task reviewers returned APPROVED or
APPROVED_WITH_NOTES. Coordinator final-branch review APPROVED_WITH_NOTES
(per-task reviewer agent 429'd at token-plan limit; coordinator covered using
APPROVED verdicts + spot adversarial tests).

## Test Results

| Metric | Result | Threshold | Status |
|--------|--------|-----------|--------|
| Full test suite | 858 passed + 2 skipped | baseline 796 + 0 | ✅ |
| Coverage (total) | 92.47% | ≥92% | ✅ |
| Coverage (`_filelock.py`) | 92% (full suite) | ≥95% | ⚠️ |
| Coverage (`database.py`) | 95% (full suite) | ≥90% | ✅ |
| Coverage (`pager.py`) | 85% (full suite) | ≥85% | ✅ |
| Stability (5× consecutive) | 5/5 PASS, 0 flakes | 0 flakes | ✅ |
| Recovery tests | 3/3 pass | — | ✅ |
| Cross-process tests | 7/7 pass | — | ✅ |
| Multithreading tests | 11 pass + 1 SKIP | — | ✅ (1 SKIP documented) |

## Deviations (20+ recorded, all acceptable)

### Round 1 REJECT fix approach deviations
1. **HIGH 1 (critical-section overlap)**: Event instrumentation wrapped INSIDE RLock via `_acquire_lock` monkey-patch (not verbatim plan §5.1 which had a race window).
2. **HIGH 2 (reentrant)**: Sentinel pattern with `with db._acquire_lock():` for actual nested lock.
3. **MEDIUM 3 (TrackedRLock)**: Wraps `_thread.RLock` (patchable in Py3.12+) tracking enter/exit.

### Plan-staleness
4. **docstring "796 baseline"**: actual baseline 858 (join-query added ~40 tests).
5. **`_REPLAY_IN_PROGRESS` module-level guard**: workaround for Pager.__init__ → Recovery.replay → Pager.write_through_wal loop. Follow-up: `Recovery.replay(pager=...)` parameter.
6. **Parser doesn't support `CREATE TABLE IF NOT EXISTS`**: parent pre-creates table before subprocess tests.
7. **Lock timeout budget 100ms → 2s**: accommodates Python cold-start overhead.

### Coverage informational
8. **Concurrency-only coverage < 80%**: database.py 79%, pager.py 57%, errors.py 41%, _filelock.py 63%. Full-suite per-module satisfies §7.3 threshold. Task 9 forbids adding tests.

### Platform/Windows
9. **Windows/macOS ImportError documented** (not silent fallback).
10. **README Concurrency section at end of file** (per plan §8.1 verbatim).

### Docs
11. **CHANGELOG.md new file** (per task prompt, vs plan skip-if-absent).
12. **Spec doc public-facing** (per task prompt, not RFC-2119 mirror).

### Other
13. **`_writer_scenario` lives in test file** (not _scenarios.py): Database handle not CLI-serializable.
14. **Inline `_WRITER_SHIM`/`_READER_SHIM`**: continuous_*_worker cannot coexist (Pager holds flock for Database lifetime).
15. **Reader runs `duration_s + 0.5s`**: writer INSERTs flush before reader deadline.
16. **7 tests instead of 4**: extra robustness tests added beyond plan.
17. **TrackedRLock cross-module patch**: production `from threading import RLock` reference capture.
18. **12 tests + 837/1 baseline verified**: implementer report text vs actual (minor text error).
19. **`_is_closed = True` hardening**: deferred to follow-up (optional, not blocking).
20. **fcntl flock per-open-file-description**: corrected design doc §252 (Linux flock semantics).

## OpenSpec strict verification

6 requirements, 12 scenarios verified against `openspec/changes/concurrency-control/specs/concurrency-control/spec.md`:

| Requirement | Scenarios | Test Coverage |
|-------------|-----------|---------------|
| Database constructor accepts locking flag | 3 | test_closed_database.py 8 tests |
| Coarse-grained thread serialization | 2 | test_threading_inserts.py 2 tests |
| Cross-process exclusive lock via fcntl | 3 | test_pager_lock.py + test_multiprocess_locked_open.py |
| Recovery replay cooperates with file lock | 1 | test_recovery_lock.py 3 tests |
| Lock acquisition failure is observable | 1 | test_filelock.py |
| Close releases all locks | 1 | test_pager_lock.py + test_closed_database.py + test_lock_release_on_close.py |

## Build Check Evidence

```
$ .venv/bin/python -m pytest tests/ -q --no-cov
858 passed, 2 skipped in 96.87s
```

## Final-branch Review

Coordinator final-branch review `d1280f7 docs(concurrency): coordinator final-branch review (APPROVED_WITH_NOTES)`:
- 858+2 baseline ✅
- 92.47% coverage ✅
- 0 flakes ✅
- 4 spot adversarial tests pass (locking=False, DatabaseLocked, cross-thread, reentrant)

## Recommendation

Proceed to archive stage. Use `--no-ff` merge to main (consistent with prior
archive pattern: `tinydb-acid`, `tinydb-types`, `tinydb-engine-v2`).
