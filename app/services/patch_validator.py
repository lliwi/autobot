"""Static + smoke-test validations for a proposed patch.

Runs BEFORE a patch is applied to the workspace. The goal is to catch the
cheap-to-detect failures (bad JSON, syntax errors, missing entry points) so
a broken skill/tool never ends up registered. A patch that fails validation
is marked ``rejected`` and its ``test_result_json`` carries the failure
details for the dashboard.

Kept deliberately conservative: we only block on problems we can prove. A
warning-class issue (unused import, style) is never a blocker — reviewer
agents / humans are the right place for that.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def validate_patch(target_path: str, new_content: str, *, workspace_root: Path | None = None) -> dict:
    """Validate a single proposed file change.

    Returns a dict:
      {
        "ok": bool,
        "checks": [ {"name": str, "ok": bool, "detail": str|None}, ... ],
        "error": str|None,   # short summary of the first failed check
      }

    ``workspace_root`` is used for the smoke-import check (a fresh Python is
    spawned with its cwd at the workspace root, so relative imports behave
    the same as at runtime). If omitted, smoke-import is skipped.
    """
    checks: list[dict] = []

    path = target_path.strip("/")
    ext = os.path.splitext(path)[1].lower()
    basename = os.path.basename(path)

    # --- JSON files --------------------------------------------------------
    if ext == ".json":
        ok, detail = _check_json(new_content)
        checks.append({"name": "json_parse", "ok": ok, "detail": detail})
        if ok and basename == "manifest.json":
            m_ok, m_detail = _check_manifest(path, new_content)
            checks.append({"name": "manifest_shape", "ok": m_ok, "detail": m_detail})

    # --- Python files ------------------------------------------------------
    elif ext == ".py":
        ok, detail = _check_python_syntax(new_content)
        checks.append({"name": "python_syntax", "ok": ok, "detail": detail})

        # Only gate on entry-point presence when the file is one of the
        # structural ones we know the runtime will import by name.
        if ok and path.startswith("tools/") and basename == "tool.py":
            h_ok, h_detail = _check_tool_handler(new_content)
            checks.append({"name": "tool_handler_present", "ok": h_ok, "detail": h_detail})

        if ok and workspace_root is not None:
            s_ok, s_detail = _smoke_import(workspace_root, path, new_content)
            checks.append({"name": "smoke_import", "ok": s_ok, "detail": s_detail})

    # --- Markdown / other --------------------------------------------------
    else:
        # No blocking checks for .md or unknown extensions — they are data,
        # not code. Just record that we looked.
        checks.append({
            "name": "no_static_checks_for_type",
            "ok": True,
            "detail": f"extension {ext or '(none)'} has no blocking validator",
        })

    failed = [c for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "checks": checks,
        "error": failed[0].get("detail") if failed else None,
    }


def _check_json(text: str) -> tuple[bool, str | None]:
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {e.msg} at line {e.lineno} col {e.colno}"
    return True, None


def _check_manifest(path: str, text: str) -> tuple[bool, str | None]:
    data = json.loads(text)
    if not isinstance(data, dict):
        return False, "manifest.json must be a JSON object"
    missing = [k for k in ("name", "description") if not data.get(k)]
    if missing:
        return False, f"manifest missing required field(s): {', '.join(missing)}"
    if path.startswith("tools/"):
        params = data.get("parameters")
        if params is not None and not isinstance(params, dict):
            return False, "tool manifest 'parameters' must be an object"
    return True, None


def _check_python_syntax(text: str) -> tuple[bool, str | None]:
    try:
        ast.parse(text)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} at line {e.lineno}"
    return True, None


def _check_tool_handler(text: str) -> tuple[bool, str | None]:
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return False, f"cannot parse to check handler: {e.msg}"
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "handler":
            return True, None
    return False, "tool.py must define a top-level `def handler(...)`"


# Smoke-import runs in a subprocess so a buggy top-level statement can't
# poison the web/worker process. We only import the module — we don't call
# any handler code, so side-effecting tools are safe.
_SMOKE_TIMEOUT = 10


def _smoke_import(workspace_root: Path, rel_path: str, content: str) -> tuple[bool, str | None]:
    """Write ``content`` to a tempfile and try to import it with a fresh Python.

    The spawn uses the same PATH/venv the subprocess tool runner uses, so
    third-party imports installed via ``install_package`` are visible.
    """
    # Use the workspace venv if it exists (same logic as run_bash/tool_executor).
    py = None
    for cand in (workspace_root / ".venv" / "bin" / "python",
                 workspace_root / "venv" / "bin" / "python"):
        if cand.exists():
            py = str(cand)
            break
    if py is None:
        py = sys.executable

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", dir=str(workspace_root), delete=False, encoding="utf-8"
    ) as fh:
        fh.write(content)
        tmp = fh.name

    try:
        proc = subprocess.run(
            [py, "-c",
             "import importlib.util, sys; "
             f"spec = importlib.util.spec_from_file_location('_patch_smoke', {tmp!r}); "
             "mod = importlib.util.module_from_spec(spec); "
             "spec.loader.exec_module(mod)"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=_SMOKE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"smoke-import hung for >{_SMOKE_TIMEOUT}s"
    except FileNotFoundError:
        return True, "python interpreter not available — skipped"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if proc.returncode == 0:
        return True, None
    # Show only the last traceback line to keep the UI readable.
    tail = (proc.stderr or "").strip().splitlines()
    detail = tail[-1] if tail else f"exit {proc.returncode}"
    return False, f"import failed: {detail}"
