"""Property-based tests for storage CRUD invariants (Task 22).

Validates the operational contract of the MVP storage engine:

* ``test_scan_equals_python_mirror``: after an arbitrary INSERT/DELETE sequence,
  ``SELECT *`` matches a Python multiset that mirrors the same operations.
  This exercises create / insert / delete / scan / tombstone behaviour across
  the full data path.
* ``test_insert_then_persist_roundtrip``: after N INSERTs followed by a close
  + reopen, all N rows survive. This exercises persistence, reopen, and scan.

Both tests are hypothesis-driven; a failed invariant surfaces as a
shrunk counterexample rather than a single fixture mismatch.
"""
from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, seed, settings

import tinydb

pytestmark = pytest.mark.property


@seed(20260715)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    operations=st.lists(
        st.tuples(
            st.sampled_from(["INSERT", "DELETE"]),
            st.integers(min_value=0, max_value=1000),
            st.text(
                max_size=50,
                alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
            ),
        ),
        max_size=50,
    )
)
def test_scan_equals_python_mirror(operations):
    db = tinydb.Database(":memory:")
    db.execute("CREATE TABLE t(id INT, name TEXT)")
    # MVP storage has no UNIQUE constraint: duplicate INSERTs yield duplicate
    # rows. Model the mirror as a multiset (``dict[(id, name), count]``) so
    # duplicate keys are tracked faithfully.
    mirror: dict[tuple[int, str], int] = {}
    for op, i, name in operations:
        key = i % 100
        if op == "INSERT":
            db.execute(f"INSERT INTO t(id, name) VALUES ({key}, '{name}')")
            mirror[(key, name)] = mirror.get((key, name), 0) + 1
        else:  # DELETE WHERE id = key (hits all rows with that id, MVP)
            db.execute(f"DELETE FROM t WHERE id = {key}")
            mirror = {k: v for k, v in mirror.items() if k[0] != key}
    rows = db.execute("SELECT * FROM t")
    actual = sorted((r.id, r.name) for r in rows)
    expected: list[tuple[int, str]] = []
    for (key, name), count in mirror.items():
        expected.extend([(key, name)] * count)
    assert actual == sorted(expected)


@seed(20260715)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(n=st.integers(min_value=0, max_value=500))
def test_insert_then_persist_roundtrip(tmp_path_factory, n):
    # ``mktemp`` returns a fresh directory per call, so each example has an
    # isolated backing file even though hypothesis reuses the same fixture.
    path = str(tmp_path_factory.mktemp("prop") / "x.db")
    # Batch INSERTs (up to 250 per statement) to keep wall-clock low while
    # still exercising the same persist + reopen code path.
    BATCH = 250
    with tinydb.Database(path) as db:
        db.execute("CREATE TABLE t(v INT)")
        for start in range(0, n, BATCH):
            stop = min(start + BATCH, n)
            rows_sql = ", ".join(f"({i})" for i in range(start, stop))
            db.execute(f"INSERT INTO t(v) VALUES {rows_sql}")
    with tinydb.Database(path) as db:
        rows = db.execute("SELECT * FROM t")
    assert len(rows) == n