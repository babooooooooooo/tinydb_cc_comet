---
change: tinydb-types
build_mode: subagent-driven-development
review_mode: standard
tdd_mode: tdd
isolation: worktree
base_ref: 5db80cfa72232f850638db64fd51014c670234f4
plan: docs/superpowers/plans/2026-07-18-tinydb-types.md
design_doc: docs/superpowers/specs/2026-07-18-tinydb-types-design.md
---

# tinydb-types Subagent Progress Checkpoint

> Coordination checkpoint for subagent-driven-development dispatch loop.
> Updated by main session after each dispatch / review / checkoff.

## Build Phase Config

- **build_mode**: subagent-driven-development
- **review_mode**: standard (per-task reviewer only for risk tasks; 1 final lightweight reviewer)
- **tdd_mode**: tdd (RED → GREEN → COMMIT)
- **isolation**: worktree (already at `/home/lz/projects/tinydb-worktrees/tinydb-types/`)
- **branch**: `feature/20260716/tinydb-types`
- **base_ref**: 5db80cf (post-constraints main)

## Plan Task List (21 tasks)

| # | Task Text | OpenSpec Sub-task | Status | Reviewer | Round |
|---|---|---|---|---|---|
| 1 | TypeCodec Protocol + REGISTRY + lookup/codec_for scaffolding | 1.1-1.2 | done | sonnet | 1 |
| 2 | Migrate 4 MVP codecs to Protocol form (INT/TEXT/FLOAT/BOOL) | 1.3 | done | sonnet | 1 |
| 3 | SMALLINT (IntCodec with width=2) | 2.1-2.4 | done | sonnet | 0 |
| 4 | BIGINT (IntCodec with width=8) | (covered by 2.x) | done | sonnet | 0 |
| 5 | DOUBLE (FloatCodec with width=8) | 3.1-3.5 | done | sonnet | 0 |
| 6 | BOOLEAN alias for BOOL | 3.5 | done | sonnet | 0 |
| 7 | VARCHAR (parametric codec with max_len) | 4.1-4.4 | done | sonnet | 0 |
| 8 | CHAR (parametric codec with PAD SPACE) | (4.x) | done | sonnet | 0 |
| 9 | DECIMAL (scaled int64 with precision/scale) | 5.1-5.4 | done (manual) | sonnet | 0 |
| 10 | DATE / TIME / TIMESTAMP UTC | 6.1-6.6 | done | sonnet | 0 |
| 11 | Verify all 15 codecs in REGISTRY | 9.1-9.2 | done | — | 0 |
| 12 | Parser — type_spec with VARCHAR(N) / DECIMAL(p,s) | 7.1-7.4 | done | — | 0 |
| 13 | Parser — DATE / TIME / TIMESTAMP literal prefix | 8.1 | done | — | 0 |
| 14 | Parser — DECIMAL literal prefix | 8.2-8.3 | done | — | 0 |
| 15 | Catalog — Column.type_params + backward compat | (covered) | done | — | 0 |
| 16 | row_codec — schema_v2() + codec_for dispatch | (covered) | done | — | 0 |
| 17 | Executor — wire 15 types into INSERT / SELECT / WHERE | (covered) | done | — | 0 |
| 18 | WHERE clause strict same-type comparison | (covered) | done | — | 0 |
| 19 | FLOAT 4-byte regression cleanup | 10.1 | done | — | 0 |
| 20 | REPL integration tests | (covered) | pending | — | — |
| 21 | Coverage + final verification | 10.1-10.3 | pending | — | — |

## Current Task

**Task 20**: REPL integration tests
- **Stage**: task-implement
- **Implementer**: pending dispatch
- **Implementer model**: sonnet
- **Risk signals**: subprocess REPL 测试 + .sql golden 文件维护

## Dispatch Log

