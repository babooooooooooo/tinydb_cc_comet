"""Performance regression: UNIQUE linear scan under 100ms for 1000 rows (R2 budget)."""
import time

import pytest

from tinydb import Database


@pytest.mark.integration
def test_unique_check_under_100ms_for_1000_rows(tmp_path):
    """Linear-scan UNIQUE check must stay under 100ms for 1000 rows (R2 mitigation)."""
    with Database(str(tmp_path / "perf.db")) as db:
        db.execute("CREATE TABLE t(id INT PRIMARY KEY, email TEXT UNIQUE)")
        # Pre-populate 1000 unique rows so subsequent UNIQUE check is O(n).
        for i in range(1000):
            db.execute(f"INSERT INTO t(id, email) VALUES ({i}, 'u{i}@x')")
        start = time.perf_counter()
        # Final INSERT triggers a full UNIQUE scan over 1000 existing rows.
        db.execute("INSERT INTO t(id, email) VALUES (1000, 'u1000@x')")
        elapsed = time.perf_counter() - start
    assert elapsed < 0.1, f"UNIQUE scan took {elapsed * 1000:.1f}ms (>100ms budget)"
