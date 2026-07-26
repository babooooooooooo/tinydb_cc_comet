"""Subprocess driver: run a scenario fn and emit JSON-serialized result.

Used by test_multiprocess_*.py tests to invoke Database operations in
fresh Python subprocesses. Result is printed as the last line on stdout
prefixed with ``RESULT:`` so the parent can parse it reliably.
"""
from __future__ import annotations

import json
import sys
import traceback


def _run(scenario_name: str, args: list, kwargs: dict) -> None:
    """Top-level entry point for ``python -m tests..._driver``."""
    # Ensure src/ on path (pytest's sys.path normally includes tests/;
    # also repo root for ``from tinydb`` to work).
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)
    src = os.path.join(root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    from tests.integration.concurrency import _scenarios

    fn = _scenarios.SCENARIOS[scenario_name]
    try:
        result = fn(*args, **kwargs)
        print("RESULT:" + json.dumps({"ok": True, "result": result}))
    except Exception as e:
        print("RESULT:" + json.dumps({
            "ok": False,
            "type": type(e).__name__,
            "msg": str(e),
            "traceback": traceback.format_exc(),
        }))
    finally:
        sys.stdout.flush()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--kwargs", default="{}")
    ns = ap.parse_args()
    _run(ns.scenario, list(ns.args), json.loads(ns.kwargs))
