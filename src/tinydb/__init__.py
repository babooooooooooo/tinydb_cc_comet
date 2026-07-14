"""tinydb: minimal embedded relational database (MVP). Public API: Database, Row, errors."""
from tinydb import errors

__version__ = "0.1.0"

# Placeholder names — full implementations land in Task 20 (Database/Row).
# Exposed now so consumers can write `import tinydb; tinydb.Database` without ImportError.
class Database:
    """Placeholder. Real Database arrives in Task 20."""

    def __init__(self, *a, **kw):
        raise NotImplementedError("Database is implemented in Task 20")


class Row:
    """Placeholder. Real Row arrives in Task 20."""

    def __init__(self, *a, **kw):
        raise NotImplementedError("Row is implemented in Task 20")


__all__ = ["Database", "Row", "errors", "__version__"]