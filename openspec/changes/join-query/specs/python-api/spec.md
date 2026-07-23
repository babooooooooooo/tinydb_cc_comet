## MODIFIED Requirements

### Requirement: execute method runs SQL statements

`Database.execute(sql)` SHALL parse the supplied SQL string, execute the resulting AST, and return a result value defined per statement type. SELECT statements MAY read from multiple tables using the JOIN capability. Existing DDL, DML, transaction, and multi-statement behavior SHALL remain compatible.

#### Scenario: SELECT returns list of Row
- **WHEN** executing `SELECT * FROM users`
- **THEN** the return value MUST be a `list[Row]`

#### Scenario: SELECT returns joined rows
- **WHEN** executing `SELECT u.id, o.id FROM users u JOIN orders o ON u.id = o.user_id`
- **THEN** the return value MUST be a `list[Row]`
- **AND** each row MUST contain both projected values without column-name collision

#### Scenario: DDL returns empty list
- **WHEN** executing `CREATE TABLE t(id INT)`
- **THEN** the return value MUST be `[]`

#### Scenario: DML returns empty list
- **WHEN** executing `INSERT INTO t VALUES (1)`
- **THEN** the return value MUST be `[]`

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

#### Scenario: JOIN name errors are explicit
- **WHEN** executing a JOIN with an unknown table, unknown qualified column, incompatible USING/NATURAL key, or ambiguous unqualified column
- **THEN** the system SHALL raise a documented TinyDB error identifying the source of the resolution failure

### Requirement: Row class provides column access

`Row` SHALL provide attribute access and mapping-style access by column name. Iteration SHALL yield column values in result-column order. For JOIN results, qualified labels such as `u.id` MUST be available through mapping-style access, USING/NATURAL merged keys MUST be available by their merged label, and no source's same-named column may be silently overwritten.

#### Scenario: Access by attribute for a single-table row
- **WHEN** iterating over a SELECT result with row having columns `id` and `name`
- **THEN** `row.id` MUST return the `id` column value
- **AND** `row.name` MUST return the `name` column value

#### Scenario: Iteration yields values in result order
- **WHEN** iterating `for value in row:`
- **THEN** values MUST yield in the order defined by the SELECT result columns

#### Scenario: Access qualified JOIN columns by mapping
- **WHEN** a JOIN result has columns `u.id` and `o.id`
- **THEN** `row["u.id"]` and `row["o.id"]` MUST return their respective values
- **AND** attribute access MUST remain available for labels that are valid Python attribute names

#### Scenario: Access a merged USING/NATURAL key
- **WHEN** a JOIN result is created with `USING (id)` or NATURAL matching `id`
- **THEN** the merged result column MUST be available under one stable `id`-compatible label
- **AND** the two source values MUST NOT appear as duplicate mapping keys

#### Scenario: Repr is human-readable
- **WHEN** calling `repr(row)` for a row `(1, 'alice', TRUE)`
- **THEN** the repr MUST contain `Row(id=1, name='alice', bool_col=True)` style output

#### Scenario: Equality compares by values
- **WHEN** comparing two `Row` instances with the same values
- **THEN** `row1 == row2` MUST be `True`
- **AND** comparing with different values MUST be `False`

### Requirement: ResolutionError is exposed and identifiable

`tinydb.ResolutionError` SHALL be importable from the top-level package and SHALL be a subclass of `tinydb.ExecutionError`. Specific name-resolution failures SHALL raise documented subtypes (e.g. `AmbiguousColumn`, `DuplicateAlias`, `UnknownSource`, `UnknownQualifiedColumn`, `MissingUsingKey`, `IncompatibleKeyTypes`).

#### Scenario: Ambiguous unqualified column raises ResolutionError
- **WHEN** executing `SELECT id FROM users u JOIN orders o`
- **THEN** the system SHALL raise `tinydb.AmbiguousColumn` (a `ResolutionError`) naming the column and the conflicting sources.

#### Scenario: Missing USING key raises ResolutionError
- **WHEN** executing `SELECT * FROM users u JOIN orders o USING (missing_col)`
- **THEN** the system SHALL raise `tinydb.MissingUsingKey` (a `ResolutionError`) identifying the missing column.

### Requirement: JOIN Row supports mapping-style access by qualified label

For JOIN results, `Row` MUST expose a `__getitem__` mapping by output-column label. Qualified labels such as `u.id` and merged USING/NATURAL labels such as `id` MUST be reachable through `row["u.id"]` and `row["id"]`. Attribute access SHALL remain available for labels that are valid Python identifiers and are not ambiguous.

#### Scenario: Mapping access by qualified label
- **WHEN** a JOIN result row has output columns `u.id` and `o.id`
- **THEN** `row["u.id"]` and `row["o.id"]` MUST return the corresponding values.

#### Scenario: Mapping access by merged key
- **WHEN** a JOIN result row has the merged USING/NATURAL key `id`
- **THEN** `row["id"]` MUST return the coalesced value
- **AND** the source-side qualified labels MUST NOT be reachable as separate mapping keys.
