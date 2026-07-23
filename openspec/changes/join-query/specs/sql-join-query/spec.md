## ADDED Requirements

### Requirement: Multi-table JOIN capability

The system SHALL parse and execute two-table and multi-table `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL JOIN`, and `CROSS JOIN` queries using the existing v0.1 database, catalog, type, and transaction infrastructure.

#### Scenario: Execute a two-table inner join
- **WHEN** executing `SELECT u.id, o.id FROM users AS u INNER JOIN orders AS o ON u.id = o.user_id`
- **THEN** the result MUST contain one row for each pair satisfying the ON expression
- **AND** the result MUST preserve the left-to-right source order

#### Scenario: Execute a chained multi-table join
- **WHEN** executing a query with `t1 JOIN t2 ON ... JOIN t3 ON ...`
- **THEN** the system MUST evaluate joins in written left-associative order
- **AND** each later ON expression MUST be able to reference any source already present in the left input and the newly joined right source

#### Scenario: Execute a left join with no match
- **WHEN** a left input row has no right input row satisfying `ON`
- **THEN** a `LEFT JOIN` MUST emit exactly one output row for that left input row
- **AND** every right-source column in that row MUST be `NULL`

#### Scenario: Execute right and full joins with unmatched rows
- **WHEN** a RIGHT or FULL JOIN has rows unmatched on one or both sides
- **THEN** RIGHT JOIN MUST preserve every right-side row and FULL JOIN MUST preserve every row from both sides
- **AND** columns from the missing side MUST be `NULL`

#### Scenario: Execute a cross join
- **WHEN** executing `SELECT * FROM users CROSS JOIN orders`
- **THEN** the result MUST contain the Cartesian product of both inputs
- **AND** the parser MUST NOT require an ON or USING clause for the CROSS JOIN

### Requirement: JOIN syntax and table references

The parser SHALL recognize `JOIN`, `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL JOIN`, `CROSS JOIN`, optional `OUTER`, optional `AS`, table aliases, `ON`, `USING`, and `NATURAL` in a SELECT FROM clause. Every non-CROSS ordinary JOIN SHALL contain exactly one ON or USING clause, while NATURAL JOIN derives keys without either clause.

#### Scenario: Parse a table alias
- **WHEN** parsing `SELECT u.id FROM users AS u`
- **THEN** the AST MUST retain the base table name `users` and alias `u`

#### Scenario: Parse all explicit join kinds
- **WHEN** parsing a FROM clause containing INNER, LEFT OUTER, RIGHT OUTER, FULL OUTER, and CROSS JOIN clauses
- **THEN** the AST MUST retain each join kind, right table reference, and source order

#### Scenario: Parse USING keys
- **WHEN** parsing `FROM users u JOIN orders o USING (id)`
- **THEN** the AST MUST retain the USING column list and MUST NOT require an ON expression

#### Scenario: Parse a natural join
- **WHEN** parsing `FROM users NATURAL LEFT JOIN profiles`
- **THEN** the AST MUST retain NATURAL and LEFT join mode without an explicit ON or USING clause

#### Scenario: Reject a JOIN without a key clause
- **WHEN** parsing `SELECT * FROM users JOIN orders`
- **THEN** the parser SHALL raise a positioned `ParseError` indicating that an ON or USING expression is required

### Requirement: Qualified column name resolution

The system SHALL resolve column references using an optional table name or alias qualifier. In a multi-source query, an unqualified column SHALL be accepted only when exactly one input source provides that column. USING and NATURAL key resolution SHALL use the same source metadata and SHALL reject missing or incompatible keys.

#### Scenario: Resolve an alias-qualified column
- **WHEN** a query references `u.id` and `users` has alias `u`
- **THEN** the resolver MUST bind the reference to the `users.id` source column

#### Scenario: Reject an ambiguous unqualified column
- **WHEN** both `users` and `orders` provide a column named `id` and the query references `id` without a qualifier
- **THEN** the system SHALL raise an `AmbiguousColumn`-compatible error naming the column

#### Scenario: Resolve USING keys
- **WHEN** both JOIN inputs provide a compatible column named `id` and the query uses `USING (id)`
- **THEN** the resolver MUST create an equality JoinKey for the two source columns
- **AND** the output schema MUST contain one merged `id` key label

#### Scenario: Resolve NATURAL keys
- **WHEN** a NATURAL JOIN has multiple compatible same-named columns
- **THEN** the resolver MUST create equality JoinKeys for every common name in deterministic schema order

#### Scenario: Reject an unknown qualified or join key column
- **WHEN** a query references `missing.id`, `u.missing`, or a USING column missing from either input
- **THEN** the system SHALL raise a clear positioned or execution-time error identifying the unknown source or column

### Requirement: JOIN expressions support composed predicates

The ON expression SHALL support column references, literals, comparison operators already supported by v0.1, and recursive `AND`, `OR`, and `NOT` composition. The same expression semantics SHALL be reusable by post-join WHERE filtering.

#### Scenario: Evaluate a compound ON predicate
- **WHEN** executing `... JOIN orders o ON u.id = o.user_id AND (o.total > 10 OR o.priority = 1)`
- **THEN** the system MUST evaluate the predicate using the parser's documented precedence and existing comparison type validation

#### Scenario: Treat NULL comparison as non-match
- **WHEN** an ON comparison has a `NULL` operand
- **THEN** the comparison MUST not produce an INNER JOIN match
- **AND** an outer JOIN MUST apply its unmatched-side behavior