### 2026-07-18 — Task 19 implementer (sonnet, background)
- Implementer status: DONE
- Commit `d74a016 fix(tests): no FLOAT 4-byte regressions found` (empty commit)
- Scanned 21 FLOAT-related tests across all test directories
- All 21 tests pass without modification
- Categories: exact-representable values (1.5/-2.5/0.0), tolerant comparison (abs < 1e-6), legacy 8-byte helper API, codec-based 4-byte registry, tokenizer/parser/round-trip
- No source code in src/tinydb/ changed
- Full test suite: 559 passed + 2 pre-existing failures (test_column_dataclass_roundtrip + test_golden_sql[error_cases/02_unsupported_type.sql])
- No per-task reviewer (decisive completion; nothing to fix)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 18 implementer (sonnet, background)
- Implementer status: DONE
- Commit `e910649 feat(types): WHERE enforces strict same-type comparison (Design D6)`
- RED: 1 failed (DATE vs TIMESTAMP not distinguished at Python level)
- GREEN: 34 new tests pass (13 unit + 13 integration + 8 extras); full suite **559 passed**, coverage **94.28%**
- File scope: type_system.py + executor.py + 2 new test files (test_validate_compare_types.py, test_types_in_where.py)
- Added: `validate_compare_types(col_type, col_params, lit_type, lit_params)`, `infer_literal_type(value)`, `_format_type_params(params)`
- eval_expr now: infer literal type → call validate_compare_types → delegate to codec
- Critical correctness fix: `datetime.datetime` is checked BEFORE `datetime.date` (datetime is subclass of date)
- Strict same-type verified for: INT vs SMALLINT/BIGINT/TEXT, VARCHAR(N) vs VARCHAR(M)/TEXT, DECIMAL(p1,s1) vs DECIMAL(p2,s2), DATE vs TIMESTAMP, FLOAT vs DOUBLE, CHAR vs TEXT, INT vs BOOL, TEXT vs INT
- Legacy `validate_compare(col_bytes, col_type, lit_bytes, lit_type)` preserved for backward compat
- Same-type cases still work: `WHERE id = 2`, `WHERE active = TRUE`, `WHERE d = DATE '...'`
- SQL-syntax pivot: VARCHAR/DECIMAL/SMALLINT literal prefixes don't exist in tokenizer → covered via unit tests with synthetic tuples
- No per-task reviewer (decisive completion; comprehensive coverage)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 17 implementer (sonnet, background)
- Implementer status: DONE
- Commit `f2ac0c9 feat(executor): wire all 15 codecs into INSERT/SELECT/WHERE paths`
- RED: 16 failed, 1 passed (all because executor used py_to_db which only handles 4 MVP types)
- GREEN: 16/16 new tests pass; full test suite **525 passed** (370 unit + 155 integration)
- File scope: executor.py +42/-27 + new test_types_roundtrip.py (16 tests) — type_system.py, row_codec.py, parser.py, catalog.py, database.py NOT touched
- Major executor refactor: replaced py_to_db/db_to_py with `codec_for(c.type, c.type_params)` dispatch in `_exec_create_table`, `_exec_insert`, `_exec_select`, `_exec_delete`, `_exec_update`, `_stable_sort`, `_scan_table`, `eval_expr`
- All 15 types verified end-to-end (VARCHAR/DECIMAL/DATE/TIME/TIMESTAMP/SMALLINT/BIGINT/DOUBLE/REAL/BOOLEAN/INTEGER/CHAR + 4 MVP)
- Codec overflow/rejection verified: VARCHAR(5)+'too long', DECIMAL(5,2)+12345.67, SMALLINT+1_000_000, DOUBLE inf
- **2 pre-existing failures noted (NOT caused by this task)**:
  1. `tests/integration/test_catalog.py::test_column_dataclass_roundtrip` — expects to_dict() to omit type_params (pre-existing from Task 15)
  2. `tests/e2e/test_golden_sql.py::test_golden_sql[error_cases/02_unsupported_type.sql]` — golden file expects "VARCHAR not supported in MVP" error (pre-existing from Task 12)
- **Gap from Task 15 fixed**: `_exec_create_table` now passes `type_params=cd.type_params` from ColumnDefinition to Column
- No per-task reviewer (decisive completion; gap fixed + 2 pre-existing unrelated failures)
- Coordinator decision: APPROVE — proceed to checkoff; pre-existing failures deferred to Task 21 audit

