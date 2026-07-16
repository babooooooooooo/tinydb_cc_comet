# Spec: python-api

> 范围：MVP 阶段的 `tinydb` 顶层包 + `Database` 类 + `Row` 数据类。后续 `tinydb-engine-v2` 可能扩展更多方法（事务上下文、`executemany` 等），但不应破坏 MVP 阶段的 API 形状。

## ADDED Requirements

### Requirement: Top-level package `tinydb` importable

The system SHALL expose a top-level Python package named `tinydb`, importable with `import tinydb`. The package MUST expose `Database` and `Row` as public names.

#### Scenario: Import Database and Row
- **WHEN** executing `import tinydb; tinydb.Database; tinydb.Row`
- **THEN** both names MUST be available without `__import__` workaround

#### Scenario: Package has `__version__`
- **WHEN** accessing `tinydb.__version__`
- **THEN** the value MUST be a string matching the format `"X.Y.Z"`; for MVP the value MUST be `"0.1.0"`

### Requirement: Database class supports file-backed and in-memory modes

`Database` SHALL accept a path argument that is either a filesystem path (file-backed) or the literal string `":memory:"` (in-memory).

#### Scenario: Open file-backed database
- **WHEN** constructing `Database('/tmp/foo.db')`
- **THEN** the system MUST create the file (if missing) or open it (if existing)
- **AND** persist data across `Database` instances across the same path

#### Scenario: Open in-memory database
- **WHEN** constructing `Database(':memory:')`
- **THEN** the system MUST NOT create any filesystem entry
- **AND** data MUST be lost when the `Database` object is garbage-collected

#### Scenario: Context manager closes the database
- **WHEN** using `Database(path)` as a context manager (`with` statement)
- **THEN** on `__exit__` the system MUST flush any pending writes and release file handles

### Requirement: execute method runs SQL statements

`Database.execute(sql)` SHALL parse the supplied SQL string, execute the resulting AST, and return a result value (defined per statement type).

#### Scenario: SELECT returns list of Row
- **WHEN** executing `SELECT * FROM users`
- **THEN** the return value MUST be a `list[Row]`

#### Scenario: DDL returns empty list
- **WHEN** executing `CREATE TABLE t(id INT)`
- **THEN** the return value MUST be `[]`

#### Scenario: DML returns empty list (MVP simplification)
- **WHEN** executing `INSERT INTO t VALUES (1)`
- **THEN** the return value MUST be `[]` (changed behavior in `tinydb-engine-v2` to return affected row count)

#### Scenario: Multiple statements separated by ;
- **WHEN** executing `CREATE TABLE t(id INT); INSERT INTO t VALUES (1); SELECT * FROM t`
- **THEN** the system MUST run all three statements in order
- **AND** return the result of the final SELECT

#### Scenario: ParseError propagates from execute
- **WHEN** executing malformed SQL `SELECT FROM`
- **THEN** the system SHALL raise `tinydb.ParseError` (a subclass of the parser's `ParseError` if applicable, or re-exported)

#### Scenario: ExecutionError on missing table
- **WHEN** executing `SELECT * FROM nonexistent`
- **THEN** the system SHALL raise `tinydb.ExecutionError` with message containing `"table nonexistent does not exist"`

### Requirement: Row class provides column access

`Row` SHALL provide attribute access and dict-style access by column name. Iteration SHALL yield column values in schema order.

#### Scenario: Access by attribute
- **WHEN** iterating over a SELECT result with row having columns `id` and `name`
- **THEN** `row.id` MUST return the `id` column value
- **AND** `row.name` MUST return the `name` column value

#### Scenario: Iteration yields values in schema order
- **WHEN** iterating `for value in row:`
- **THEN** values MUST yield in the order defined by the table's column list

#### Scenario: Repr is human-readable
- **WHEN** calling `repr(row)` for a row `(1, 'alice', TRUE)`
- **THEN** the repr MUST contain `Row(id=1, name='alice', bool_col=True)` style output

#### Scenario: Equality compares by values
- **WHEN** comparing two `Row` instances with the same values
- **THEN** `row1 == row2` MUST be `True`
- **AND** comparing with different values MUST be `False`

### Requirement: Query results iterable as list of tuples

For API ergonomics, `Database.execute(sql)` for SELECT SHALL return a list whose elements support both Row attribute access and tuple unpacking.

#### Scenario: Tuple unpack from row
- **WHEN** doing `id, name = row` for a row with two columns
- **THEN** `id` MUST bind to the first column value
- **AND** `name` MUST bind to the second column value

### Requirement: Docstrings document MVP limitations

Public API docstrings SHALL document MVP-level limitations clearly so users do not assume production-grade guarantees.

#### Scenario: Database class docstring mentions non-ACID
- **WHEN** reading the `Database` class docstring
- **THEN** it MUST contain the phrase `"MVP: non-ACID, no crash safety"`

#### Scenario: MVP does not expose transactions
- **WHEN** inspecting the `Database` class for transaction-related methods
- **THEN** `begin`, `commit`, `rollback` methods MUST NOT exist on Database (added in `tinydb-acid`)
