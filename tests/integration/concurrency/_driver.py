"""Subprocess driver: run a scenario fn and emit JSON-serialized result."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _decode(value: str, converter):
    if converter is str:
        return value
    return converter(value)


def _run(scenario_name: str, args: list, kwargs: dict) -> None:
    """Top-level entry point for ``python -m tests..._driver``."""
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    src = os.path.join(root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    try:
        from tests.integration.concurrency import _scenarios

        if scenario_name not in _scenarios.SCENARIOS:
            raise KeyError(f"unknown scenario: {scenario_name}")
        fn = _scenarios.SCENARIOS[scenario_name]
        meta = _scenarios.SCENARIOS_META.get(scenario_name, {})
        specs = meta.get("args", [])
        if len(args) != len(specs) + int(meta.get("needs_db", False)):
            raise TypeError(f"{scenario_name} expects {len(specs) + int(meta.get('needs_db', False))} arguments, got {len(args)}")
        converted = []
        if meta.get("needs_db"):
            from tinydb import Database
            converted.append(Database(args[0]))
            args = args[1:]
        converted.extend(_decode(value, converter) for value, (_, converter) in zip(args, specs))
        result = fn(*converted, **kwargs)
        print("RESULT:" + json.dumps({"ok": True, "result": result}))
    except Exception as exc:
        print("RESULT:" + json.dumps({
            "ok": False, "type": type(exc).__name__, "msg": str(exc),
            "traceback": traceback.format_exc(),
        }))
    finally:
        sys.stdout.flush()


def run_scenario(scenario_name: str, *args, timeout: float = 30.0, **kwargs) -> dict:
    """Launch a driver subprocess and return its decoded RESULT envelope."""
    cmd = [sys.executable, "-m", "tests.integration.concurrency._driver", scenario_name]
    cmd.extend(str(arg) if isinstance(arg, str) else json.dumps(arg) for arg in args)
    if kwargs:
        cmd.extend(["--kwargs", json.dumps(kwargs)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_repo_root())
    result_line = next((line[len("RESULT:"):] for line in proc.stdout.splitlines() if line.startswith("RESULT:")), None)
    if result_line is None:
        return {"ok": False, "type": "ProtocolError", "msg": "no RESULT line", "stdout": proc.stdout, "stderr": proc.stderr}
    try:
        return json.loads(result_line)
    except json.JSONDecodeError as exc:
        return {"ok": False, "type": "ProtocolError", "msg": str(exc), "stdout": proc.stdout, "stderr": proc.stderr}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--kwargs", default="{}")
    ns = ap.parse_args()
    _run(ns.scenario, list(ns.args), json.loads(ns.kwargs))
