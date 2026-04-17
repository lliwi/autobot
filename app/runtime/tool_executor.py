import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models.tool import Tool
from app.models.tool_execution import ToolExecution
from app.runtime.tool_registry import get as get_tool

logger = logging.getLogger(__name__)


def execute(run_id, agent, tool_name, arguments):
    """Execute a tool and record the execution.

    Built-in tools run in-process against the Flask app. Workspace tools run
    as subprocesses inside the agent's per-workspace venv so they can import
    packages the agent has installed without contaminating the Flask env.
    """
    tool_def = get_tool(tool_name)
    if tool_def is None:
        return _execute_workspace_tool(run_id, agent, tool_name, arguments)

    execution = _new_execution(run_id, agent, tool_name, arguments)
    try:
        result = tool_def.handler(_agent=agent, _run_id=run_id, **arguments)
        execution.output_json = result
        execution.status = "success" if not (isinstance(result, dict) and result.get("error")) else "error"
    except Exception as e:
        current_app.logger.error(f"Tool execution error: {tool_name}: {e}")
        execution.output_json = {"error": str(e)}
        execution.status = "error"
    finally:
        execution.finished_at = datetime.now(timezone.utc)
        db.session.commit()
    return execution.output_json


def _new_execution(run_id, agent, tool_name, arguments) -> ToolExecution:
    ex = ToolExecution(
        run_id=run_id,
        agent_id=agent.id,
        tool_name=tool_name,
        input_json=arguments,
        status="running",
    )
    db.session.add(ex)
    db.session.commit()
    return ex


def _execute_workspace_tool(run_id, agent, tool_name, arguments):
    tool = Tool.query.filter_by(agent_id=agent.id, slug=tool_name, enabled=True).first()
    if tool is None:
        tool = Tool.query.filter_by(agent_id=agent.id, name=tool_name, enabled=True).first()
    if tool is None:
        return {"error": f"Unknown tool: {tool_name}"}

    workspace = Path(agent.workspace_path).resolve()
    tool_py = (workspace / tool.path / "tool.py").resolve()
    try:
        tool_py.relative_to(workspace)
    except ValueError:
        logger.error("Path traversal attempt: %s", tool_py)
        return {"error": "tool path escapes workspace"}
    if not tool_py.exists():
        return {"error": f"tool.py missing at {tool.path}"}

    execution = _new_execution(run_id, agent, tool_name, arguments)
    try:
        result = _run_in_venv(agent, tool, tool_py, arguments)
        execution.output_json = result
        execution.status = "success" if not (isinstance(result, dict) and result.get("error")) else "error"
    except Exception as e:
        current_app.logger.exception("Workspace tool crashed: %s", tool_name)
        execution.output_json = {"error": str(e)}
        execution.status = "error"
    finally:
        execution.finished_at = datetime.now(timezone.utc)
        db.session.commit()
    return execution.output_json


def _run_in_venv(agent, tool, tool_py: Path, arguments: dict) -> dict:
    """Spawn the subprocess runner under the agent's venv and return its result."""
    from app.services import venv_manager

    py = venv_manager.ensure_venv(agent)
    runner = venv_manager.default_tool_runner()

    timeout = int(getattr(tool, "timeout", None) or 0) or int(
        current_app.config.get("WORKSPACE_TOOL_TIMEOUT_SECONDS", 30)
    )

    request = {
        "tool_path": str(tool_py),
        "arguments": arguments or {},
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "slug": agent.slug,
            "workspace_path": str(Path(agent.workspace_path).resolve()),
        },
    }

    env = _subprocess_env()

    try:
        proc = subprocess.run(
            [str(py), runner],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"tool timed out after {timeout}s"}

    stdout = (proc.stdout or "").strip()
    if not stdout:
        stderr_tail = (proc.stderr or "")[-500:]
        return {"error": f"tool produced no output (exit {proc.returncode}). stderr: {stderr_tail}"}

    # The runner prints a single JSON line on the last non-empty line.
    last_line = stdout.splitlines()[-1]
    try:
        envelope = json.loads(last_line)
    except json.JSONDecodeError:
        return {"error": f"tool output was not JSON: {last_line[:200]}"}

    if not envelope.get("ok"):
        msg = envelope.get("error") or "tool failed"
        tb = envelope.get("traceback")
        logger.warning("Workspace tool %s failed: %s\n%s", tool.slug, msg, tb)
        return {"error": msg}

    return envelope.get("result")


def _subprocess_env() -> dict:
    keep = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy",
            "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env
