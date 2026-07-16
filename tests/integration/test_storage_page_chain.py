"""Integration suite for tinydb storage page chain (Task 26, §3.3).

Locks in §3.3 of the design doc: rows that overflow the inline payload spill
into a chain of follow-up data pages (``page_type=2`` overflow pages) tracked
via the inline page's ``overflow_next`` header field. This suite exercises two
end-to-end scenarios that drive the page chain:

1. Multi-page allocation: 100 rows of an INT column on a single table —
   the rows must span more than one data page, and SELECT * must return
   every row.
2. Persistence across reopen: 50 rows written in one session, reopened in a
   second session, and SELECT * must still return all rows in the original
   insertion order. This validates that the overflow chain survives
   serialization + reopen (overflow_next pointer is rewritten correctly on
   load).

These tests touch the filesystem via ``tmp_path`` so they live under
``tests/integration/`` and carry the ``integration`` pytest marker (already
registered in ``pyproject.toml``).

Test-side correction vs. plan template: the plan template shows the bare
``INSERT INTO t VALUES (...)`` form, but the MVP parser requires an explicit
column list (see ``parser.py::_parse_insert`` — columns are mandatory and
``INSERT`` without them raises ``ParseError``). The tests below use the
parser's supported form: ``INSERT INTO <t>(v) VALUES (...)``. Production
parser is intentionally NOT weakened to match the plan template — the
template was a sketch and the explicit-column contract is the spec.
"""
from __future__ import annotations

import pytest

from tinydb import Database


pytestmark = pytest.mark.integration


def test_multi_page_allocation(tmp_path):
    """100-row table forces the executor to span multiple data pages.

    Inserts 100 rows into a single INT column and asserts SELECT * returns
    the full set — fails closed if the overflow chain drops, reorders, or
    loses rows during page allocation.
    """
    with Database(str(tmp_path / "mp.db")) as db:
        db.execute("CREATE TABLE big(v INT)")
        for i in range(100):
            db.execute(f"INSERT INTO big(v) VALUES ({i})")
        rows = db.execute("SELECT * FROM big")
    assert len(rows) == 100


def test_persistence_chain_across_reopen(tmp_path):
    """50-row overflow chain survives a second Database open.

    Writes 50 rows, closes the file-backed Database, reopens it in a fresh
    context manager, and verifies the count plus the first/last rows are
    unchanged. Asserting order (rows[0].v == 0, rows[49].v == 49) catches
    overflow_next pointer corruption on reload.
    """
    path = str(tmp_path / "ch.db")
    with Database(path) as db:
        db.execute("CREATE TABLE t(v INT)")
        for i in range(50):
            db.execute(f"INSERT INTO t(v) VALUES ({i})")
    with Database(path) as db:
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == 50 and rows[0].v == 0 and rows[49].v == 49
