# repl-shell Specification

## Purpose
TBD - created by archiving change repl-shell. Update Purpose after archive.
## Requirements
### Requirement: REPL provides interactive SQL loop

The REPL MUST start with a `tinydb>` prompt, accept SQL input line by line, and execute each completed statement through the existing `Database.execute(sql)` API. SELECT results MUST be printed; DDL/DML MUST print `OK`. Empty result sets MUST display `(no rows)`. The REPL MUST exit with status code 0 on EOF (Ctrl-D) or `.exit` / `.quit`.

#### Scenario: Basic CRUD round-trip

- **WHEN** user enters `CREATE TABLE t(id INT);` then `INSERT INTO t(id) VALUES (1);` then `SELECT * FROM t;` at the prompt
- **THEN** REPL prints `OK` after CREATE, `OK` after INSERT, and one row (`Row(id=1)`) after SELECT

#### Scenario: Empty result message

- **WHEN** user enters `SELECT * FROM empty_table;` against a table with no rows
- **THEN** REPL prints `(no rows)`

#### Scenario: Exit on Ctrl-D

- **WHEN** user sends EOF (Ctrl-D) on stdin
- **THEN** REPL exits with status code 0

#### Scenario: Exit on .exit

- **WHEN** user enters `.exit`
- **THEN** REPL exits with status code 0

#### Scenario: Exit on .quit

- **WHEN** user enters `.quit`
- **THEN** REPL exits with status code 0

### Requirement: REPL supports multi-line SQL continuation

When the current input buffer contains an unterminated single-quoted string (odd count of `'`) or unmatched open parentheses (`(` count greater than `)` count), the REPL MUST enter continuation mode displaying a `...>` prompt, accumulate further input until the buffer is balanced, and execute only the final complete statement(s).

#### Scenario: Multi-line INSERT with VALUES spanning lines

- **WHEN** user enters `INSERT INTO t(id, name) VALUES (` then on next line `  1, 'alice');`
- **THEN** REPL shows `...>` after the first line, executes the combined statement on the second line, and prints `OK`

#### Scenario: Multi-line text literal

- **WHEN** user enters `INSERT INTO t(name) VALUES ('alice` then on next line ` smith');`
- **THEN** REPL waits for closing quote, executes the combined statement, prints `OK`

### Requirement: REPL exposes meta-commands

Lines starting with `.` MUST be dispatched to meta-command handlers and MUST NOT be passed to the SQL parser. Meta-commands MUST support: `.exit` / `.quit`, `.help`, `.tables`, `.schema <name>`, `.read <path>`. Unknown meta-commands MUST print `ERROR: unknown command: <name>`.

#### Scenario: .help lists meta-commands

- **WHEN** user enters `.help`
- **THEN** REPL prints a list including `.exit`, `.quit`, `.help`, `.tables`, `.schema`, `.read`

#### Scenario: .tables lists table names

- **WHEN** user has created tables `users` and `orders` then enters `.tables`
- **THEN** REPL prints `users` and `orders` (one per line, in any order)

#### Scenario: .schema prints CREATE TABLE statement

- **WHEN** user has created `users(id INT, name TEXT)` then enters `.schema users`
- **THEN** REPL prints `CREATE TABLE users(id INT, name TEXT);`

#### Scenario: .schema unknown table

- **WHEN** user enters `.schema ghost`
- **THEN** REPL prints `ERROR: no such table: ghost`

#### Scenario: .read executes SQL file

- **WHEN** user creates a file `seed.sql` containing `CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1);` then enters `.read seed.sql`
- **THEN** REPL executes both statements, prints `OK` for each, and returns to the prompt

#### Scenario: .read missing file

- **WHEN** user enters `.read nope.sql` and the file does not exist
- **THEN** REPL prints `ERROR: cannot read file: nope.sql`

#### Scenario: Unknown meta-command

- **WHEN** user enters `.foo`
- **THEN** REPL prints `ERROR: unknown command: .foo`

