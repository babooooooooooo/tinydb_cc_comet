# cli-enhancements

## ADDED Requirements

### Requirement: Interactive REPL provides multi-line input

The tinydb-repl shell SHALL accept multi-line SQL statements terminated by a semicolon. While the input buffer is unterminated (no closing `;`, unmatched quote, or unmatched parenthesis), the REPL MUST display a continuation prompt and accumulate subsequent lines into the same statement.

#### Scenario: Statement spanning multiple lines executes as one query
- **WHEN** a user enters a SELECT statement across three lines, the third ending with `;`
- **THEN** the REPL MUST execute the entire statement as a single query
- **AND** the continuation prompt MUST appear on lines 2 and 3

#### Scenario: Unclosed quote triggers continuation prompt
- **WHEN** a user enters `SELECT 'unterminated` (no closing quote) and presses Enter
- **THEN** the REPL MUST display the continuation prompt on the next line

#### Scenario: Empty line at continuation prompt cancels statement
- **WHEN** a user enters an unterminated statement, then presses Enter on an empty line followed by Ctrl-C
- **THEN** the REPL MUST discard the buffered input and return to the primary prompt

### Requirement: SQL syntax highlighting during input

When the terminal supports ANSI colors (TERM not "dumb", NO_COLOR not set), the REPL SHALL highlight SQL tokens in real time as the user types. Keywords, strings, numbers, operators, and comments SHALL receive distinct visual styling via pygments.

#### Scenario: SQL keywords render in color
- **WHEN** a user types `SELECT * FROM users` in a color-supporting terminal
- **THEN** the REPL MUST render the SQL with at least the SELECT and FROM keywords visually distinguished from identifiers and operators

#### Scenario: NO_COLOR environment disables highlighting
- **WHEN** the `NO_COLOR` environment variable is set to `1`
- **THEN** the REPL MUST NOT emit any ANSI color escape codes in its input or output

#### Scenario: TERM=dumb disables highlighting
- **WHEN** the `TERM` environment variable is `dumb`
- **THEN** the REPL MUST NOT emit ANSI color escape codes

### Requirement: Line editing with Emacs keybindings

The REPL SHALL provide readline-style line editing capabilities including: move to start of line (Ctrl-A), move to end of line (Ctrl-E), delete to end (Ctrl-K), delete word backward (Ctrl-W), and history navigation (up/down arrows).

#### Scenario: Ctrl-A moves cursor to line start
- **WHEN** the user has typed `SELECT * FROM` and presses Ctrl-A
- **THEN** the cursor MUST position at the beginning of the line

#### Scenario: Up arrow recalls previous statement
- **WHEN** the user has previously executed `SELECT 1` and presses Up arrow on an empty prompt
- **THEN** the REPL MUST display `SELECT 1` as the current input

### Requirement: Meta command .explain displays query plan

The REPL SHALL support `.explain <sql>` which parses the SQL into a `LogicalPlan` and renders it as a tree without executing the query. The output MUST use `plan.format_plan()` to produce indented node output.

#### Scenario: .explain SELECT displays plan tree
- **WHEN** a user enters `.explain SELECT * FROM users WHERE age > 18`
- **THEN** the REPL MUST output the LogicalPlan tree (Scan → Filter → Project) without executing the query
- **AND** the output MUST NOT include result rows

#### Scenario: .explain with invalid SQL shows parse error
- **WHEN** a user enters `.explain SELECT FROMM users` (invalid)
- **THEN** the REPL MUST display the parse error message (not a Python traceback)

### Requirement: Meta command .indexes lists index metadata

The REPL SHALL support `.indexes [table]` which lists all indexes in the database. With no argument, all indexes are listed. With a table name, only indexes for that table are listed.

#### Scenario: .indexes lists all indexes
- **WHEN** a user enters `.indexes`
- **THEN** the REPL MUST list each index as `<table>.<column>` with BTree root_page_id and estimated key count

#### Scenario: .indexes users shows only indexes on users
- **WHEN** a user enters `.indexes users`
- **THEN** the REPL MUST list only indexes whose table is `users`

### Requirement: Meta command .stats shows database statistics

The REPL SHALL support `.stats` which displays table count, total row count, page count, free page count, and WAL file size.

#### Scenario: .stats on empty database shows zeros
- **WHEN** a user opens a fresh database and enters `.stats`
- **THEN** the REPL MUST display: `Tables: 0`, `Rows: 0`, `Pages: 1`, `Free pages: 0`, `WAL: 0 bytes`

#### Scenario: .stats after inserts shows non-zero counts
- **WHEN** a user has inserted 100 rows into a table and enters `.stats`
- **THEN** the REPL MUST display `Tables: 1` and `Rows: 100` (or higher, accounting for catalog overhead)

### Requirement: Meta command .timer toggles execution timing

The REPL SHALL support `.timer on|off` which toggles display of execution time after each SQL statement.

#### Scenario: .timer on adds timing to subsequent output
- **WHEN** a user runs `.timer on` and then executes `SELECT 1`
- **THEN** the result line MUST be followed by a `Time: X.XXX ms` line

#### Scenario: .timer off suppresses timing
- **WHEN** a user runs `.timer off`
- **THEN** subsequent statements MUST NOT include timing output

### Requirement: Meta command .format switches output format

The REPL SHALL support `.format <table|csv|json>` which switches the result output format. The default MUST be `table`.

#### Scenario: .format csv emits CSV
- **WHEN** a user runs `.format csv` and executes `SELECT id, name FROM users LIMIT 2`
- **THEN** the REPL MUST emit RFC 4180 CSV with a header row and data rows

#### Scenario: .format json emits JSON array
- **WHEN** a user runs `.format json` and executes `SELECT id, name FROM users LIMIT 2`
- **THEN** the REPL MUST emit a JSON array of objects with `id` and `name` keys

#### Scenario: .format table restores ASCII table
- **WHEN** a user runs `.format table` after csv or json
- **THEN** the REPL MUST emit the original ASCII table output

### Requirement: REPL degrades gracefully when prompt_toolkit unavailable

When the `prompt_toolkit` package cannot be imported (e.g., minimal install), the REPL MUST fall back to stdlib-only mode using `input()` for single-line reads, single statement per line, with no syntax highlighting or advanced line editing.

#### Scenario: Missing prompt_toolkit falls back to input()
- **WHEN** prompt_toolkit is not importable and a user invokes `tinydb-repl`
- **THEN** the REPL MUST start successfully using `input()` for line reads
- **AND** MUST display a warning at startup that advanced features are unavailable
- **AND** MUST still accept single-line statements terminated by `;`

### Requirement: Persistent command history

The REPL SHALL persist command history to `~/.tinydb_history` between sessions. History MUST be loaded at startup and saved at exit.

#### Scenario: History persists across sessions
- **WHEN** a user executes `SELECT 1`, exits, and restarts `tinydb-repl`
- **THEN** pressing Up arrow on the empty prompt MUST recall `SELECT 1`

### Requirement: Backward-compatible meta commands

The REPL MUST continue to support the existing meta commands: `.exit`, `.quit`, `.help`, `.tables`, `.schema <name>`, `.read <path>` with unchanged behavior.

#### Scenario: .exit terminates the REPL
- **WHEN** a user enters `.exit`
- **THEN** the REPL MUST terminate cleanly with exit code 0

#### Scenario: .help lists all available commands
- **WHEN** a user enters `.help`
- **THEN** the REPL MUST list both legacy and new meta commands (`.explain`, `.indexes`, `.stats`, `.timer`, `.format`, `.color`)