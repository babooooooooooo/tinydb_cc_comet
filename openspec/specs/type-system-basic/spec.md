# type-system-basic Specification

## Purpose
TBD - created by archiving change tinydb-mvp. Update Purpose after archive.
## Requirements
### Requirement: Type literals parseable from SQL text

The system SHALL parse the four base type literals from SQL text strings without ambiguity: signed integers, decimal floats, single-quoted text, and boolean keywords.

#### Scenario: Parse positive integer literal
- **WHEN** parsing the SQL text `42`
- **THEN** the tokenizer SHALL produce one INTEGER token with value `42`

#### Scenario: Parse negative integer literal
- **WHEN** parsing the SQL text `-7`
- **THEN** the tokenizer SHALL produce one INTEGER token with value `-7`

#### Scenario: Parse decimal float literal
- **WHEN** parsing the SQL text `3.14`
- **THEN** the tokenizer SHALL produce one FLOAT token with value `3.14`

#### Scenario: Parse text literal
- **WHEN** parsing the SQL text `'hello world'`
- **THEN** the tokenizer SHALL produce one TEXT token with value `hello world`

#### Scenario: Parse boolean literal TRUE
- **WHEN** parsing the SQL text `TRUE` (case-insensitive)
- **THEN** the tokenizer SHALL produce one BOOL token with value `true`

#### Scenario: Parse boolean literal FALSE
- **WHEN** parsing the SQL text `false`
- **THEN** the tokenizer SHALL produce one BOOL token with value `false`

#### Scenario: Reject NaN in float literal
- **WHEN** parsing the SQL text `NaN`
- **THEN** the tokenizer SHALL raise `ValueError` with message containing `"NaN not allowed"`

#### Scenario: Reject Infinity in float literal
- **WHEN** parsing the SQL text `Infinity` or `inf`
- **THEN** the tokenizer SHALL raise `ValueError`

### Requirement: Type encoding to binary buffer

The system SHALL encode typed values into a stable binary format suitable for slotted-page storage. Each type SHALL have a deterministic byte-level encoding.

#### Scenario: INT encodes as 8-byte signed big-endian
- **WHEN** encoding the integer `42`
- **THEN** the bytes MUST equal `b'\x00\x00\x00\x00\x00\x00\x00\x2a'`

#### Scenario: INT encoding rejects out-of-range value
- **WHEN** encoding the integer `2**63`
- **THEN** the encoder SHALL raise `OverflowError`

#### Scenario: TEXT encodes length-prefixed UTF-8
- **WHEN** encoding the text `alice`
- **THEN** the bytes MUST equal `b'\x00\x05alice'`

#### Scenario: TEXT encoding rejects non-UTF-8
- **WHEN** attempting to encode a Python string with invalid surrogate
- **THEN** the encoder SHALL raise `UnicodeEncodeError`

#### Scenario: BOOL encodes as single byte
- **WHEN** encoding the boolean `True`
- **THEN** the bytes MUST equal `b'\x01'`
- **WHEN** encoding the boolean `False`
- **THEN** the bytes MUST equal `b'\x00'`

#### Scenario: FLOAT encodes as 8-byte big-endian IEEE 754
- **WHEN** encoding the float `3.14`
- **THEN** the bytes MUST equal `struct.pack('>d', 3.14)`

### Requirement: Type decoding from binary buffer

The system SHALL decode binary buffers back to typed Python values, round-tripping with encoding for all valid inputs.

#### Scenario: Decode INT roundtrips
- **WHEN** decoding `b'\x00\x00\x00\x00\x00\x00\x00\x2a'` as INT
- **THEN** the value MUST equal `42`

#### Scenario: Decode TEXT roundtrips
- **WHEN** decoding `b'\x00\x05alice'` as TEXT
- **THEN** the value MUST equal `'alice'`

#### Scenario: Decode BOOL roundtrips
- **WHEN** decoding `b'\x01'` as BOOL
- **THEN** the value MUST equal `True`

#### Scenario: Decode rejects truncated buffer
- **WHEN** decoding a buffer that is shorter than the type's expected length
- **THEN** the decoder SHALL raise `ValueError`

### Requirement: Strict type coercion rejection

