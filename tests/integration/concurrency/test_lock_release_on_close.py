"""Process A opens + closes; Process B opens immediately after (CC Task 5.5).

Validates that ``Database.close()`` releases the fcntl.flock so the next
process can open the file without DatabaseLocked. This is the basic
"sequential access" happy path: the lock is only held during the open
transaction, never across process boundaries once the holder has exited.

Uses the public ``open_and_close`` scenario via `_driver.run_scenario`
twice in sequence on the same path.
"""
from __future__ import annotations

import pytest

from tests.integration.concurrency._driver import run_scenario
from tinydb._filelock import _HAS_FCNTL


pytestmark = pytest.mark.integration


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_close_releases_lock_for_next_process(tmp_path):
    """Process A opens + closes; Process B opens immediately and succeeds."""
    path = str(tmp_path / "test.db")

    # Process A: open + close. The scenario returns
    # ``{"status": "closed"}`` after a clean open/close cycle. We
    # assert ok=true so a DatabaseLocked escape (e.g. some prior test
    # leaked the lock) would surface here.
    payload_a = run_scenario("open_and_close", path, timeout=10.0)
    assert payload_a["ok"], f"process A failed: {payload_a}"
    assert payload_a["result"]["status"] == "closed", (
        f"process A: expected status='closed', got {payload_a['result']!r}"
    )

    # Process B: opens immediately after A returned. If ``close()``
    # failed to release the flock (e.g. EBADF swallowed, or fcntl
    # LOCK_UN never sent), B would raise DatabaseLocked and the
    # scenario would return ``{"status": "open"}`` only on success.
    payload_b = run_scenario("open_and_close", path, timeout=10.0)
    assert payload_b["ok"], f"process B failed: {payload_b}"
    assert payload_b["result"]["status"] == "closed", (
        f"process B: expected status='closed', got {payload_b['result']!r} — "
        f"close() did not release the flock"
    )


@pytest.mark.skipif(not _HAS_FCNTL, reason="requires fcntl for cross-process flock")
def test_close_releases_lock_after_multiple_open_close_cycles(tmp_path):
    """Repeat open/close 5 times — no leaked locks across iterations."""
    path = str(tmp_path / "test.db")

    for i in range(5):
        payload = run_scenario("open_and_close", path, timeout=10.0)
        assert payload["ok"], f"iteration {i} failed: {payload}"
        assert payload["result"]["status"] == "closed", (
            f"iteration {i}: expected status='closed', got {payload['result']!r}"
        )