## MODIFIED Requirements

### Requirement: Parser produces AST nodes

The parser SHALL consume a token stream and produce a typed AST node. Each supported statement type SHALL have a distinct AST node class. SELECT AST nodes SHALL retain table references, optional aliases, JOIN kinds, connection key forms, and qualified column references. Errors SHALL raise `ParseError` with line, column, and message.

#### Scenario: CREATE TABLE produces CreateTable AST
- **WHEN** parsing `CREATE TABLE users (id INT, name TEXT)`
- **THEN** the parser MUST emit a `CreateTable(name="users", columns=[("id", "INT"), ("name", "TEXT")])` AST node
- **AND** line/column attributes MUST point to the `CREATE` keyword

#### Scenario: CREATE TABLE rejects duplicate column names
- **WHEN** parsing `CREATE TABLE t(id INT, id TEXT)`
- **THEN** the parser SHALL raise `ParseError` with message containing `"duplicate column"` and column position

#### Scenario: CREATE TABLE rejects unsupported type
- **WHEN** parsing `CREATE TABLE t(id VARCHAR(10))`
- **THEN** the parser SHALL raise `ParseError` mentioning `"VARCHAR not supported in MVP"`
- **AND** the position attribute MUST point to `VARCHAR`

#### Scenario: SELECT AST retains qualified JOIN structure
- **WHEN** parsing `SELECT u.id FROM users AS u LEFT JOIN orders o ON u.id = o.user_id`
- **THEN** the AST MUST contain a table reference for `users` with alias `u`
- **AND** MUST contain a LEFT JOIN clause whose right table is `orders` with alias `o`
- **AND** the selected and ON columns MUST retain their qualifiers

#### Scenario: SELECT AST retains USING and NATURAL structure
- **WHEN** parsing `SELECT * FROM users u NATURAL FULL JOIN profiles p` or `SELECT * FROM users u JOIN profiles p USING (id)`
- **THEN** the AST MUST retain the NATURAL or USING key form, join kind, and source order

### Requirement: SELECT parsing with WHERE col = literal

The parser SHALL recognize `SELECT` queries with a FROM table reference, optional alias, zero or more `INNER`, `LEFT`, `RIGHT`, `FULL`, or `CROSS` JOIN clauses, optional `ON`/`USING`/`NATURAL` key forms, optional qualified column references, and the existing WHERE/ORDER BY/LIMIT/OFFSET/GROUP BY/HAVING syntax. WHERE and JOIN expressions MUST use the existing expression grammar and MUST preserve single-table compatibility.

#### Scenario: Parse SELECT * from one table
- **WHEN** parsing `SELECT * FROM users`
- **THEN** the parser MUST emit a SELECT AST with one table reference, no joins, and a wildcard projection

#### Scenario: Parse SELECT with explicit columns
- **WHEN** parsing `SELECT id, name FROM users`
- **THEN** the parser MUST emit a SELECT AST with unqualified column references for `id` and `name`

#### Scenario: Parse SELECT with WHERE col = literal
- **WHEN** parsing `SELECT * FROM users WHERE id = 1`
- **THEN** the parser MUST emit a SELECT AST with an unqualified `id` column reference and the literal predicate

#### Scenario: Parse a qualified column
- **WHEN** parsing `SELECT u.id FROM users AS u`
- **THEN** the parser MUST emit a column reference with qualifier `u` and name `id`

#### Scenario: Parse explicit outer and cross joins
- **WHEN** parsing `SELECT * FROM users u LEFT OUTER JOIN orders o ON u.id = o.user_id RIGHT JOIN profiles p ON o.id = p.order_id FULL OUTER JOIN flags f ON p.id = f.profile_id CROSS JOIN audit a`
- **THEN** the parser MUST emit the join kinds and source order without requiring a key clause for CROSS JOIN

#### Scenario: Parse USING and NATURAL joins
- **WHEN** parsing `SELECT * FROM users u JOIN orders o USING (id)` or `SELECT * FROM users NATURAL LEFT JOIN profiles`
- **THEN** the parser MUST emit the corresponding USING column list or NATURAL marker

#### Scenario: Parse a composed ON expression
- **WHEN** parsing `SELECT * FROM users u LEFT JOIN orders o ON u.id = o.user_id AND (o.total > 10 OR o.priority = 1)`
- **THEN** the parser MUST retain the complete composed ON expression

#### Scenario: SELECT rejects missing FROM
- **WHEN** parsing `SELECT id`
- **THEN** the parser SHALL raise `ParseError` with message containing `"expected FROM"`

### Requirement: ParseError carries position and message

All parse-time errors SHALL raise `ParseError` with `line`, `col`, and human-readable `message` attributes, including malformed JOIN clauses and qualified names.

#### Scenario: Unexpected token reports position
- **WHEN** parsing `CREATE 123 (id INT)` (digit where identifier expected)
- **THEN** `ParseError.line` and `ParseError.col` MUST point to `123`
- **AND** message MUST contain `"expected table name"`

#### Scenario: Multiple statements separated by ; supported at top level
- **WHEN** parsing `CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1)`
- **THEN** the parser MUST emit a `StatementList` containing two AST nodes in source order

#### Scenario: JOIN requires an appropriate key clause
- **WHEN** parsing `SELECT * FROM users JOIN orders`
- **THEN** the parser SHALL raise `ParseError` with the source position of the missing ON or USING clause

#### Scenario: CROSS and NATURAL JOIN do not require ON
- **WHEN** parsing a valid CROSS JOIN or NATURAL JOIN without ON/USING
- **THEN** the parser MUST accept the statement and retain the implicit key semantics

### Requirement: NATURAL JOIN automatically discovers common columns

The parser SHALL recognize `NATURAL [INNER|LEFT|RIGHT|FULL] JOIN` and emit a join clause marked as natural. The resolver SHALL compute the natural key set by intersecting the column names of the two sources in deterministic schema order.

#### Scenario: NATURAL JOIN emits natural marker
- **WHEN** parsing `SELECT * FROM users NATURAL LEFT JOIN profiles`
- **THEN** the AST MUST retain a natural join marker and the LEFT mode
- **AND** the resolver MUST compute the natural key set from the common column names of `users` and `profiles` in catalog schema order.
