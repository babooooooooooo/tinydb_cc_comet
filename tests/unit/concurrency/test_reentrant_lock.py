"""可重入 RLock 不死锁 (Task 6.5).

Each test holds the per-instance RLock via an OUTER ``with
db._acquire_lock():`` scope and calls ``db.execute()`` /
``db.explain_plan()`` from inside. Those calls internally do
``with self._acquire_lock():`` (a reentrant acquire from the SAME
thread). With RLock: reentrant acquire completes immediately.
Without (non-reentrant Lock): the inner acquire would block forever;
we use a worker thread + ``join(timeout=...)`` so deadlock fails the
test rather than hanging it.

Negative-test verification: monkey-patch ``db_mod.RLock`` to a
non-reentrant ``threading.Lock`` and re-run; the worker thread
will not complete within the timeout, surfacing the deadlock.
"""
import threading
import pytest

from tinydb.database import Database


@pytest.mark.integration
def test_execute_inside_execute_does_not_deadlock(tmp_path):
    """``db.execute`` inside an outer-locked scope completes (RLock reentrant).

    Outer: ``with db._acquire_lock():`` holds the RLock from the same
    thread that then runs ``db.execute(...)``. The body of
    ``Database.execute`` does its own ``with self._acquire_lock():`` —
    that's a reentrant acquire. With RLock the second acquire returns
    immediately; with a non-reentrant ``threading.Lock`` the second
    acquire would block forever.
    """
    db = Database(str(tmp_path / "a.db"))
    try:
        outcome = {}

        def worker():
            try:
                # Outer lock scope (RLock count → 1). Both db.execute
                # calls below internally re-enter the RLock (count → 2).
                with db._acquire_lock():
                    db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
                    db.execute("INSERT INTO t(id, v) VALUES (1, 100)")
                    db.execute("INSERT INTO t(id, v) VALUES (2, 200)")
                outcome["ok"] = True
            except Exception as exc:  # noqa: BLE001
                outcome["err"] = exc

        t = threading.Thread(target=worker)
        t.start()
        # Generous timeout: with a healthy RLock this returns in <100ms.
        # With non-reentrant Lock the inner acquire deadlocks and we time
        # out here, surfacing the failure cleanly instead of hanging pytest.
        t.join(timeout=10.0)

        if t.is_alive():
            raise AssertionError(
                "RLock not reentrant: nested db.execute deadlocked (10s timeout)"
            )

        assert outcome.get("ok"), (
            f"reentrant execute failed: {outcome.get('err')!r}"
        )

        rows = db.execute("SELECT * FROM t")
        assert len(rows) == 2
        ids = sorted(int(r.values[0]) for r in rows)
        assert ids == [1, 2]
    finally:
        db.close()


@pytest.mark.integration
def test_explain_plan_inside_execute_is_reentrant(tmp_path):
    """``explain_plan`` inside an outer-locked scope completes (RLock reentrant).

    Same pattern as the execute test: outer ``with db._acquire_lock():``
    holds the RLock, then ``db.explain_plan(...)`` is called from inside.
    ``explain_plan`` internally does ``with self._acquire_lock():`` which
    must acquire the RLock reentrantly.
    """
    db = Database(str(tmp_path / "a.db"))
    try:
        db.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        db.execute("INSERT INTO t(id, v) VALUES (1, 100)")

        outcome = {}

        def worker():
            try:
                with db._acquire_lock():
                    plan = db.explain_plan("SELECT * FROM t")
                    outcome["plan"] = plan
                outcome["ok"] = True
            except Exception as exc:  # noqa: BLE001
                outcome["err"] = exc

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=10.0)

        if t.is_alive():
            raise AssertionError(
                "RLock not reentrant: nested db.explain_plan deadlocked (10s timeout)"
            )

        assert outcome.get("ok"), (
            f"reentrant explain_plan failed: {outcome.get('err')!r}"
        )
        # And the plan returned a real LogicalPlan (Project(...) — truthy)
        assert outcome["plan"] is not None
    finally:
        db.close()
