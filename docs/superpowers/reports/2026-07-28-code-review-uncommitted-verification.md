# Working-tree Uncommitted Fixes — Verification Report (2026-07-28)

> **Scope:** `git diff` against HEAD `08a9ca5`. Four files: +58/-61 LOC.
> **Verifier:** Sonnet general-purpose agent with live Python execution.
> **Verdict:** APPROVED.

## 1. Diff verification

| # | File | Stated Fix | Verified |
|---|------|------------|----------|
| 1 | `src/tinydb/executor.py` | Rewrote `_normalize_having_for_executor` to return `(alias, op, lit)` strings instead of integer positions. Added shared `_COMPARE_OPS` dict (replacing 6-branch if/elif in `_compare`). Removed unreachable `isinstance(...) AggregateCall` branch. Fixed docstring. | YES |
| 2 | `src/tinydb/_join_executor.py` | Removed local `_OPERATIONS` dict; now `from tinydb.executor import _COMPARE_OPS as _OPERATIONS`. | YES |
| 3 | `src/tinydb/_repl_meta.py` | Removed dead `else cmd_token` branch in `handle_meta` ternary; added comment explaining the invariant. | YES |
| 4 | `src/tinydb/resolver.py` | Extracted `_drop_right_by_source(per_join_keys)` helper (used in 2 places). Removed unnecessary `if not per_join_keys:` fast-path in `_merged_schema`. | YES |

Diff stat matches stated `+58/-61` exactly (`git diff --stat`).

## 2. Sanity check (live execution)

```python
from tinydb import Database
db = Database(":memory:")
db.execute("CREATE TABLE users (dept TEXT, age INT)")
db.execute("INSERT INTO users (dept, age) VALUES ('eng', 30), ('eng', 25), ('sales', 40)")

# Alias form (the path the previous fix unblocked)
r1 = db.execute("SELECT dept, COUNT(*) AS n FROM users GROUP BY dept HAVING n > 1")
# Output: [('eng', 2)]

# Inline aggregate (the path that was crashing before this fix)
r2 = db.execute("SELECT dept, COUNT(*) FROM users GROUP BY dept HAVING COUNT(*) > 1")
# Output: [('eng', 2)]
```

Both forms produce the expected output. The High-severity bug (incorrect integer-position return from `_normalize_having_for_executor`) is fixed.

## 3. Additional verification points (verifier's checks)

- `HAVING n < 3` → correct counts
- `HAVING COUNT(salary) > 1` (alias-with-arg) → correct
- `JOIN ON a.x < b.x` (left=1 row, right=2 rows) → `[(10, 200)]` — verifies shared `_COMPARE_OPS` works in the join path
- `JOIN ON a.x = b.x` → matches expected
- `JOIN ON a.x > b.x` → matches expected

## 4. Full test suite

```
967 passed, 2 skipped in 96.73s (0:01:36)
Coverage: 92.59%
```

Baseline preserved. The 2 skips are pre-existing (documented limitations of MVP tokenizer and Task 8 follow-up).

## 5. New regressions / bugs introduced

| # | Finding | Severity |
|---|---------|----------|
| 1 | **Error-message clarity regression** at `executor.py` line ~348: When `apply_having` is called directly with an `AggregateCall` object (NOT via `_normalize_having_for_executor`), the error message is now less specific (`unknown column AggregateCall(...) in HAVING` vs the previous `HAVING with inline aggregate not supported; use the SELECT-list alias`). Production path (Executor.execute) is unaffected because it always normalizes first. Documented as cosmetic, not a blocker. | LOW |
| 2 | **Type-annotation improvement**: `_normalize_having_for_executor` now has `Optional[tuple]` return annotation (was `Any`). Improvement, not regression. | N/A |
| 3 | **NULL-comparison contract preserved**: Confirmed that `executor._compare` is NULL-safe (caller checks `val is None` first) and `_join_executor._compare` is NOT NULL-safe — matches the documented design (`NULL handling lives in the caller`). The shared `_COMPARE_OPS` only changes which ops are recognized, not NULL semantics. | N/A |
| 4 | **Alias substitution correctness** verified end-to-end for both `AggregateCall.alias=None` (falls through to `_aggregate_default_alias`) and `AggregateCall.alias='n'` (uses the explicit alias). | N/A |
| 5 | **No T-01..T-34 regressions** to prior review findings. | N/A |
| 6 | **`_merged_schema` fast-path removal** is safe — unified path produces identical output for `per_join_keys == ()`. | N/A |

## 6. Overall verdict

**APPROVED.** All four stated fixes are accurate and consistent with the session summary. The canonical sanity check passes. The full test suite maintains the 967/2 baseline with 92.59% coverage. The only minor concern (less-specific error message when `apply_having` is called directly with an `AggregateCall`) is a low-severity cosmetic regression that does not affect production behavior; the fix path always normalizes first.

These changes are safe to commit at the user's discretion (per global rule, commits are user-initiated).

## 7. Suggested commit message (if user requests commit)

```
fix(quality): complete HAVING alias substitution + JOIN/executor operator unification

executor.py
  - _normalize_having_for_executor: return (alias, op, lit) instead of integer position
    (resolves HAVING COUNT(*) > 1 crashing with "unknown column 2 in HAVING")
  - extract module-level _COMPARE_OPS dict; replace 6-branch if/elif in _compare
  - drop unreachable isinstance AggregateCall branch
  - update apply_having docstring

_join_executor.py
  - drop local _OPERATIONS dict; import _COMPARE_OPS from executor

repl_meta
  - drop dead `else cmd_token` branch in handle_meta ternary

resolver
  - extract _drop_right_by_source helper used by _merged_schema + resolve()
  - drop unnecessary `if not per_join_keys:` fast-path

967/2 baseline preserved. 92.59% coverage. Net diff: +58/-61.
```
