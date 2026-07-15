# Spec: sql-minimal-parser

> 范围：MVP 阶段的 5 个语句（CREATE TABLE / DROP TABLE / INSERT / SELECT / DELETE），WHERE 暂只支持 `col = literal`。更复杂的解析（UPDATE、AND/OR、ORDER BY、LIMIT、子查询）属于 `tinydb-engine-v2`。

## ADDED Requirements

### Requirement: Tokenizer recognizes lexical categories

The tokenizer SHALL classify input characters into six token categories: identifier / keyword, integer, float, text literal, boolean literal, and punctuation. Invalid characters SHALL raise a tokenizer error with line and column.

#### Scenario: Tokenize identifier
- **WHEN** tokenizing `users`
- **THEN** the tokenizer MUST emit one IDENT token with value `"users"` and source position `(line=1, col=1)`

#### Scenario: Tokenize keyword case-insensitively
- **WHEN** tokenizing `CREATE` or `create` or `Create`
- **THEN** all three SHALL emit a KEYWORD token with the same canonical form `"CREATE"`

#### Scenario: Tokenize text literal with embedded space
- **WHEN** tokenizing `'hello world'`
- **THEN** the tokenizer MUST emit one TEXT token with value `"hello world"`

#### Scenario: Tokenize text literal with escaped quote
- **WHEN** tokenizing `'it''s ok'` (SQL-style doubled single quote)
- **THEN** the tokenizer MUST emit one TEXT token with value `"it's ok"`

#### Scenario: Tokenize punctuation
- **WHEN** tokenizing `( ) , ; = *`
- **THEN** the tokenizer MUST emit one PUNCT token for each in source order

#### Scenario: Tokenizer error reports position
- **WHEN** tokenizing `@` (invalid character)
- **THEN** the tokenizer SHALL raise `TokenError` with `line` and `col` attributes set to the position of `@`

### Requirement: Parser produces AST nodes

The parser SHALL consume a token stream and produce a typed AST node. Each supported statement type SHALL have a distinct AST node class. Errors SHALL raise `ParseError` with line, column, and message.

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

### Requirement: DROP TABLE parsing

The parser SHALL recognize the `DROP TABLE` statement and emit a `DropTable` AST node.

#### Scenario: Parse DROP TABLE
- **WHEN** parsing `DROP TABLE users`
- **THEN** the parser MUST emit a `DropTable(name="users")` AST node

#### Scenario: DROP TABLE missing table name
- **WHEN** parsing `DROP TABLE`
- **THEN** the parser SHALL raise `ParseError` with message containing `"expected table name"`

### Requirement: INSERT parsing with explicit column list

The parser SHALL recognize the `INSERT INTO table(col, ...) VALUES (val, ...)` form and emit an `Insert` AST node.

#### Scenario: Parse single-row INSERT
- **WHEN** parsing `INSERT INTO users(id, name) VALUES (1, 'alice')`
- **THEN** the parser MUST emit `Insert(table="users", columns=["id","name"], values=[[1, "alice"]])`

#### Scenario: Parse multi-row INSERT
- **WHEN** parsing `INSERT INTO users(id, name) VALUES (1, 'alice'), (2, 'bob')`
- **THEN** the parser MUST emit `Insert(table="users", columns=["id","name"], values=[[1,"alice"],[2,"bob"]])`

#### Scenario: INSERT column count mismatch rejected
- **WHEN** parsing `INSERT INTO users(id, name) VALUES (1)`
- **THEN** the parser SHALL raise `ParseError` mentioning `"value count mismatch"`

### Requirement: SELECT parsing with WHERE col = literal

The parser SHALL recognize the `SELECT ... FROM table [WHERE col = literal]` form and emit a `Select` AST node. WHERE clauses MUST support exactly one `col = literal` predicate (no AND/OR/IN/LIKE).

#### Scenario: Parse SELECT *
- **WHEN** parsing `SELECT * FROM users`
- **THEN** the parser MUST emit `Select(table="users", columns=["*"], where=None)`

#### Scenario: Parse SELECT with explicit columns
- **WHEN** parsing `SELECT id, name FROM users`
- **THEN** the parser MUST emit `Select(table="users", columns=["id","name"], where=None)`

#### Scenario: Parse SELECT with WHERE col = literal
- **WHEN** parsing `SELECT * FROM users WHERE id = 1`
- **THEN** the parser MUST emit `Select(table="users", columns=["*"], where=("id", "=", 1))`

#### Scenario: Parse SELECT rejects WHERE with unsupported operator
- **WHEN** parsing `SELECT * FROM users WHERE id > 1`
- **THEN** the parser SHALL raise `ParseError` mentioning `"operator > not supported; MVP supports only ="`

#### Scenario: Parse SELECT rejects missing FROM
- **WHEN** parsing `SELECT id`
- **THEN** the parser SHALL raise `ParseError` with message containing `"expected FROM"`

### Requirement: DELETE parsing with WHERE optional

The parser SHALL recognize `DELETE FROM table [WHERE col = literal]` and emit a `Delete` AST node. WHERE MUST be optional.

#### Scenario: Parse DELETE all
- **WHEN** parsing `DELETE FROM users`
- **THEN** the parser MUST emit `Delete(table="users", where=None)`

#### Scenario: Parse DELETE WHERE
- **WHEN** parsing `DELETE FROM users WHERE id = 1`
- **THEN** the parser MUST emit `Delete(table="users", where=("id", "=", 1))`

### Requirement: ParseError carries position and message

All parse-time errors SHALL raise `ParseError` with `line`, `col`, and human-readable `message` attributes.

#### Scenario: Unexpected token reports position
- **WHEN** parsing `CREATE 123 (id INT)` (digit where identifier expected)
- **THEN** `ParseError.line` and `ParseError.col` MUST point to `123`
- **AND** message MUST contain `"expected table name"`

#### Scenario: Multiple statements separated by ; supported at top level
- **WHEN** parsing `CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1)`
- **THEN** the parser MUST emit a `StatementList` containing two AST nodes in source order

### Requirement: Parser is pure (no I/O)

The parser SHALL be a pure function from token stream to AST. It MUST NOT perform any file I/O or global state mutation; this enables isolated unit testing.

#### Scenario: Parser call is deterministic
- **WHEN** calling the parser twice with the same input
- **THEN** the two AST outputs MUST be structurally equal