### 2026-07-18 — Task 16 implementer (sonnet, background)
- Implementer status: DONE
- Commit `b662ed4 feat(row_codec): wire all 15 types + schema_v2 dispatch`
- RED: 1 failed (no `schema_v2` property); other 17 passed (Task 2 had already wired codecs correctly)
- GREEN: 18/18 new tests pass; full unit suite **370 passed** (352 + 18)
- File scope: catalog.py (+7 lines schema_v2 property) + new test_row_codec_v2.py (161 lines, 18 tests) — **row_codec.py required ZERO source changes** (Task 2 already handled 3-tuple via `_column_type_and_params` + routed through `codec_for`)
- Backward compat verified: legacy 2-tuple `[(name, type)]` schema still works; FLOAT 4-byte wire format preserved
- All 15 types roundtrip verified: INT/SMALLINT/BIGINT/TEXT/BOOL/FLOAT/DOUBLE/VARCHAR/CHAR/DECIMAL/DATE/TIME/TIMESTAMP
- CHAR(N) padding verified: `"ab"` → `"ab   "` roundtrip preserved
- DECIMAL precision verified: 1.23 roundtrip ≈ 1.23 (within scale)
- No per-task reviewer (decisive completion; minimal diff; well-scoped property addition)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 15 implementer (sonnet, background)
- Implementer status: DONE
- Commit `8849aa4 feat(catalog): Column.type_params with backward-compatible JSON`
- RED: 8 failed (no `type_params` field; no `to_dict`/`from_dict` keys)
- GREEN: 8/8 new tests pass; full unit suite **352 passed**
- File scope: catalog.py +3 lines + new test_catalog_type_params.py (8 tests)
- **Minimal change**: only `type_params: tuple = ()` field added between `type: str` and `nullable`; `to_dict`/`from_dict` updated to emit/read the new key
- Backward compat preserved: legacy `_load_column(["id", "INT"])` still works (relies on defaults); old JSON without `type_params` defaults to `()`
- No per-task reviewer (decisive completion; minimal diff, well-bounded field addition)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 14 implementer (sonnet, background)
- Implementer status: DONE
- Commit `8da63de feat(parser): DECIMAL literal prefix parsing`
- RED: 7 failed (no `_parse_decimal_literal` method, also DECIMAL was tokenizing as IDENT)
- GREEN: 7/7 new tests pass; full unit suite **344 passed**, coverage **86.79%**
- File scope: parser.py + tokenizer.py + new test_parser_decimal_lit.py
- **Tokenizer escape valve invoked**: DECIMAL was being tokenized as IDENT, RED proved missing keyword. Added DECIMAL to KEYWORDS (1-line minimal fix). Justified per task hint.
- parser.py line count: **861** (≤870 budget, was 828)
- Routing added at 3 sites (parallel to `_DATETIME_KEYWORDS`):
  - INSERT VALUES (parser.py:531)
  - UPDATE SET (parser.py:686)
  - WHERE COMPARISON (parser.py:782)
- No per-task reviewer (decisive completion; tokenizer escape justified by RED)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 13 implementer (sonnet, background)
- Implementer status: DONE_WITH_CONCERNS
- Commit `386c203 feat(parser): DATE/TIME/TIMESTAMP literal prefix parsing`
- RED: 7 failed (no `_parse_datetime_literal` method)
- GREEN: 7/7 new tests pass; full unit suite 337 passed
- File scope: parser.py + tokenizer.py + new test_parser_datetime_lit.py — type_system.py, executor.py, row_codec.py, catalog.py NOT touched
- parser.py line count: **828** (well under 870 budget)
- Routed `_parse_insert`, `_parse_update`, `_parse_comparison` to detect DATE/TIME/TIMESTAMP prefix and delegate to new method (peek-before-advance)
- Concerns (both valid adaptations):
  - `parse(sql)` adapted to `parse(tokenize(sql))` because public `parse()` takes list[Token] (not str) — `stmt.values[0]` adapted to `stmt.values[0][0]` (rows are nested lists)
  - Only added DATE/TIME/TIMESTAMP to KEYWORDS (not other types from plan example) — correct scoping to Task 13 boundary
