"""Sandboxed bash execution inside the agent's workspace."""
from app.runtime.tool_registry.core import ToolDefinition, register

_BASH_OUTPUT_LIMIT = 20000


def register_bash_tools():
    register(
        ToolDefinition(
            name="run_bash",
            description=(
                "Run a shell command inside the agent's workspace. Use this to execute "
                "`.sh` scripts stored under the workspace, run quick one-liners, or chain "
                "CLI tools. The command always runs with cwd set inside the workspace "
                "(optionally a subdirectory via `workdir`). The agent's per-workspace "
                "venv, if one exists, is prepended to PATH so packages installed through "
                "`install_package` are importable. Provide EITHER `command` (one-liner "
                "evaluated with `bash -c`) OR `script` (multi-line bash body, wrapped "
                "with `set -euo pipefail`). Output is truncated to ~20k characters."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell one-liner. Mutually exclusive with `script`.",
                    },
                    "script": {
                        "type": "string",
                        "description": "Multi-line bash body. Mutually exclusive with `command`.",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Working directory relative to the workspace root. Defaults to the workspace root.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Hard timeout in seconds (1..600). Defaults to 30.",
                    },
                },
            },
            handler=lambda **kwargs: _run_bash(**kwargs),
        )
    )


def _run_bash(_agent=None, _run_id=None, command=None, script=None,
              workdir=None, timeout=30, **kwargs):
    """Run bash inside the agent's workspace, with path and time containment.

    The subprocess inherits the worker's env (so outbound HTTP/DNS work) but
    has cwd pinned inside ``workspace_path``. We also prepend the per-workspace
    venv's bin dir to PATH when present so the agent can call packages it
    installed via ``install_package`` without activating the venv by hand.

    One-line commands intentionally use ``bash -c`` instead of ``bash -lc``.
    A login shell may reinitialize PATH from profile files and drop the venv
    prefix, which makes ``python`` resolve to the system interpreter even when
    ``venv_active`` is true.
    """
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    if _agent is None:
        return {"error": "No agent context"}

    has_cmd = bool(command)
    has_script = bool(script)
    if has_cmd == has_script:
        return {"error": "provide exactly one of 'command' or 'script'"}

    try:
        timeout = int(timeout) if timeout is not None else 30
    except (TypeError, ValueError):
        return {"error": "timeout must be an integer"}
    if timeout < 1 or timeout > 600:
        return {"error": "timeout must be in 1..600 seconds"}

    workspace_root = Path(_agent.workspace_path).resolve()
    if not workspace_root.is_dir():
        return {"error": f"workspace not found at {workspace_root}"}

    # Resolve workdir relative to the workspace, reject escape attempts.
    rel = (workdir or ".").strip()
    if os.path.isabs(rel):
        return {"error": "workdir must be relative to the workspace root"}
    cwd = (workspace_root / rel).resolve()
    try:
        cwd.relative_to(workspace_root)
    except ValueError:
        return {"error": "workdir escapes the workspace"}
    if not cwd.is_dir():
        return {"error": f"workdir does not exist: {rel}"}

    env = os.environ.copy()
    venv_bin = None
    for candidate in (workspace_root / ".venv" / "bin",
                      workspace_root / "venv" / "bin"):
        if (candidate / "python").exists():
            venv_bin = candidate
            break
    if venv_bin is not None:
        env["VIRTUAL_ENV"] = str(venv_bin.parent)
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"

    def _trim(text):
        text = text or ""
        if len(text) <= _BASH_OUTPUT_LIMIT:
            return text
        return text[-_BASH_OUTPUT_LIMIT:] + "\n[truncated]"

    temp_path = None
    try:
        if has_script:
            fd, temp_path = tempfile.mkstemp(
                suffix=".sh", dir=str(workspace_root), text=True,
            )
            with os.fdopen(fd, "w") as f:
                f.write("#!/usr/bin/env bash\nset -euo pipefail\n")
                f.write(script)
            os.chmod(temp_path, 0o700)
            argv = ["bash", temp_path]
        else:
            argv = ["bash", "-c", command]

        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": _trim(proc.stdout),
            "stderr": _trim(proc.stderr),
            "venv_active": venv_bin is not None,
            "cwd": str(cwd.relative_to(workspace_root)) or ".",
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "error": "timeout",
            "timeout": timeout,
            "stdout": _trim(e.stdout if isinstance(e.stdout, str) else ""),
            "stderr": _trim(e.stderr if isinstance(e.stderr, str) else ""),
        }
    except FileNotFoundError:
        return {"error": "bash not available in this process"}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
