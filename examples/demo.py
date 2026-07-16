"""End-to-end tinydb MVP demo: CREATE -> INSERT -> SELECT WHERE -> DELETE -> SELECT.

Run from the repo root:

    python3 examples/demo.py       # or `python` on systems where that alias is python3

Demonstrates the full SQL surface (excluding DROP, which behaves the same as
DELETE from the catalog's point of view) against an in-memory database.
"""
import tinydb


def main() -> None:
    with tinydb.Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, name TEXT, active BOOL)")
        db.execute("INSERT INTO users(id, name, active) VALUES (1, 'alice', TRUE)")
        db.execute("INSERT INTO users(id, name, active) VALUES (2, 'bob', FALSE)")
        db.execute("INSERT INTO users(id, name, active) VALUES (3, 'carol', TRUE)")

        print("All users:")
        for row in db.execute("SELECT * FROM users"):
            print(" ", repr(row))

        print("\nActive users:")
        for row in db.execute("SELECT * FROM users WHERE active = TRUE"):
            print(" ", repr(row))

        db.execute("DELETE FROM users WHERE id = 2")

        print("\nAfter deleting id=2:")
        for row in db.execute("SELECT * FROM users"):
            print(" ", repr(row))


if __name__ == "__main__":
    main()