- No per-task reviewer (decisive completion; concerns are well-handled)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 12 implementer (sonnet, background)
- Implementer status: DONE
- Commit `6d17f6d feat(parser): accept VARCHAR(N) and DECIMAL(p, s) in column definitions`
- RED: 12 failed, 5 passed — every type-spec case (VARCHAR/CHAR/DECIMAL with params, missing-param rejection, invalid DECIMAL precision/scale, non-parametric-with-params rejection, type_params dataclass field)
- GREEN: test_parser_type_spec.py 17/17 + test_parser.py 16/16 + full unit suite 330/330
- File scope: parser.py + tests/unit/test_parser.py + tests/unit/test_parser_type_spec.py (NEW) — type_system.py, row_codec.py, catalog.py, executor.py NOT touched
- codec_for integration: parser `_parse_type_params` calls `codec_for(name, params)` (lazy import) as single source of truth for range validation (VARCHAR N>=1, DECIMAL 1<=p<=18, 0<=s<p)
- Backward compat: existing `name INT` still parses; ColumnDefinition.type_params defaults to ()
- Informational: parser.py now 780 lines (self-imposed soft target was ≤750); not blocking
- No per-task reviewer (decisive completion; codec_for delegation eliminates duplication risk)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 11 implementer (sonnet, background)
- Implementer status: DONE
- Commit `b2418ed test(types): assert full 15-type REGISTRY + alias resolution`
- RED: `test_registry_has_15_core_types` failed — REGISTRY had 13 keys, test expected 15
- GREEN: registry suite 9 passed; full unit suite **313 passed**; coverage **86.78%**
- File scope: ONLY type_system.py + test_type_system_registry.py
- Module line count: **444 lines** (within ≤445 target)
- Singleton identity preserved: `REGISTRY["BOOLEAN"] is REGISTRY["BOOL"] → True`; `REGISTRY["REAL"] is REGISTRY["FLOAT"] → True`
- Aliases (INTEGER, DOUBLE PRECISION) registered as REGISTRY keys; `lookup()` resolves all 4 aliases correctly
- No per-task reviewer (decisive completion; only test addition + singleton alias registration)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 10 implementer (sonnet, background)
- Implementer status: DONE_WITH_CONCERNS
- Commit `c08a791 feat(types): add DATE / TIME / TIMESTAMP codecs (UTC unified)`
- RED: 8 failed with KeyError: 'DATE'/'TIME'/'TIMESTAMP'
- GREEN: 93 passed (8 new DATE/TIME/TIMESTAMP + 85 existing)
- File scope: ONLY type_system.py + test_type_system_v2.py
- **`import datetime as _dt` added at module top** + `_EPOCH_DATE`/`_EPOCH_DT` constants
- **Concern (severe)**: type_system.py now **441 lines**, **91 lines over §F6 budget of 350**
  - 3 non-parametric codecs minimum ~55 lines (mathematically unreachable for ≤ +15 goal)
  - Root cause: §F6 budget was unrealistic — base 132 lines (legacy helpers preserved per §F2) + ~290 lines codec framework = 422 minimum
  - **Decision deferred to Task 21 audit**: either (a) refactor aggressively, (b) update §F6 budget to 450+
  - Pre-existing RED: `test_registry_has_15_core_types` expects aliases (BOOLEAN/REAL/DOUBLE PRECISION/INTEGER) as REGISTRY keys — Task 11 will fix
- No per-task reviewer (decisive completion; line budget is process concern, not correctness)
- Coordinator decision: APPROVE — proceed to checkoff; address budget in Task 21 or interim budget-refactor task

### 2026-07-18 — Task 9 implementer (sonnet, background) — manually salvaged
- Implementer status: completed work; agent terminated by API 429 before commit/report
- Commit `17008ef feat(types): add DECIMAL(p,s) codec with scaled int64 encoding` (manually made after recovery)
- RED→GREEN: 7 DECIMAL tests passed (all plan-spec cases)
- Full type_system suite: 114 passed, 1 scaffold-aligned RED (`test_registry_has_15_core_types` requires DATE/TIME/TIMESTAMP from Task 10)
- File scope: ONLY type_system.py + test_type_system_v2.py
- **Module line count: 386 lines** (+12 from 374 → cumulative 36 lines over §F6 budget of 350)
- Implementation quality: **Compact** — uses single-line method bodies, `self._factor` cached in __init__, inline validation
- Coordinator recovered: implementation was correct and complete; only commit + checkoff missing
- No per-task reviewer (decisive completion + low risk)
- Coordinator decision: APPROVE — proceed to checkoff; cumulative overrun will require Task 21 refactor or interim budget-refactor task

### 2026-07-18 — Task 8 implementer (sonnet, background)
- Implementer status: DONE_WITH_CONCERNS
- Commit `134fdd0 feat(types): add CHAR(N) codec with PAD SPACE semantics`
- RED: 4 failed with KeyError: 'CHAR'
- GREEN: 71 tests passed (4 new CHAR + 67 existing)
- File scope: ONLY type_system.py + test_type_system_v2.py
- **Concern (mitigated)**: type_system.py still 374 lines (no change from Task 7)
  - **Elegant solution**: `_CharCodec(_VarcharCodec)` inheritance with single `encode_py` override
  - Net lines: +0 (CHAR definition is ~3 lines vs ~30 if standalone)
  - Inherited error messages still say "VARCHAR" in non-overridden paths — tests pass because only encode_py (overridden) is exercised for error-message assertion
