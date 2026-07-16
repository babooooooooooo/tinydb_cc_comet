# tinydb (MVP)

> Minimal embedded relational database for teaching and embedding. **MVP: non-ACID, no crash safety.**

> **Status:** MVP complete — `CREATE` / `DROP` / `INSERT` / `SELECT` / `DELETE` over `INT` / `TEXT` / `FLOAT` / `BOOL`. Non-ACID, single-process. See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) for the full scope.

## Quick start

```python
import tinydb
with tinydb.Database(":memory:") as db:
    db.execute("CREATE TABLE users(id INT, name TEXT)")
    db.execute("INSERT INTO users(id, name) VALUES (1, 'alice')")
    for row in db.execute("SELECT * FROM users"):
        print(row.id, row.name)
```

Notes on the grammar shown above:

- `INSERT` requires an explicit column list (no positional shorthand).
- `WHERE` accepts only `column = literal`.
- `SELECT` columns may be `*` or a comma-separated list; multi-row results come back as immutable `Row` objects accessed by column name or unpacking.

## Run the demo

A runnable end-to-end example lives at [`examples/demo.py`](examples/demo.py) — it covers CREATE, INSERT, SELECT `*`, SELECT `WHERE`, DELETE, and SELECT after delete, against an in-memory database:

```bash
python examples/demo.py
```

## Module map (line budgets per proposal)

| Module | Budget | Responsibility |
|--------|--------|----------------|
| `type_system.py` | 150 | INT/TEXT/FLOAT/BOOL codecs |
| `pager.py` | 250 | 4KB pages, mmap/bytearray |
| `slotted_page.py` | 220 | single page layout |
| `catalog.py` | 100 | table metadata |
| `tokenizer.py` | 200 | SQL lexer |
| `parser.py` | 600 | recursive descent parser |
| `executor.py` | 400 | AST -> storage |
| `database.py` | 100 | public API |

Budgets reflect the proposal's `<= N` line ceilings. See [`docs/MVP_LIMITATIONS.md`](docs/MVP_LIMITATIONS.md) for what MVP does NOT do.