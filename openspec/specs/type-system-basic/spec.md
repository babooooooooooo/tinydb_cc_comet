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

The system SHALL provide explicit conversion functions between Python native objects and DB-typed values for boundary use (API layer, INSERT parameters, SELECT result rows).

#### Scenario: Convert Python int to INT
- **WHEN** converting Python `42` to DB type for an INT column
- **THEN** the function SHALL return encoded bytes for `42`

#### Scenario: Convert Python str to TEXT
- **WHEN** converting Python `'alice'` to DB type for a TEXT column
- **THEN** the function SHALL return encoded bytes for `'alice'`

#### Scenario: Convert Python float to FLOAT
- **WHEN** converting Python `2.5` to DB type for a FLOAT column
- **THEN** the function SHALL return encoded bytes for `2.5`

#### Scenario: Convert Python float NaN rejected
- **WHEN** converting Python `float('nan')` to DB type for a FLOAT column
- **THEN** the function SHALL raise `ValueError`

#### Scenario: Convert Python bool to BOOL
- **WHEN** converting Python `True` to DB type for a BOOL column
- **THEN** the function SHALL return `b'\x01'`

#### Scenario: Convert Python float to INT rejected
- **WHEN** attempting to convert Python `2.5` to DB type for an INT column
- **THEN** the function SHALL raise `TypeError`