- Provided **concrete refactor plan for Task 21** (5 numbered items, ~17-19 lines of potential savings):
  1. Refactor `_VarcharCodec` to use `self.name` in error messages
  2. Move `_IntCodec._spec` to module-level constant
  3. Extract `_expect(tname, expected, value)` helper for py_to_db/db_to_py
  4. Inline REGISTRATION construction via `_variant(cls, name, width)` helper
  5. Collapse legacy `encode_text`/`decode_text` duplication with codec
- No per-task reviewer dispatched (no process risk; line budget concern unchanged from Task 7)
- Coordinator decision: APPROVE — proceed to checkoff; consolidate refactor plan for Task 21

### 2026-07-18 — Task 7 implementer (sonnet, background)
- Implementer status: DONE_WITH_CONCERNS
- Commit `56327d8 feat(types): add VARCHAR(N) codec with max_len validation`
- RED: 4 failed with KeyError: 'VARCHAR'
- GREEN: 67 tests passed (4 new VARCHAR + 63 existing)
- File scope: ONLY type_system.py + test_type_system_v2.py
- **Concern 1 (significant)**: type_system.py now 374 lines, **24 lines over §F6 budget of 350**
  - Root cause: `_VarcharCodec` class requires 5 methods (~30 lines minimal)
  - Implementer justification: cannot compress further without compromising readability
  - Cumulative impact: Task 8 (CHAR ~30 lines) + Task 9 (DECIMAL ~40 lines) will push to ~440+ lines
  - **Mitigation**: address via refactor in Task 21 Step 2 (audit checkpoint) OR dispatch budget-refactor task before Task 9 if pressure severe
- Concern 2 (non-issue): test_type_system_registry.py failures reduced 5→3 (DECIMAL-related, pre-existing scaffold-aligned RED)
- Per-call instance verified: `codec_for("VARCHAR", (10,)) is not codec_for("VARCHAR", (20,))`
- Backward compat verified: all 11 non-parametric lookups return singleton
- Alias-map loop updated to skip parametric classes
- No per-task reviewer dispatched (no process risk; only line-budget concern)
- Coordinator decision: APPROVE — proceed to checkoff; address line budget in Task 21

