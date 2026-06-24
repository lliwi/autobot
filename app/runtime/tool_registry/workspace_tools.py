"""Workspace file access + the current-time helper.

Also owns the per-run read cache used to dedupe repeated ``read_workspace_file``
calls within a single turn.
"""
from datetime import datetime, timezone

from app.runtime.tool_registry.core import ToolDefinition, register

# Per-run cache of files already served by ``read_workspace_file``. Lets us
# return a cheap stub on the second read within the same turn instead of
# re-serializing the file into the model's context. Entries live for the run
# and are cleaned up by ``forget_run_reads`` at the end.
_RUN_READ_CACHE: dict[int, set[str]] = {}


def forget_run_reads(run_id: int | None) -> None:
    """Drop the per-run read cache once a run completes."""
    if run_id is None:
        return
    _RUN_READ_CACHE.pop(run_id, None)


def register_workspace_tools():
    register(
        ToolDefinition(
            name="read_workspace_file",
            description=(
                "Read a file from the agent's workspace. Path is relative to the workspace root. "
                "Reference docs (TOOLS.md, AGENTS.md, skills/<slug>/SKILL.md) are NOT pre-loaded "
                "into context — use this tool to fetch them when the current task needs them. "
                "The same file is cached per-run: re-reading it in the same turn returns a stub "
                "so you don't pay the tokens twice."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Path to the file relative to the workspace root."}
                },
                "required": ["filename"],
            },
            handler=lambda **kwargs: _read_workspace_file(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_workspace_files",
            description="List all files in the agent's workspace.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_workspace_files(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="get_current_time",
            description="Get the current date and time in UTC.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: {"time": datetime.now(timezone.utc).isoformat()},
        )
    )


def _read_workspace_file(_agent=None, _run_id=None, filename=None, path=None, file=None, name=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    # Accept common aliases the model might emit.
    filename = filename or path or file or name
    from app.workspace.manager import get_global_skills_path, list_files, read_file

    if not filename:
        available = list_files(_agent)
        return {
            "error": "Missing required argument 'filename'.",
            "hint": "Call this tool again with filename set to one of the entries in 'available_files'.",
            "available_files": available,
        }

    available = list_files(_agent)
    content = None

    if filename in available:
        content = read_file(_agent, filename)
    elif filename.startswith("skills/"):
        # Skills are now global — fall back to _global/skills/
        global_file = get_global_skills_path().parent / filename
        if global_file.exists():
            content = global_file.read_text(encoding="utf-8")

    if content is None:
        return {
            "error": f"File '{filename}' not found in workspace.",
            "available_files": available,
        }

    # Dedup within the same run: the model already has the content in its
    # context from the earlier read, so returning it again is pure waste.
    if _run_id is not None:
        seen = _RUN_READ_CACHE.setdefault(_run_id, set())
        if filename in seen:
            return {
                "filename": filename,
                "cached": True,
                "note": (
                    "Already read earlier in this run — the full content is"
                    " in your context above. Do not re-request unless the"
                    " file may have changed (you proposed a patch to it)."
                ),
            }
        seen.add(filename)

    return {"filename": filename, "content": content}


def _list_workspace_files(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.workspace.manager import list_files

    files = list_files(_agent)
    return {"files": files}