### Requirement: REPL persists command history on Unix

On Unix-like platforms where `readline` is importable, the REPL MUST load `~/.tinydb_history` on startup (if the file exists) and append the current session's commands to it on exit. Missing history file MUST NOT be an error. Write failures (permission denied, disk full) MUST be silently ignored.

#### Scenario: History loaded on startup

- **WHEN** `~/.tinydb_history` exists with prior commands `SELECT 1;` and the user starts `tinydb-repl`
- **THEN** pressing the up arrow recalls `SELECT 1;` as the most recent prior command

#### Scenario: History saved on exit

- **WHEN** user enters `SELECT 2;` then `.exit`
- **THEN** `~/.tinydb_history` contains `SELECT 2;`

#### Scenario: Missing history file is not an error

- **WHEN** `~/.tinydb_history` does not exist and the user starts `tinydb-repl`
- **THEN** REPL starts normally without raising an error

### Requirement: REPL silently falls back when readline is unavailable

On platforms where `import readline` raises `ImportError` (e.g., Windows without pyreadline3), the REPL MUST use Python's built-in `input()` and MUST NOT load or save any history file. The REPL MUST continue to function for SQL execution, meta-commands, and output formatting.

#### Scenario: Windows fallback path

- **WHEN** `import readline` raises `ImportError` and user starts `tinydb-repl`
- **THEN** REPL accepts SQL input via built-in `input()` and executes statements without raising readline errors

### Requirement: REPL formats SELECT output as aligned table

SELECT results MUST be printed as a table with a header row (column names), a separator row (`---` segments), and one row per `Row` value. Column width MUST be `min(max(len(header), max(value_width)), 30)`. Values exceeding the cap MUST be truncated with a trailing `…`. Empty result set MUST print `(no rows)` instead of a table.

#### Scenario: Two-column aligned output

- **WHEN** user selects two columns `id` and `name` from a 3-row table with values `1,'alice'`, `2,'bob'`, `3,'carol'`
- **THEN** output contains a header row `id | name`, a separator row `--- | ---`, and three data rows aligned by column

#### Scenario: Long value truncated with ellipsis

- **WHEN** a column value's string representation exceeds 30 characters
- **THEN** the printed cell shows the first 29 characters followed by `…`

#### Scenario: Empty result shows "(no rows)"

- **WHEN** SELECT returns zero rows
- **THEN** REPL prints exactly `(no rows)` (no table, no header)

### Requirement: REPL displays errors as single line

Exceptions raised by `Database.execute(sql)` MUST be caught and printed as `ERROR: <ExceptionClass>: <message>` on a single line, without traceback. The REPL MUST remain running after printing the error and continue accepting input.

#### Scenario: Parse error

- **WHEN** user enters `SELECT FROM` (invalid SQL)
- **THEN** REPL prints `ERROR: ParseError: line 1, col 7: ...` (single line, no traceback) and returns to the prompt

#### Scenario: Execution error

- **WHEN** user enters `SELECT * FROM ghost;` against a database with no `ghost` table
- **THEN** REPL prints `ERROR: ExecutionError: table 'ghost' does not exist` (single line) and returns to the prompt

#### Scenario: REPL continues after error

- **WHEN** an error has just been printed
- **THEN** the next SQL statement executes normally

### Requirement: REPL accepts --database CLI flag

`tinydb-repl` MUST accept an optional `--database <path>` argument that opens a file-backed `Database(path)` instead of the default `:memory:` database. The path MUST be opened via the existing `Database` constructor (which creates the file if absent).

#### Scenario: Default in-memory database

- **WHEN** user starts `tinydb-repl` with no flag
- **THEN** REPL connects to a `:memory:` database (no file is created in the working directory)

#### Scenario: File-backed database via flag

- **WHEN** user starts `tinydb-repl --database /tmp/foo.db`
- **THEN** REPL opens `/tmp/foo.db`; INSERT/SELECT mutations persist to that file across REPL restarts

