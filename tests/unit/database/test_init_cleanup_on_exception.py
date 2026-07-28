"""Unit tests for Database.__init__ cleanup when post-Pager setup raises.

Verifies the fix from design doc §T4 (tinydb-review-2026-07-28-fixes):

  ``__init__`` must explicitly close the Pager if any step *after* the
  Pager construction fails (e.g. ``Catalog.from_bytes`` blowing up on a
  corrupt page 1, an ``IndexManager`` initialization error, etc.).
  Pre-fix, the partial Database object would leave the Pager open;
  cleanup happened only via Python refcount / GC, which is not
  deterministic and may keep the OS flock held until the next GC cycle.

The tests below:

  (a) Patch ``Catalog.from_bytes`` / ``IndexManager.__init__`` to raise
      on the *first* call. Spy on ``Pager.close()`` to count calls.

  (b) Assert ``Pager.close()`` was called exactly once during the failed
      ``__init__``. Pre-fix, ``Pager.close()`` is *not* called in the
      error path — the test will fail.

  (c) Verify a second ``Database(path)`` succeeds (proves the OS flock
      was released).
"""
import pytest

from tinydb import Database


pytestmark = pytest.mark.unit


def _has_fcntl() -> bool:
    from tinydb._filelock import _HAS_FCNTL
    return _HAS_FCNTL


def test_init_cleanup_calls_pager_close_on_catalog_from_bytes_failure(
    tmp_path, monkeypatch
):
    """If Catalog.from_bytes raises, Pager.close() must be invoked.

    Strategy:
      1. Patch ``Catalog.from_bytes`` to raise ``IOError("simulated")``
         on the *first* call.
      2. Patch ``Pager.close`` to count calls (the real Pager class is
         imported lazily, so we monkey-patch the method on the class
         itself, but only record; we let the real close() still run via
         a small wrapper that delegates).
      3. Open the DB — must raise IOError.
      4. Assert the spy recorded >=1 close() call during the failed
         init. Pre-fix this is 0; post-fix this is >=1.

    Note: We patch at the *class* level rather than on the instance,
    so the spy fires for *any* Pager created in this test.
    """
    from tinydb import catalog as catalog_mod
    from tinydb import pager as pager_mod

    # Force Catalog.from_bytes to fail on first call.
    real_from_bytes = catalog_mod.Catalog.from_bytes
    first = {"v": False}

    def boom_then_normal(cls, raw):
        if not first["v"]:
            first["v"] = True
            raise IOError("simulated Catalog.from_bytes failure")
        return real_from_bytes(raw)

    monkeypatch.setattr(
        catalog_mod.Catalog, "from_bytes", classmethod(boom_then_normal)
    )

    # Spy on Pager.close() — count calls without changing semantics.
    real_close = pager_mod.Pager.close
    close_calls = {"n": 0}

    def counting_close(self):
        close_calls["n"] += 1
        return real_close(self)

    monkeypatch.setattr(pager_mod.Pager, "close", counting_close)

    db_path = str(tmp_path / "init_cleanup.tdb")

    with pytest.raises(IOError, match="simulated Catalog.from_bytes failure"):
        Database(db_path, locking=False)

    # Pager.close() must have been called at least once during cleanup.
    # Pre-fix: 0. Post-fix: 1.
    assert close_calls["n"] >= 1, (
        f"Database.__init__ did not call Pager.close() on the error path "
        f"(saw {close_calls['n']} calls). OS flock may be leaked."
    )

    # Second open must succeed (flock released).
    db2 = Database(db_path, locking=False)
    try:
        result = db2.execute("CREATE TABLE t (id INT)")
        assert result == []
    finally:
        db2.close()


def test_init_cleanup_calls_pager_close_on_index_manager_failure(
    tmp_path, monkeypatch
):
    """IndexManager() failure mid-init must also close the Pager."""
    from tinydb import index_manager as im_mod
    from tinydb import pager as pager_mod

    real_init = im_mod.IndexManager.__init__
    raised = {"v": False}

    def boom_init(self, pager):
        if not raised["v"]:
            raised["v"] = True
            raise RuntimeError("simulated IndexManager failure")
        real_init(self, pager)

    monkeypatch.setattr(im_mod.IndexManager, "__init__", boom_init)

    real_close = pager_mod.Pager.close
    close_calls = {"n": 0}

    def counting_close(self):
        close_calls["n"] += 1
        return real_close(self)

    monkeypatch.setattr(pager_mod.Pager, "close", counting_close)

    db_path = str(tmp_path / "init_im.tdb")

    with pytest.raises(RuntimeError, match="simulated IndexManager failure"):
        Database(db_path, locking=False)

    assert close_calls["n"] >= 1, (
        f"Database.__init__ did not call Pager.close() when IndexManager "
        f"failed (saw {close_calls['n']} calls)."
    )

    db2 = Database(db_path, locking=False)
    try:
        db2.execute("CREATE TABLE t (id INT)")
    finally:
        db2.close()


def test_init_cleanup_original_exception_not_masked_by_pager_close(
    tmp_path, monkeypatch
):
    """When both Catalog.from_bytes AND Pager.close() raise, the ORIGINAL
    exception (from from_bytes) must propagate.

    Design doc §4.4 / R4.2: the cleanup path must use try/finally so
    the original exception re-raises even if close() blows up.
    """
    from tinydb import catalog as catalog_mod
    from tinydb import pager as pager_mod

    real_from_bytes = catalog_mod.Catalog.from_bytes

    def boom(cls, raw):
        raise IOError("original failure")

    monkeypatch.setattr(catalog_mod.Catalog, "from_bytes", classmethod(boom))

    real_close = pager_mod.Pager.close

    def boom_close(self):
        raise RuntimeError("close() also failed")

    monkeypatch.setattr(pager_mod.Pager, "close", boom_close)

    db_path = str(tmp_path / "init_exc.tdb")

    # The user-visible exception must be the ORIGINAL one.
    with pytest.raises(IOError, match="original failure"):
        Database(db_path, locking=False)