The system SHALL NOT perform implicit type conversion. Operations between mismatched types SHALL raise a `TypeError`.

#### Scenario: Compare INT with TEXT literal rejected
- **WHEN** executing `SELECT * FROM t WHERE int_col = '5'`
- **THEN** the executor SHALL raise `TypeError` with message containing `"type mismatch: INT vs TEXT"`

#### Scenario: Insert TEXT into INT column rejected
- **WHEN** executing `INSERT INTO t(int_col) VALUES ('hello')`
- **THEN** the executor SHALL raise `TypeError`

#### Scenario: Insert INT into TEXT column rejected
- **WHEN** executing `INSERT INTO t(text_col) VALUES (5)`
- **THEN** the executor SHALL raise `TypeError`

### Requirement: Python to DB and DB to Python conversion

The system SHALL provide explicit conversion between Python native objects and DB-typed values via the codec registry. The legacy `py_to_db`/`db_to_py` module-level helpers are removed; canonical entry points are `codec_for(type, params).encode_py(value)` for Python → DB bytes and `codec_for(type, params).decode_bytes(buf, offset)` for DB bytes → Python.

#### Scenario: Convert Python int to INT via codec registry
- **WHEN** converting Python `42` to DB type for an INT column via `codec_for("INT").encode_py(42)`
- **THEN** the function SHALL return bytes `b'\x00\x00\x00\x2a'` (8-byte big-endian)

#### Scenario: Convert Python str to TEXT via codec registry
- **WHEN** converting Python `'alice'` to DB type for a TEXT column via `codec_for("TEXT").encode_py('alice')`
- **THEN** the function SHALL return bytes `b'\x00\x05alice'` (length-prefixed UTF-8)

#### Scenario: Convert Python float to FLOAT via codec registry
- **WHEN** converting Python `2.5` to DB type for a FLOAT column via `codec_for("FLOAT").encode_py(2.5)`
- **THEN** the function SHALL return bytes `struct.pack('>f', 2.5)`

#### Scenario: Convert Python float NaN rejected via codec registry
- **WHEN** converting Python `float('nan')` to DB type for a FLOAT column via `codec_for("FLOAT").encode_py(float('nan'))`
- **THEN** the function SHALL raise `CodecError` with message containing `"NaN not allowed"`

#### Scenario: Convert Python bool to BOOL via codec registry
- **WHEN** converting Python `True` to DB type for a BOOL column via `codec_for("BOOL").encode_py(True)`
- **THEN** the function SHALL return `b'\x01'`

#### Scenario: Convert Python float to INT rejected via codec registry
- **WHEN** converting Python `2.5` to DB type for an INT column via `codec_for("INT").encode_py(2.5)`
- **THEN** the function SHALL raise `CodecError` with message indicating type mismatch

#### Scenario: Parametric type (VARCHAR) conversion via codec registry
- **WHEN** converting Python `'hello'` (5 chars) to DB type for a `VARCHAR(10)` column via `codec_for("VARCHAR", (10,)).encode_py('hello')`
- **THEN** the function SHALL return 2-byte length prefix `b'\x00\x05'` followed by UTF-8 bytes `b'hello'`

#### Scenario: Parametric type VARCHAR length exceeds limit rejected
- **WHEN** converting Python `'x' * 20` to DB type for a `VARCHAR(10)` column via `codec_for("VARCHAR", (10,)).encode_py('x' * 20)`
- **THEN** the function SHALL raise `CodecError` with message containing `"length"` and `"exceeds"`

#### Scenario: Legacy py_to_db helper removed from public API
- **WHEN** any module attempts to import `py_to_db` from `tinydb.type_system`
- **THEN** the import SHALL raise `ImportError` (function no longer exported)

#### Scenario: Legacy db_to_py helper removed from public API
- **WHEN** any module attempts to import `db_to_py` from `tinydb.type_system`
- **THEN** the import SHALL raise `ImportError` (function no longer exported)

#### Scenario: Legacy validate_compare helper removed from public API
- **WHEN** any module attempts to import `validate_compare` from `tinydb.type_system`
- **THEN** the import SHALL raise `ImportError` (function no longer exported; the modern API `validate_compare_types` is the canonical entry)

