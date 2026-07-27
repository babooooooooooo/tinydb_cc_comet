# Changelog

## Unreleased

### Added

- Added the `tinydb-repl` command with multiline SQL input, including
  parenthesis, quote, comment, and semicolon-aware continuation, plus
  prompt-toolkit line editing and Pygments SQL syntax highlighting when the
  optional packages are available.
- Added 12 REPL meta-command names: `.exit`, `.quit`, `.help`, `.tables`,
  `.schema`, `.read`, `.explain`, `.indexes`, `.stats`, `.timer`, `.format`,
  and `.color`.
- Added three query output formats: aligned `table`, RFC 4180 `csv`, and
  JSON-array `json`.
- Added `.timer on|off` for query timing and `.color on|off` for the session's
  color preference.
- Added a soft fallback to the standard-library `input()` adapter when
  `prompt_toolkit` is not installed. SQL and meta commands remain available
  without syntax highlighting or advanced line editing.

### Compatibility

- `NO_COLOR` and `TERM=dumb` disable interactive syntax highlighting and ANSI
  color output.
- Existing `tinydb-repl --database PATH` usage and the `~/.tinydb_history`
  history path remain supported in rich mode.

## 0.1.1

See [`docs/superpowers/reports/2026-07-21-v0.1.1-verify.md`](docs/superpowers/reports/2026-07-21-v0.1.1-verify.md)
for the previous release changes.
