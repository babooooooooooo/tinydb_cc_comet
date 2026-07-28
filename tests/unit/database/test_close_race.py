"""Unit tests for Database.execute() race-safety with concurrent close().

Verifies the fix from design doc §T4 (tinydb-review-2026-07-28-fixes):

  ``_is_closed`` check must run *inside* ``self._acquire_lock()``. Pre-fix,
  the check ran outside the lock — a TOCTOU race let a thread enter the
  locked region after another thread had already called ``close()``, and
  the closed Pager would then raise an arbitrary error
  (ValueError/AttributeError/etc.) instead of the documented
  ``RuntimeError("Database is closed")``.

The test below forces the race window to be open using a ``threading.Barrier``
synchronized with a patched executor that calls ``db.close()`` from inside
the locked region. Any path through ``Database.execute()`` that touches
``self.pager`` *after* ``close()`` ran must raise ``RuntimeError("Database
is closed")`` — never a generic Pager error.
"""
import threading
import time
import pytest

from tinydb import Database


pytestmark = pytest.mark.unit


def test_close_during_execute_raises_only_runtime_error(tmp_path, monkeypatch):
    """Force the close-vs-execute race: every failed call must be RuntimeError.

    Strategy:
      1. Patch ``Database.executor.execute`` to call ``db.close()`` once
         when first invoked, then return normally. This guarantees the
         race window is exercised on the very next call (or, equivalently,
         that the close happens *while* a thread is in the locked region).
      2. Launch many threads that each call ``db.execute("SELECT 1")``
         concurrently. The threads are synchronized via a Barrier so they
         all enter the locked region roughly together.
      3. Collect every exception type raised. The only acceptable failure
         type is ``RuntimeError("Database is closed")``. Any other
         exception (ValueError, AttributeError, IOError, ...) signals
         the check happened *outside* the lock and the closed Pager was
         used.
    """
    db_path = str(tmp_path / "race.tdb")
    db = Database(db_path, locking=False)
    try:
        # Need at least one table so SELECT has something to run.
        db.execute("CREATE TABLE t (id INT)")
        db.execute("INSERT INTO t (id) VALUES (1)")

        close_done = threading.Event()
        original_executor_execute = db.executor.execute

        def executor_execute_with_close_once(stmt):
            # First call (any thread) closes the Database — the very next
            # thread that enters the locked region will see _is_closed=True
            # and must raise RuntimeError.
            try:
                return original_executor_execute(stmt)
            finally:
                if not close_done.is_set():
                    close_done.set()
                    db.close()

        monkeypatch.setattr(db.executor, "execute", executor_execute_with_close_once)

        n_threads = 16
        barrier = threading.Barrier(n_threads)
        results: list[BaseException] = []
        results_lock = threading.Lock()

        def worker():
            # Stagger a tiny bit so close is guaranteed to interleave.
            barrier.wait()
            try:
                db.execute("SELECT id FROM t")
            except BaseException as e:  # noqa: BLE001 — we inspect type/msg
                with results_lock:
                    results.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive(), "worker thread hung"

        # The non-RuntimeError exceptions would prove the pre-fix race
        # is back. We assert ONLY RuntimeError is acceptable, and that
        # its message mentions the close reason.
        for exc in results:
            assert isinstance(exc, RuntimeError), (
                f"unexpected exception type {type(exc).__name__}: {exc!r} "
                f"— _is_closed check must be inside the lock"
            )
            assert "closed" in str(exc), (
                f"RuntimeError message must mention 'closed', got: {exc!r}"
            )
    finally:
        # Idempotent close; safe even if already closed above.
        try:
            db.close()
        except Exception:
            pass


def test_execute_after_close_never_touches_pager(tmp_path):
    """Sanity: a closed Database never re-enters the Pager.

    After the fix, ``close()`` is idempotent and sets ``_is_closed=True``
    inside the lock. ``execute()`` then raises ``RuntimeError("Database
    is closed")`` *before* touching ``self.pager``. We verify by closing
    the Database first (real pager), then swapping in an ExplodingPager
    that raises on any attribute access. If the post-fix implementation
    accidentally reaches the Pager, the test fails loudly.
    """
    db = Database(str(tmp_path / "t.tdb"), locking=False)
    db.execute("CREATE TABLE t (id INT)")
    # Close while pager is real so the cleanup path runs normally.
    db.close()

    class ExplodingPager:
        def __getattr__(self, name):
            raise AssertionError(
                f"pager.{name} accessed after close() — _is_closed check "
                f"must run inside the lock before any Pager call"
            )

    db.pager = ExplodingPager()  # type: ignore[assignment]
    # Subsequent execute/explain_plan must short-circuit on _is_closed.
    with pytest.raises(RuntimeError, match="closed"):
        db.execute("SELECT id FROM t")
    with pytest.raises(RuntimeError, match="closed"):
        db.explain_plan("SELECT * FROM t")
