"""tinydb: minimal embedded relational database (MVP). Public API: Database, Row, errors."""
from tinydb import errors
from tinydb.database import Database, Row

__version__ = "0.1.0"

__all__ = ["Database", "Row", "errors", "__version__"]
