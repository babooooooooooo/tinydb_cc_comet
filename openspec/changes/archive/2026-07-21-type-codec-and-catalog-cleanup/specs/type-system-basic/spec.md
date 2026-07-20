# type-system-basic delta

## MODIFIED Requirements

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