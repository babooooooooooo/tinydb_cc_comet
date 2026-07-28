# tinydb_comet — Database / Orchestration / Plan Code Review (2026-07-28)

> **Scope:** `database.py`, `transaction.py` (cross), `plan.py`, `_filelock.py`
> **HEAD:** `08a9ca5`

## Findings

### `src/tinydb/database.py`

**T-DB-01 [HIGH] — database.py:79-97** — Exceptions after `Pager` construction do not close the pager; OS lock + file descriptor leak.

**Verified** at the constructor. After `Pager(...)` is constructed (lock acquired, mmap opened, WAL opened), if `Catalog.from_bytes` or index rebuilding raises, `Database.__init__` exits without calling `Pager.close()`. The OS flock persists until process exit.

**Cross-validated:** Storage agent agrees (T-ST-04 / T-ST-05).

**Fix:** Restructure `__init__` so each step is acquired in a sequence that allows clean teardown on failure. Easiest: wrap the post-Pager body in try/except: `self.close(); raise`.

---

**T-DB-02 [HIGH] — database.py:124-127** — `_is_closed` check-then-act race with `close()`.

```python
if self._is_closed:
    raise RuntimeError(...)
# ... plan, parse, etc ...
with self._lock:
    # execute prepared plan against possibly-torn-down pager
```

**Failure scenario:** Thread A: `_is_closed == False`, paused. Thread B: acquires lock, closes pager, sets `_is_closed = True`. Thread A: acquires lock, executes against closed pager.

**Same bug at `explain_plan()` (lines 160-163)** — and arguably worse because planning can use cached catalog state without touching the pager.

**Cross-validated:** not flagged in 2026-07-27 review (that review focused on R1: close-releases-lock).

**Fix:** Move `_is_closed` check INSIDE the `with self._lock:` block.

---

**T-DB-03 [MED] — database.py:190-194** — If `Pager.close()` itself raises, `_is_closed` is never set even though `Pager.close()` may have released the lock and mmap.

**Failure scenario:** `FileLock.release()` raises → caller sees exception → later `execute()` operates on partially closed, unlocked pager.

**Fix:** Wrap `_is_closed = True` in a way that survives close failures; or document the trade-off explicitly.

---

### `src/tinydb/transaction.py`

**T-DB-04 [HIGH] — transaction.py:45-50** — Same finding as T-ST-01: WAL ordering. Listed again here for completeness because both agents flagged it.

---

**T-DB-05 [HIGH] — transaction.py:45-50** — Exception during commit leaves transaction in ACTIVE state even after partial durable side effects.

**Failure scenario:** Mid-commit exception → caller sees failed commit. Calls `rollback()` → tries to append ROLLBACK to WAL, but some main pages were already written and not undoable. Recovery sees COMMIT (because it was appended before the failure point in the success path) but the transaction state was never updated to COMMITTED.

**Fix:** On exception during commit, explicitly set `self._state = TxnState.ROLLED_BACK` (or a new `FAILED` state) BEFORE re-raising. Recovery should treat FAILED as "no COMMIT applied; everything in pending_writes is discarded."

---

**T-DB-06 [MED] — transaction.py:39-40** — Same finding as T-ST-02: `write_page()` mutates `pending_writes` before `wal_append_page`.

---

### `src/tinydb/_filelock.py`

**T-DB-07 [MED] — _filelock.py:12-16, 29-41** — Cross-platform: `_HAS_FCNTL=False` doesn't bind `fcntl`; `try_acquire()` raises `NameError` instead of the documented no-op.

Same as T-ST-15. Listed here because the database-core agent also flagged it.

---

### `src/tinydb/plan.py`

No additional high-confidence correctness issue found beyond what was addressed in 2026-07-27 review (T-29 plan-dispatch complexity).

---

## Cross-validated with 2026-07-27 concurrency-control review

- **CC Round 1 REJECT fix (close releases lock)** — verified still in place at `database.py:180-195`.
- **TrackedRLock wrapping** — verified still patching correctly per the test suite (`tests/unit/concurrency/test_tracked_rlock.py`).
- **`_acquire_lock` inner RLock wrap** — verified: locks acquired in order, sentinel pattern intact.
- **`Database.close()` idempotent** — verified (`cc51c46` latent test fix now passes).

## Summary

| Severity | Count |
|---|---|
| HIGH | 4 (T-DB-01, T-DB-02, T-DB-04, T-DB-05) |
| MED | 3 (T-DB-03, T-DB-06, T-DB-07) |
| LOW | 0 |

**Highest-impact:** T-DB-02 (check-then-act race — not addressed in prior reviews), T-DB-01 (close-on-init-failure — pre-existing pattern bug).
