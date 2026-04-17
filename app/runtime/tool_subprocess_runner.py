"""Isolated tool runner executed by the per-workspace venv.

The Flask worker spawns this script with the agent's venv python and sends a
JSON request on stdin:

    {
      "tool_path": "<abs path to tool.py>",
      "arguments": {...},
      "agent": {"id": 1, "name": "…", "slug": "…", "workspace_path": "…"}
    }

The runner imports the tool module by file path, calls ``handler(**arguments)``
passing the serialized agent as ``_agent``, and writes a single JSON line to
stdout:

    {"ok": true,  "result": <json>}
    {"ok": false, "error": "<message>", "traceback": "<traceback>"}

This script MUST NOT import anything from the Flask app — it runs against the
workspace venv which does not have the project's deps. Only the standard
library is assumed.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path


def _load_handler(tool_path: str):
    p = Path(tool_path).resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"tool file not found: {tool_path}")
    # Use a predictable module name so if the tool imports its own siblings
    # via relative paths it still finds them on the filesystem.
    spec = importlib.util.spec_from_file_location(f"_ws_tool_{p.parent.name}", str(p))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {tool_path}")
    module = importlib.util.module_from_spec(spec)
    # Add tool directory to sys.path so the tool can do `from . import utils`
    # style local imports if it needs to.
    sys.path.insert(0, str(p.parent))
    spec.loader.exec_module(module)
    handler = getattr(module, "handler", None)
    if not callable(handler):
        raise AttributeError(f"{tool_path} has no callable 'handler'")
    return handler


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    try:
        raw = sys.stdin.read()
        request = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        _emit({"ok": False, "error": f"invalid request json: {e}"})
        return 2

    tool_path = request.get("tool_path")
    arguments = request.get("arguments") or {}
    agent = request.get("agent") or {}

    if not tool_path:
        _emit({"ok": False, "error": "missing tool_path"})
        return 2
    if not isinstance(arguments, dict):
        _emit({"ok": False, "error": "arguments must be an object"})
        return 2

    try:
        handler = _load_handler(tool_path)
    except Exception as e:
        _emit({"ok": False, "error": f"failed to load tool: {e}", "traceback": traceback.format_exc()})
        return 2

    try:
        result = handler(_agent=agent, **arguments)
    except TypeError as e:
        _emit({"ok": False, "error": f"bad arguments: {e}", "traceback": traceback.format_exc()})
        return 1
    except Exception as e:
        _emit({"ok": False, "error": str(e), "traceback": traceback.format_exc()})
        return 1

    # Force-serialize the result now so we fail loudly here rather than emitting
    # garbled output.
    try:
        json.dumps(result, default=str)
    except (TypeError, ValueError) as e:
        _emit({"ok": False, "error": f"tool returned non-JSON-serialisable value: {e}"})
        return 1

    _emit({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
