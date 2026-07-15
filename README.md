# tinydb (MVP)

> Minimal embedded relational database for teaching and embedding. **MVP: non-ACID, no crash safety.**

> **Status:** package scaffold only. The `Database`/`Row` placeholders raise `NotImplementedError` until Task 20.

## Quick start
```python
import tinydb
with tinydb.Database(":memory:") as db:
    db.execute("CREATE TABLE users(id INT, name TEXT)")
    db.execute("INSERT INTO users VALUES (1, 'alice')")
    for row in db.execute("SELECT * FROM users"):
        print(row.id, row.name)
```

## Module map (line budgets per proposal Impact)
| Module | Budget | Responsibility |
|--------|--------|----------------|
| `type_system.py` | 150 | INT/TEXT/FLOAT/BOOL codecs |
| `pager.py` | 250 | 4KB pages, mmap/bytearray |
| `slotted_page.py` | 150 | single page layout |
| `catalog.py` | 100 | table metadata |
| `tokenizer.py` | 200 | SQL lexer |
| `parser.py` | 600 | recursive descent parser |
| `executor.py` | 400 | AST -> storage |
| `database.py` | 100 | public API |

See `docs/MVP_LIMITATIONS.md` for what MVP does NOT do.