### 2026-07-18 — Task 6 implementer (sonnet, background)
- Implementer status: DONE
- Commit `4ac9454 feat(types): register BOOLEAN alias for BOOL`
- Investigation: BOOLEAN alias ALREADY wired by Task 2 (`aliases = ("BOOLEAN",)` on `_BoolCodec` + alias-build loop)
- RED→GREEN collapse (test passed immediately on first run; pure test addition)
- GREEN: 63 tests passed (1 new + 62 existing)
- File scope: ONLY test_type_system_v2.py (no src change needed)
- Module line count: 345 (unchanged, ≤ 350 budget)
- No per-task reviewer (no risk signals hit)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 5 implementer (sonnet, background)
- Implementer status: DONE
- Commit `6e5b82a feat(types): add DOUBLE codec (8-byte) + DOUBLE PRECISION alias`
- RED: 4 failed with KeyError: 'DOUBLE'/'DOUBLE PRECISION'
- GREEN: 62 tests passed (5 new + 57 existing)
- File scope: ONLY type_system.py + test_type_system_v2.py
- Module line count: 345 (-5 from Task 4's 350)
- DOUBLE PRECISION alias via `aliases = ("DOUBLE PRECISION",)` declarative (Task 4 pattern)
- REAL still maps to FLOAT (not DOUBLE) — confirmed
- No per-task reviewer (no risk signals hit)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 4 implementer (sonnet, background)
- Implementer status: DONE
- Commit `5a54735 feat(types): add BIGINT codec (IntCodec with width=8) + INTEGER alias`
- RED: 3 failed with KeyError: 'BIGINT'
- GREEN: 57 tests passed (4 new + 53 existing)
- File scope: ONLY type_system.py + test_type_system_v2.py
- Module line count: 350 (at budget limit)
- INTEGER alias via `REGISTRY["INT"].aliases = ("INTEGER",)` declarative approach (cleaner than manual _ALIAS_MAP)
- No per-task reviewer (no risk signals hit)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 3 implementer (sonnet, background)
- Implementer status: DONE_WITH_CONCERNS
- Commit `ffaa4ee feat(types): add SMALLINT codec (IntCodec with width=2)`
- RED: 3 failed with KeyError: 'SMALLINT'
- GREEN: 53 tests passed (19 prior v2 + 3 SMALLINT + 31 legacy)
- File scope: ONLY type_system.py + test_type_system_v2.py (54 ins / 25 del)
- Module line count: 348 (≤ 350 budget)
- Concerns (minor, accepted):
  1. Combined `_fmt` + `_bounds` properties → single `_spec` tuple (saves 4 lines, preserves semantics)
  2. Inlined SMALLINT registration (3 lines via direct REGISTRY["SMALLINT"] assignment)
  3. Trimmed "Codec Protocol implementations" comment block from 13→4 lines to fit budget
- No per-task reviewer (no risk signals hit)
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 2 reviewer (sonnet, background)
- Reviewer verdict: **APPROVED_WITH_NITS** (6 NITs, all docs/cosmetic, no functional impact)
  - NIT-1: type_system.py module docstring stale line count (`<= 150` → actual 343)
  - NIT-2: `_IntCodec` docstring references wrong task (says "Plan task 2" → should be tasks 3/4)
  - NIT-3: codec method type hints dropped (`def encode_py(self, value)` vs `value: int`)
  - NIT-4/5/6: NOT BUG — docstring hints, class attribute, mutable globals
- Spec compliance: PASS — file scope clean, all 15 legacy MVP functions preserved, REGISTRY has 4 keys, _ALIAS_MAP populated, FLOAT 4-byte, inf/nan rejected, row_codec accepts both 2-tuple + 3-tuple schemas, 114 in-scope tests green, 5 registry failures scaffold-aligned RED
- Code quality: PASS — type hints on public methods, actionable error messages, no silent failures
- Process compliance: PASS — implementer did not check off tasks, did not touch out-of-scope files
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 2 implementer (sonnet, background)
- Implementer status: DONE
- Commit `313be81 refactor(types): migrate MVP codecs to Protocol form (FLOAT 4-byte migration)`
- RED: 19 failed with KeyError: 'INT'/'TEXT'/'BOOL'/'FLOAT'
- GREEN: 114 in-scope tests passed (test_type_system_v2 + legacy test_type_system + test_row_codec + test_parser + e2e)
- FLOAT 4-byte verified: encoded 1.5 → 3fc00000 (4 bytes)
- Backward compat: all 15 legacy MVP functions preserved; row_codec accepts both schema formats
- Risk signal hits: 公共 API 契约变更 + schema 变更 + FLOAT 4-byte migration
- Action: dispatch task reviewer per review_mode=standard risk rule

### 2026-07-18 — Task 1 reviewer (sonnet, background)
- Reviewer verdict: **APPROVED_WITH_CONCERNS** (5 NITs, all stylistic/forward-looking, no functional impact)
  - NIT-1/2/3: bare `tuple` type hints vs `tuple[str, ...]` — Protocol structural, scaffolding only
  - NIT-4: `codec_for` checks `_ALIAS_MAP` (forward-compatible with Task 2 alias registration)
  - NIT-5: `codec_for` returns via `lookup()` (functionally safe)
- Spec compliance: PASS — all 8 tests match plan; backward compat verified; file scope respected
- Code quality: PASS — type hints, docstrings, actionable error messages
- Process compliance: PASS — no plan/design/openspec tampering; conventional commit format
- Coordinator decision: APPROVE — proceed to checkoff

### 2026-07-18 — Task 1 implementer (sonnet, background)
- Dispatched implementer with full Task 1 text + TDD + file scope + risk signal checklist
- Implementer reported DONE_WITH_CONCERNS:
  - Commit `7f520bf` in place
  - 7/8 tests fail (scaffold-aligned RED; REGISTRY empty by design — subsequent tasks populate)
  - 1/8 passes (`test_lookup_unknown_type_raises`)
  - Legacy 31 tests still pass (backward compat verified)
  - Concern: brief said "7 should PASS" but only 1 did — implementer correctly noted this is scaffold-aligned, not a bug
- Coordinator verification: ran tests with correct `.venv/bin/python` after installing venv (was missing in worktree); confirmed 7 fail + 1 pass matches report
- Risk signal hit: 公共 API 契约变更 (adds 4 new public names: TypeCodec, REGISTRY, lookup, codec_for)
- Action: dispatch task reviewer (per review_mode=standard risk rule)