### Requirement: JOIN results compose with query phases

The system SHALL allow JOIN output to flow through projection, WHERE, ORDER BY, LIMIT/OFFSET, GROUP BY, HAVING, and existing aggregate functions.

#### Scenario: Filter a joined result
- **WHEN** executing a JOIN followed by `WHERE o.status = 'paid'`
- **THEN** only joined rows satisfying the WHERE expression MUST be projected

#### Scenario: Aggregate a joined result
- **WHEN** executing `SELECT u.id, COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.id HAVING COUNT(*) > 1`
- **THEN** grouping and HAVING MUST operate on the joined rows using the same aggregate semantics as v0.1

#### Scenario: Order and limit a joined result
- **WHEN** executing a JOIN with qualified `ORDER BY` and `LIMIT/OFFSET`
- **THEN** ordering MUST occur before the limit/offset is applied
- **AND** qualified sort references MUST use the same resolver as SELECT and WHERE

### Requirement: Logical plan is constructible without execution

The system SHALL expose a read-only logical plan representation for JOIN queries. Constructing or formatting the plan MUST NOT modify database state, write pages, commit a transaction, or execute DML.

#### Scenario: Build a join plan
- **WHEN** building a plan for a SELECT containing two JOIN clauses
- **THEN** the plan MUST contain a left-deep source tree with Scan and Join nodes in written order
- **AND** each Join node MUST retain its kind, ON/USING/NATURAL key representation, and child nodes

#### Scenario: Build a plan without side effects
- **WHEN** a caller constructs a plan for a valid SELECT
- **THEN** the database file, WAL, catalog, and transaction state MUST remain unchanged

### Requirement: JOIN result labels are unambiguous

The system SHALL label JOIN result columns so that source-qualified columns cannot overwrite one another in the Python API. `SELECT *` over multiple sources MUST expand columns in source order using qualified labels. USING and NATURAL merged keys MUST be emitted once.

#### Scenario: Select all joined columns
- **WHEN** executing `SELECT * FROM users u JOIN orders o ON u.id = o.user_id`
- **THEN** the returned row metadata MUST contain labels such as `u.id` and `o.id`
- **AND** labels MUST remain unique even when source tables share column names

#### Scenario: Select a USING join
- **WHEN** executing `SELECT * FROM users u JOIN orders o USING (id)`
- **THEN** the returned result MUST contain one merged `id` output for the USING key
- **AND** non-key duplicate names MUST remain source-qualified

#### Scenario: Explicit qualified projection
- **WHEN** executing `SELECT u.id, o.id FROM users u JOIN orders o ON u.id = o.user_id`
- **THEN** the result MUST expose both projected values without one replacing the other
- **AND** the Python row mapping MUST permit lookup by the qualified labels

### Requirement: Outer join output ordering is stable

`LEFT`, `RIGHT`, and `FULL JOIN` MUST emit rows in `strict-left-deep-insertion` order: matching combinations follow the left-deep nested-loop input order; `LEFT` unmatched rows immediately follow their left row; `RIGHT`/`FULL` unmatched right-side rows are appended after all matching rows in the right-side scan order.

#### Scenario: LEFT emits unmatched rows adjacent to their left row
- **WHEN** executing `SELECT u.id, o.id FROM users u LEFT JOIN orders o ON u.id = o.user_id`
- **AND** user `1` has no matching order
- **THEN** the result MUST contain a row with `u.id = 1` and `o.id = NULL`
- **AND** that row MUST appear immediately after the last matched row of user `1` (or first if no match).

#### Scenario: FULL preserves right unmatched in scan order
- **WHEN** executing `SELECT u.id, o.id FROM users u FULL JOIN orders o ON u.id = o.user_id`
- **AND** some orders reference users that do not exist
- **THEN** the result MUST contain a row for each unmatched right order
- **AND** those rows MUST appear after all matched combinations and left-unmatched rows
- **AND** the unmatched right rows MUST be in `orders` source scan order.

### Requirement: NATURAL JOIN with no common columns degrades to CROSS

When a `NATURAL JOIN` has no common column between the two sources, the join MUST behave as a `CROSS JOIN` (Cartesian product) without raising an error. The user-declared outer join kind (LEFT/RIGHT/FULL) still applies for NULL padding.

#### Scenario: NATURAL with empty common column set
- **WHEN** executing `SELECT * FROM users NATURAL LEFT JOIN audit`
- **AND** `users` and `audit` share no column name
- **THEN** the result MUST be the Cartesian product of the two inputs
- **AND** unmatched-side NULL padding MUST follow LEFT semantics.

### Requirement: USING and NATURAL merged keys use coalesce semantics

`USING (col, ...)` and `NATURAL` merged keys MUST emit a single output column whose value is taken from the left source first; if that value is `NULL`, the right source value is used; if both are `NULL`, the merged key is `NULL`. The merged key label MUST be the unqualified column name. Outer-join unmatched rows use `NULL` on the missing side.

#### Scenario: Coalesce chooses non-null side
- **WHEN** executing `SELECT * FROM users u LEFT JOIN profiles p USING (id)`
- **AND** a left row has `id = 1` with `NULL` profile value and the right row has `id = 1` with non-NULL value
- **THEN** the merged `id` column MUST equal the right source value.

#### Scenario: Both sides null yields null
- **WHEN** both left and right merged-key values are `NULL`
- **THEN** the merged key MUST be `NULL`.
