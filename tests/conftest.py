"""Top-level pytest fixtures for tinydb.

`concurrency-control` change: existing tests default to ``locking=False``
to avoid 796 baseline tests paying the per-test flock overhead. New
concurrency tests opt-in via direct ``Database(path, locking=True)``
calls or use the ``file_db`` / ``memory_db_locked`` fixtures below.
"""
from __future__ import annotations

import pytest

from tinydb.database import Database


@pytest.fixture
def file_db(tmp_path):
    """File-backed Database with locking=True (default)."""
    db = Database(str(tmp_path / "test.db"), locking=True)
    try:
        yield db
    finally:
        if not db._is_closed:
            db.close()


@pytest.fixture
def file_db_unlocked(tmp_path):
    """File-backed Database with locking=False (opt-out baseline fixture)."""
    db = Database(str(tmp_path / "test.db"), locking=False)
    try:
        yield db
    finally:
        if not db._is_closed:
            db.close()


@pytest.fixture
def memory_db_locked():
    """In-memory Database with locking=True (locks thread, no file lock)."""
    db = Database(":memory:", locking=True)
    try:
        yield db
    finally:
        if not db._is_closed:
            db.close()


@pytest.fixture
def memory_db():
    """In-memory Database with locking=False (zero-overhead baseline fixture)."""
    db = Database(":memory:", locking=False)
    try:
        yield db
    finally:
        if not db._is_closed:
            db.close()
