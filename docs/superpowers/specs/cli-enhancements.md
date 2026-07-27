# tinydb REPL: public contracts

This document describes the public behavior of the `tinydb-repl` interactive
SQL shell and the interfaces used by its input, output, and meta-command
layers. The command is available after installing the project (or the `repl`
optional extra).

## Starting the REPL

```bash
tinydb-repl                              # in-memory database
tinydb-repl --database path/to/data.db   # file-backed database
```

The `--database PATH` option opens an existing database or creates a new one.
`--help` prints usage information. SQL statements are executed against the
selected database until EOF (`Ctrl-D`) or `.exit`/`.quit`.

## Input adapter interface: `ReplIOProtocol`

The REPL loop depends on an I/O adapter rather than directly depending on a
terminal library. The protocol is available from `tinydb._repl_io`:

```python
from typing import Protocol

class ReplIOProtocol(Protocol):
    def read_statement(self) -> str | None: ...
    def add_history(self, statement: str) -> None: ...
    def save_history(self) -> None: ...
```

Implementations must provide the following behavior:

| Method | Contract |
|---|---|
| `read_statement()` | Read the next submitted input. Return the complete SQL or meta-command text, `None` on EOF, and an empty string when the current input is blank or was cancelled with `Ctrl-C`. |
| `add_history(statement)` | Record an executed statement when the adapter has an explicit history backend. Blank statements are ignored. |
| `save_history()` | Flush or persist adapter-managed history. It is safe for an adapter without persistent history to implement this as a no-op. |

The REPL ignores blank results, dispatches lines beginning with `.` as meta
commands, and sends other results to the SQL executor. A `Ctrl-C` clears the
current input buffer without terminating the shell; `Ctrl-D` returns EOF.

## `PromptToolkitReplIO`

`PromptToolkitReplIO` is the rich adapter used when both `prompt_toolkit` and
`pygments` can be imported. It is constructed as:

```python
PromptToolkitReplIO(
    db_path: str,
    history_path: pathlib.Path,
    color: bool,
)
```

The adapter provides:

- Multiline editing through `PromptSession(multiline=True)`.
- A `...>` continuation prompt while parentheses, quoted strings, or SQL
  comments are incomplete. A semicolon submits the accumulated statement.
- SQL token highlighting through Pygments' SQL lexer when `color` is true.
- Prompt-toolkit history and history search, including Up/Down navigation and
  `Ctrl-R` search.
- Prompt-toolkit's standard Emacs-style editing keys, including `Ctrl-A`,
  `Ctrl-E`, `Ctrl-K`, and `Ctrl-W`.

History is stored in the configured path, normally `~/.tinydb_history`.
Prompt-toolkit records submitted text in its history buffer, so
`add_history()` is intentionally a no-op for this adapter and must not append
the same statement a second time. `FileHistory` manages persistence, so
`save_history()` is also a no-op.

`set_color(enabled: bool)` rebuilds the prompt session with or without the SQL
lexer while preserving its history object. This is used by `.color on` and
`.color off`.

Constructing `PromptToolkitReplIO` when the optional packages are unavailable
raises `RuntimeError`; the REPL selects `FallbackReplIO` instead.

## `FallbackReplIO`

`FallbackReplIO` is the standard-library-only adapter. It has the same
constructor shape as the rich adapter:

```python
FallbackReplIO(db_path: str, history_path: pathlib.Path)
```

It reads lines with `input()` and does not provide syntax highlighting,
multiline cursor editing, history search, or persistent history. SQL input is
still accumulated until it contains a semicolon and all parentheses, quotes,
and comments are closed, so a minimal installation can execute multiline SQL
one line at a time. Meta commands are returned immediately without requiring a
semicolon.

The fallback keeps a transient in-memory history for the current process;
`save_history()` does not write a file. The command-line entry point emits a
warning when it selects this adapter. `Ctrl-C` discards any partial SQL buffer
and returns control to the primary prompt.

## REPL state: `ReplState`

`ReplState` is a session-scoped dataclass from `tinydb._repl_meta`. Meta
commands update this object and the SQL renderer reads it for subsequent
statements.

```python
from tinydb._repl_meta import ReplState

state = ReplState(
    timer_enabled=False,
    output_format="table",
    color_enabled=True,
)
```

