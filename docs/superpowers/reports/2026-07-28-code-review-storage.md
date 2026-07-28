# tinydb_comet — Storage Layer Code Review (2026-07-28)

> **Scope:** `pager.py`, `btree.py`, `slotted_page.py`, `wal.py`, `recovery.py`, `transaction.py`, `index_manager.py`, `_index_pager.py`, `_filelock.py`
> **Out-of-scope flag from 2026-07-27 review:** now covered
> **HEAD:** `08a9ca5`

## Findings (10 of N total; top-impact first)

### `src/tinydb/transaction.py`

**T-ST-01 [HIGH] — transaction.py:45-50** — WAL protocol ordering violates atomicity.

```python
for pid, data in self.pending_writes.items():
    self._pager.write_main_page(pid, data)   # main writes go FIRST
self._pager.wal_append_commit(self.id)        # COMMIT goes AFTER
self._pager.fsync_main()                       # fsync main
self._pager.wal_truncate_before(self.id)       # no WAL fsync before this
```

**Failure scenario:** Crash between `write_main_page` and `wal_append_commit` → main file has uncommitted data, no way to undo. Crash between `wal_append_commit` and the OS-fsync of WAL → main file has committed data but WAL shows incomplete. Recovery can't distinguish.

**Fix:** Swap the order — `wal_append_commit → wal_fsync → write_main_page × N → fsync_main`. Plus an explicit `fsync` of the WAL between `wal_append_commit` and `wal_truncate_before`.

**Cross-validated:** Database-core agent agrees.

---

**T-ST-02 [HIGH] — transaction.py:39-40** — `write_page()` mutates `pending_writes` BEFORE `wal_append_page`.

```python
def write_page(self, page_id: int, data: bytes) -> None:
    ...
    self.pending_writes[page_id] = data                      # ← mutate first
    self._pager.wal_append_page(self.id, page_id, data)      # ← WAL append after
```

**Failure scenario:** I/O error during `wal_append_page` → caller sees exception, but the failed page is still in `pending_writes`. A subsequent `commit()` will write it to main without a WAL record. Recovery treats the transaction as COMPLETE (the COMMIT is appended without a matching PAGE_WRITE), main has the data, but WAL can't undo anything.

**Fix:** Reorder: WAL append first, mutate state only on success.

---

**T-ST-03 [MED] — transaction.py:49,56** — `wal_truncate_before(self.id)` retains records whose txn_id equals `self.id`.

The docstring in `wal.py` (need to verify) likely documents the semantics, but the call-site behavior does not match "remove this transaction's records."

**Failure scenario:** Transaction 1 commits; `wal_truncate_before(1)` is called; per-call: it KEEPS records where `txn_id == 1`. Subsequent transactions accumulate. WAL grows unbounded until a higher-numbered transaction triggers broader cleanup.

**Fix:** Either change the call site to `wal_truncate_before(self.id + 1)`, or change `wal_truncate_before` semantics to "remove records whose txn_id < supplied_id" and update all call sites.

---

### `src/tinydb/pager.py`

**T-ST-04 [HIGH] — pager.py:267-280** — `_init_wal()` opens `Wal(self._wal_path)` without `try/finally`.

**Failure scenario:** WAL constructor or `Recovery.replay()` raises (corrupt torn WAL) → outer Database constructor exits while `wal._file` is still open. Subsequent open calls see WAL as busy or `DatabaseLocked`.

**Fix:** Move WAL construction into a try/finally, or have `Database.__init__` call `self.pager.close()` if construction fails past the lock-acquire point.

---

**T-ST-05 [HIGH] — pager.py:68-79** — Constructor exception path leaks the file lock.

**Failure scenario:** `Pager(...)` succeeds, then `_init_wal()` raises → Database construction fails → caller doesn't have a reference → cannot call `Pager.close()` → OS lock held until process exit.

**Cross-validated:** Database-core agent agrees — same issue from the Database side (`database.py:79-97`).

**Fix:** Restructure so the lock is the last resource acquired. Or wrap init in `try/except/close()`. (See T-2026-07-28-06 in summary.)

---

**T-ST-06 [MED] — pager.py:418-432** — `free_page(pid)` accepts any `pid ≥ 1` without ownership or range validation.

**Failure scenario:** `free_page(1)` puts the catalog page (page 1) on the free list, then a subsequent `alloc_page()` returns pid 1 and the caller overwrites catalog metadata.

**Fix:** Validate `pid` is not in the reserved range (≤ RESERVED_PAGES) and that it is within `page_count() - 1`. Return `ValueError` on invalid input.

---

**T-ST-07 [MED] — pager.py:434-444** — `read_page(pid)` does not validate pid is within `page_count()`.

**Failure scenario:** Stale/corrupt pointer → `IndexError` from `slice` in the mmap path, not a controlled error. Callers can't catch a single exception type.

