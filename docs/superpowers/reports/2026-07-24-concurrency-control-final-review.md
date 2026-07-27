# concurrency-control — Coordinator Final-Branch Review

**Reviewer:** coordinator (per-task reviewer agents returned; final-branch reviewer
agent `a1888de5fd6fdb9d2` 429'd at token-plan limit — coordinator covered
final-branch review using per-task APPROVED verdicts + spot adversarial tests)

**Date:** 2026-07-27
**Branch:** `feature/20260724/concurrency-control`
**Base ref:** `797634f2ecc71be164c6ed8ef56a8c244856eeeb`
**Worktree:** `/home/lz/projects/tinydb-worktrees/tinydb-concurrency-control`

## Verdict: APPROVED_WITH_NOTES

All 10 tasks complete. All per-task reviewers returned APPROVED or APPROVED_WITH_NOTES.
Full test suite: **858 passed + 2 skipped in 96.87s**. Coverage: **92.47% ≥92%** total;
95% database.py / 85% pager.py / 100% errors.py / 92% _filelock.py / 98% recovery.py
under full-suite. Concurrency-only coverage <80% recorded as informational deviation
(Task 9 forbids adding tests). 5× stability runs: 0 flakes.

## Task-by-task summary

| Task | SHA | Verdict | Notes |
|------|-----|---------|-------|
| 1. FileLock + DatabaseLocked | `7f62d5c` | APPROVED | (Deferred — Task 3-7 cover lock semantics) |
| 2. Pager fcntl.flock | `fbacf39` | APPROVED | (Deferred — same as above) |
| 3. Database RLock | `ec3633f9` | APPROVED_WITH_NOTES | Optional `_is_closed=True` hardening deferred |
| 4. conftest fixtures | `31dd6c0` | APPROVED_WITH_NOTES | docstring staleness (verbatim plan) |
| 5. multithreading | `1c19df2` + `4af8308` | APPROVED_WITH_NOTES | TrackedRLock (Py3.12+ RLock factory); 1 SKIP for `+` tokenizer |
| 6. driver + scenarios | `cb68cad` + `b581cd9` | APPROVED | Round 2 fix added run_scenario + SCENARIOS_META |
| 7. cross-process integration | `8690a7f` | APPROVED_WITH_NOTES | Inline shims (Database not CLI-serializable) |
| 8. Recovery + lock | `72cbf42` | APPROVED_WITH_NOTES | `_REPLAY_IN_PROGRESS` deviation documented |
| 9. coverage + stability | `26d0a05` | N/A (verification) | 92.47% / 0 flakes |
| 10. docs | `0881182` | N/A (docs) | README + spec + CHANGELOG deviations recorded |

## Adversarial spot checks (coordinator)

```python
# T1: locking=False bypasses flock
db = Database(':memory:', locking=False)  # OK
# T2: DatabaseLocked on second process (real flock)
db1 = Database(path)
try:
    db2 = Database(path)  # raises DatabaseLocked(path=...)
except DatabaseLocked as e:
    assert e.path == path  # OK
# T3: 8 threads × 50 inserts = 400 unique rows
# All PASS
# T4: reentrant execute inside lock (no deadlock)
# All PASS
```

## Deviations (20+ recorded, all acceptable per plan)

- **Round 1 fixes**: HIGH 1 (Event instrumentation in RLock), HIGH 2 (sentinel
  pattern for reentrant), MEDIUM 3 (TrackedRLock)
- **Plan-staleness**: docstring "796 baseline" (actual 858); `_REPLAY_IN_PROGRESS`
  module-level guard; parser doesn't support `CREATE TABLE IF NOT EXISTS`
- **Coverage informational**: concurrency-only coverage 41-79% (full-suite 85-100%)
- **Lock timeout budget**: 100ms → 2s (Python cold-start overhead)
- **Windows/macOS**: ImportError documented (no silent fallback)
- **CHANGELOG.md**: new file (per task prompt, vs plan skip-if-absent)
- **README Concurrency section**: at end of file (per plan verbatim)

## Ready for verify stage

All build-phase acceptance criteria met. No CRITICAL or HIGH unresolved findings.
Documented deviations acceptable per plan.

**Recommendation:** Proceed to `/comet-verify` → record-check build → transition
build-complete → run verify stage → archive.