| Field | Type | Default | Meaning |
|---|---|---:|---|
| `timer_enabled` | `bool` | `False` | Append elapsed execution time after each successfully executed SQL statement when true. |
| `output_format` | `"table" \| "csv" \| "json"` | `"table"` | Select the renderer for row results. |
| `color_enabled` | `bool` | `True` | Session preference for interactive SQL highlighting. The initial value is set from the terminal environment by the CLI. |

A new `ReplState` is created for each `tinydb-repl` session; settings are not
persisted between sessions.

## Meta commands

The registry exposes 12 command names. `.exit` and `.quit` are two names for
the same exit operation.

| Command | Arguments | Contract |
|---|---|---|
| `.exit` | none | Exit cleanly with status 0. |
| `.quit` | none | Alias for `.exit`. |
| `.help` | none | List all meta commands and the supported keyboard shortcuts. |
| `.tables` | none | Print catalog table names, one per line. |
| `.schema` | `<name>` | Print the reverse-generated `CREATE TABLE` statement for the named table. |
| `.read` | `<path>` | Read a UTF-8 SQL script and execute each semicolon-terminated statement. |
| `.explain` | `<sql>` | Parse the SQL and print its logical plan using `plan.format_plan()`; do not execute the query or print result rows. Invalid SQL is reported as an `ERROR` line. |
| `.indexes` | optional `[table]` | List all indexes, or only indexes for the selected table. Each entry identifies `table.column` and includes the B-tree root page and an estimated key count. |
| `.stats` | none | Print `Tables`, `Rows`, `Pages`, `Free pages`, and `WAL` statistics. |
| `.timer` | `on` or `off` | Set whether successful SQL execution is followed by `Time: X.XXX ms`. The default is off. |
| `.format` | `table`, `csv`, or `json` | Select the row output format. The default is `table`. |
| `.color` | `on` or `off` | Set the session's interactive color preference. |

Commands are recognized after leading whitespace and do not require a trailing
semicolon. Unknown commands and invalid or missing arguments produce a
user-facing `ERROR` message and leave the session running.

## SQL input and output

The rich adapter accepts a statement over multiple physical lines. Input is
considered incomplete while it contains any of the following:

- An unmatched `(`.
- An unterminated single- or double-quoted string (including doubled quote
  escapes such as `''` and `""`).
- An open line comment (`--`) or block comment (`/* ... */`).
- No semicolon terminator yet.

The continuation prompt is shown until the statement is complete. An empty
line at the primary prompt is ignored. `Ctrl-C` cancels the current buffer.

Successful DDL and DML statements print `OK`. A SELECT with no rows prints
`(no rows)`. Execution failures are rendered as one `ERROR: <Class>: <message>`
line rather than a Python traceback, and do not end the session.

### Output formats

`.format` controls only row-producing SQL statements. The default is `table`.

| Format | Contract |
|---|---|
| `table` | Aligned ASCII columns with a header and separator row. |
| `csv` | RFC 4180-compatible CSV with a header row followed by data rows. |
| `json` | A JSON array containing one object per row, keyed by column name. Values that are not natively JSON serializable are rendered as strings. |

All three formats use `(no rows)` for an empty result. For example:

```text
.format csv
SELECT id, name FROM users;
id,name
1,Alice
2,Bob
```

When `.timer on` is active, the timing line is printed after the result or
`OK` line:

```text
Time: 4.213 ms
```

## Color environment variables

Color is enabled at startup unless the terminal environment opts out:

- Any non-empty `NO_COLOR` value disables syntax highlighting.
- `TERM=dumb` disables syntax highlighting.

These checks are performed before constructing `PromptToolkitReplIO`, so a
non-color terminal receives no Pygments lexer. `.color on` and `.color off`
can change the session preference when the terminal supports color; keep
`NO_COLOR` set (or `TERM=dumb`) for a process-wide no-color policy.

The stdlib fallback never emits syntax-highlighting color because it does not
load Pygments or prompt-toolkit.

## Optional dependencies and compatibility

The rich adapter uses `prompt_toolkit>=3.0.0` and `pygments>=2.18`. A minimal
installation can omit them and still use the SQL shell through the fallback
adapter. No database schema migration or CLI argument migration is required.