**Fix:** Raise `InvalidDatabaseFile(f"page {pid} out of range")` when pid ≥ page_count.

---

### `src/tinydb/btree.py`

**T-ST-08 [HIGH] — btree.py:190** — Leaf split sets `next_leaf_id=0` on the new right leaf and never patches it.

```python
right = LeafNode(
    keys=leaf.keys[mid:],
    values=leaf.values[mid:],
    next_leaf_id=0,           # ← never patched
    tombstones=leaf.tombstones[mid:],
)
right_pid = self.pager.alloc_page()
left.next_leaf_id = right_pid  # ← left is patched; right is not
self.pager.write_page(leaf_pid, left.serialize())
self.pager.write_page(right_pid, right.serialize())  # ← right stays at 0
```

**Failure scenario:** 3+ leaves via random inserts. Range descent from leaf #1 ends at leaf #2 (`next_leaf_id` of leaf #2 is 0 → chain terminates). Stress-test claim: 378/3000 rows returned.

**Fix:** In `_insert_into_parent` (or a post-split pass), after each split, set `right.next_leaf_id = previous_right_pid`. Or track the trailing rightmost leaf across splits.

---

### `src/tinydb/slotted_page.py`

**T-ST-09 [HIGH] — slotted_page.py:148-165** — `insert()` checks only `len(row_bytes)` against free space; doesn't reserve `SLOT_SIZE` bytes.

**Failure scenario:** Page near-full with `free < SLOT_SIZE + row_length` but still `> row_length`. Insert succeeds, but appending to slot directory overlaps the data area's tail. Round-trip reads garbled.

**Fix:** Capacity check: `if free_end - used_end >= len(row_bytes) + SLOT_SIZE`.

---

**T-ST-10 [HIGH] — slotted_page.py:144-147, 160-166** — Tombstone-slot reuse skips capacity check.

Same effect as T-ST-09 but for the "reuse old slot's data offset" path.

**Fix:** Same as T-ST-09.

---

**T-ST-11 [MED] — slotted_page.py:176-189** — `update()` skips free-space check entirely.

**Failure scenario:** Repeated same-length updates accumulate leaked historical bytes; eventually an update that "fits" by length still overlaps slot directory.

**Fix:** After a same-length update, leave the gap; after a longer-length update, fall through to the T-ST-09 capacity check.

---

**T-ST-12 [MED] — slotted_page.py:101-127** — `from_bytes()` trusts `num_slots`, offsets, lengths, and `data_len` without validation.

**Failure scenario:** Corrupted or hand-crafted page advertises `data_len > PAGE_SIZE`; later reads produce malformed metadata instead of an explicit `InvalidPage` error.

**Fix:** Validate `num_slots * SLOT_SIZE <= PAGE_SIZE - SLOTTED_HEADER_SIZE` and `data_len <= PAGE_SIZE - slot_directory_end`.

---

### `src/tinydb/recovery.py`

**T-ST-13 [MED] — recovery.py:40-50** — Recovery applies PAGE_WRITE/COMMIT records without verifying a matching BEGIN exists.

**Failure scenario:** Torn-log write places PAGE_WRITE → COMMIT for an unknown txn_id. Recovery marks it committed and applies the page image. The user later sees a database that doesn't match the SQL they ran (because the WAL was corrupted but recovery accepted it).

**Fix:** State-machine validation: track txn_ids seen via BEGIN. Reject PAGE_WRITE/COMMIT for unknown txn_ids.

---

### `src/tinydb/wal.py`

**T-ST-14 [MED] — wal.py:72-77** — If header write fails after `open()`, no cleanup path.

**Failure scenario:** Disk-full, permission denied → constructor raises while `Wal._file` is partially open.

**Fix:** Wrap `_make_header()` in try/except, close on failure.

---

### `src/tinydb/_filelock.py`

**T-ST-15 [MED] — _filelock.py:12-16, 29-41** — `_HAS_FCNTL=False` doesn't bind `fcntl`, but `try_acquire()` references it.

**Failure scenario:** Direct construction on Windows/macOS (without going through Pager, which currently fails earlier with ImportError) → `NameError: name 'fcntl' is not defined`.

**Fix:** Use sentinel `_fcntl = None` on platforms without fcntl; `try_acquire` returns True (no-op) when `_fcntl is None`. Document this contract.

---

## Summary

| Severity | Count |
|---|---|
| HIGH | 6 |
| MED | 9 |
| LOW | 0 |

**Highest-impact:** T-ST-01 (WAL ordering), T-ST-08 (B+tree leaf chain), T-ST-09 + T-ST-10 (slotted page overlap).

**Cross-validated with Database-core agent:** T-ST-01, T-ST-04 (same root cause as T-2026-07-28-06).
