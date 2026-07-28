# tinydb_comet — Type System / Codecs / Catalog Code Review (2026-07-28)

> **Scope:** `type_system.py`, `row_codec.py`, `_schema.py`, `catalog.py`, `errors.py`
> **HEAD:** `08a9ca5`

## Findings

### `src/tinydb/type_system.py`

**T-TC-01 [HIGH] — type_system.py:196-200** — `_IntCodec.validate` rejects the maximum representable value (e.g., 2^31 for INT) but `decode_bytes` happily reads it back.

**Failure scenario:** Insert INT `-2147483648` → `encode_py` raises `CodecError("INT out of range")`. But raw bytes `b"\x80\x00\x00\x00"` written directly via `BufferView` → `decode_bytes` returns `-2147483648` without complaint. Round-trip is asymmetric.

**Fix:** Move the bounds check to a shared helper; have both `encode_py` and `decode_bytes` use it. Or document the asymmetry and decide which side is canonical.

---

**T-TC-02 [HIGH] — type_system.py:354-403** — `_DateCodec`, `_TimeCodec`, `_TimestampCodec` skip both encode-overflow pre-check AND decode-buffer-bounds check.

**Failure scenario A (encode):** A date far in the past has `(value - _EPOCH_DATE).days` exceeding int32 → `struct.error: 'i' format requires -2147483648 <= number <= 2147483647`. NOT `CodecError`, NOT `OverflowError`, NOT `ValueError`. The triple-inheritance `CodecError(TypeError, ValueError, OverflowError)` was meant to make `except OverflowError` blocks catch codec errors — they don't.

**Failure scenario B (decode):** `codec_for("DATE").decode_bytes(b"", 0)` → `struct.error: unpack_from requires a buffer of at least 4 bytes`. Other codecs raise `ValueError(f"{name} decode truncated at offset 0")` — DOC-CONTRACT VIOLATION.

**Fix:** Wrap `struct.error` in a `CodecError` boundary at the decode call site, OR add explicit bounds checks.

---

**T-TC-03 [HIGH] — type_system.py:302-308** — `_VarcharCodec.decode_bytes` and inherited `_CharCodec` skip `_check(max_len)`.

**Failure scenario:** Schema migration `ALTER TABLE t MODIFY col VARCHAR(10)` where an existing row stores a 200-byte string. On read, `decode_bytes` returns the 200-byte string into the VARCHAR(10) cell. Round-trip is broken post-migration.

**Note:** DV7 (the symmetric `encode_py` raising `TypeError` instead of `CodecError`) is documented "do not fix" by the 2026-07-21 cleanup. This finding is the asymmetric DECODE twin — encode has the check, decode doesn't.

**Fix:** Add `_check(len(text))` in `decode_bytes` (before the `bytes(text, "utf-8")` conversion).

---

**T-TC-04 [MED] — type_system.py:288-315** — `_VarcharCodec._check` counts UTF-8 BYTES, not characters.

**Failure scenario:** `CREATE TABLE t (s VARCHAR(3)); INSERT INTO t VALUES ('αβ')` — `'αβ'` is 2 chars but 4 bytes in UTF-8 → `CodecError("VARCHAR(3) length 4 exceeds max")`. Users expect char count.

**Note:** SQL standard and Postgres `character varying(N)` count code points. Project semantics differ.

**Fix:** Either document the deviation explicitly or implement char counting via `len(value.encode("utf-8"))` on the encode side AND add an inverse `len(value.encode("utf-8")) <= max_len` check on decode.

---

**T-TC-05 [MED] — type_system.py:407-415** — `SMALLINT`, `BIGINT`, `DOUBLE` codecs created by mutating class attributes after instantiation.

**Failure scenario:** Future code that re-instantiates `REGISTRY["INT"]` silently leaves the other width codecs pointing at stale class attrs.

**Fix:** Use proper class hierarchy / freeze each codec instance; document the mutability contract.

---

**T-TC-06 [MED] — type_system.py:46-51 vs 221-225** — `parse_text_literal(s)` (module-level) and `_TextCodec.parse_literal(text, params)` duplicate text-literal parsing logic (single-quote strip + `'' → '` decode).

