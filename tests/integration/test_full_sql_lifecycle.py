"""Integration suite for the full SQL lifecycle (Task 27, Design Doc §7 + §9).

Locks in §9 Spec Patch: a single Database file can be driven through the
complete CREATE -> INSERT x N -> SELECT WHERE -> DELETE -> reopen -> SELECT
arc, with persistence preserved across context-manager exits and BOOL
columns filterable via ``WHERE <bool_col> = TRUE``.

Coverage:
* Full lifecycle on one ``tmp_path`` file: CREATE TABLE, three INSERTs
  (including a FALSE BOOL row), a BOOL-filtered SELECT that excludes the
  FALSE row, and a WHERE-int DELETE of one survivor, then a second
  ``Database(path)`` reopen that confirms the persisted state on disk.
* Parser contract: the plan template shows bare ``INSERT INTO t VALUES (...)``,
  but the MVP parser requires an explicit column list (see
  ``parser.py::_parse_insert`` — columns are mandatory and bare INSERTs raise
  ``ParseError``). These tests use the parser's supported form:
  ``INSERT INTO users(id, name, active) VALUES (...)``. Production parser is
  intentionally NOT weakened to match the plan template.
* BOOL semantics: ``WHERE active = TRUE`` is the first end-to-end integration
  exercise of BOOL WHERE; the executor's ``_resolve_where`` returns the
  literal as a Python value and the column comparison falls through to
  Python-level ``==`` (validated against the BOOL E2E SQL
  ``tests/e2e/sql/happy_path/09_bool_column.sql``). If this test fails on
  the BOOL line, the executor — not the test — is the bug, and the
  implementer must load ``systematic-debugging`` before proposing fixes.

These tests open a real file-backed ``Pager`` via ``tmp_path`` and drive
SQL through the public ``Database`` context manager, so they live under
``tests/integration/`` and carry the ``integration`` pytest marker (already
registered in ``pyproject.toml``).
"""
from __future__ import annotations

import pytest

from tinydb import Database

pytestmark = pytest.mark.integration


def test_full_lifecycle_create_insert_select_delete_reopen(tmp_path):
    """End-to-end SQL lifecycle: CREATE -> INSERT x3 -> SELECT WHERE BOOL
    -> DELETE WHERE id -> reopen -> SELECT, with persistence surviving the
    context-manager exit and the reopened Database seeing the post-DELETE
    rows only.
    """
    path = str(tmp_path / "life.db")
    with Database(path) as db:
        db.execute("CREATE TABLE users(id INT, name TEXT, active BOOL)")
        db.execute("INSERT INTO users(id, name, active) VALUES (1, 'alice', TRUE)")
        db.execute("INSERT INTO users(id, name, active) VALUES (2, 'bob', FALSE)")
        db.execute("INSERT INTO users(id, name, active) VALUES (3, 'carol', TRUE)")
        rows = db.execute("SELECT * FROM users WHERE active = TRUE")
        assert sorted(r.name for r in rows) == ["alice", "carol"]
        db.execute("DELETE FROM users WHERE id = 2")
    with Database(path) as db:
        rows = db.execute("SELECT * FROM users")
    # After deleting id=2, only alice (1) and carol (3) must persist on
    # reopen — bob's row is gone and the disk round-trip preserved it.
    assert sorted(r.id for r in rows) == [1, 3]
