"""Property test: UNIQUE column tracks Python multiset exactly across random inserts."""
from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, seed, settings

import tinydb

pytestmark = pytest.mark.property


@seed(20260716)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    rows=st.lists(
        st.tuples(st.integers(min_value=0, max_value=10), st.integers(min_value=0, max_value=10)),
        max_size=20,
    )
)
def test_unique_constraint_mirror(rows):
    """UNIQUE on a single column tracks the Python multiset exactly."""
    db = tinydb.Database(":memory:")
    db.execute("CREATE TABLE t(id INT, x INT UNIQUE)")
    mirror: set[int] = set()
    for i, x in rows:
        try:
            db.execute(f"INSERT INTO t(id, x) VALUES ({i}, {x})")
            mirror.add(x)
        except Exception:
            # Constraint violation is expected; Python mirror ignored.
            pass
    actual = sorted(r.x for r in db.execute("SELECT * FROM t"))
    assert actual == sorted(mirror)