**Failure scenario:** Future change to one (e.g., NULL byte handling) silently diverges.

**Fix:** Have `_TextCodec.parse_literal` call the module-level helper.

---

**T-TC-07 [MED] — type_system.py:21** — `CodecError` defined in `type_system.py` but logically a top-level exception. Should be re-exported from `tinydb.errors`.

**Cross-validated:** Doc-lens finding (mixed language also noted in F-09 / L-08).

---

### `src/tinydb/catalog.py`

**T-TC-08 [HIGH] — catalog.py:257-270** — `_pack_chain` defensively truncates payload to `CHAIN_BODY_SIZE`, masking an upstream bug in `_serialize_segments`.

**Verified** at `catalog.py:262`:
```python
body = seg[:CHAIN_BODY_SIZE]
```

**Failure scenario:** Single large table (200+ columns) → `_serialize_segments` returns the segment unsplit (`> 1` guard fails) → truncation drops trailing columns silently.

**Fix:** Remove the truncation AND make `_serialize_segments` actually split any segment > CHAIN_BODY_SIZE. The defensive truncation is the wrong layer of defense — it should be the `_serialize_segments` caller's responsibility.

---

**T-TC-09 [HIGH] — catalog.py:273-296** — `_unpack_chain` bounds the loop by `pager.page_count()`, but `page_count` reads the mmap file size at call time.

**Failure scenario:** Mid-write crash leaves page N pointing to page N+5 (allocated before crash). Recovery opens the file, `page_count()` returns N (file-size before reclaim), `_unpack_chain` raises `InvalidDatabaseFile("catalog chain exceeds page_count (N+1); loop?")` for a valid chain that exceeds page_count() at parse time but is recoverable.

**Fix:** Don't bound the loop by page_count; instead, bound by `(pid >= CHAIN_HEAD_PAGE and pid <= MAX_PAGE_ID)` and validate at each step.

---

**T-TC-10 [MED] — catalog.py:158** — `Catalog.to_bytes` raises `ValueError("catalog page overflow")` but `CatalogFull` already exists in `errors.py:79`.

**Cross-reference:** T-2026-07-28-09 (DatabaseLocked) and T-2026-07-28-01 — error hierarchy consistency issue.

---

**T-TC-11 [MED] — catalog.py:168 vs 180** — `create_table` raises `ValueError` for duplicate name; `drop_table` raises `KeyError` for missing. Both are name-policy violations; project prefers `ExecutionError` subclasses.

---

**T-TC-12 [MED] — catalog.py:294-296** — Last-writer-wins on duplicate table names across chain segments; torn-write silent overwrite.

---

### `src/tinydb/errors.py`

**T-TC-13 [HIGH] — errors.py:145-153** — `DatabaseLocked(TinydbError)` while all other user-facing errors subclass `ExecutionError`.

**Failure scenario:** REPL catches `ExecutionError` for user-friendly failures; `DatabaseLocked` slips through.

**Already in 2026-07-27 review T-03.** Re-flagged because it's the only exception hierarchy issue ALL three relevant agents (storage / codecs / database-core) agreed on.

**Fix:** `class DatabaseLocked(ExecutionError):` with CHANGELOG note.

---

### Cross-file

**T-TC-14 [MED] — repl.py:149, _repl_meta.py:23, _repl_format.py:15** — `VALID_OUTPUT_FORMATS = ("table", "csv", "json")` defined 3×.

(Also in REPL review as F-06 / L-M-10.)

**T-TC-15 [MED] — docstring language inconsistency** — `errors.py:85,97,107,117,127,135,148` mix Chinese (from join-query T3, concurrency-control T1) with English (storage layer). `catalog.py` is fully English; `_repl_format.py` is fully Chinese; `_repl_meta.py` is mixed.

---

## Summary

| Severity | Count |
|---|---|
| HIGH | 6 |
| MED | 9 |
| LOW | 0 |

**Highest-impact:** T-TC-08 (catalog silent truncation), T-TC-13 (DatabaseLocked hierarchy), T-TC-03 (VARCHAR decode asymmetry).
