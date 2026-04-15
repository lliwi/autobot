from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    handler: Callable


_registry: dict[str, ToolDefinition] = {}


def register(tool_def: ToolDefinition):
    _registry[tool_def.name] = tool_def


def get(name: str) -> ToolDefinition | None:
    return _registry.get(name)


def get_all_definitions() -> list[dict]:
    """Return tools in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }
        for td in _registry.values()
    ]


def register_builtin_tools():
    """Register the built-in tools that ship with Phase 1."""
    from datetime import datetime, timezone

    register(
        ToolDefinition(
            name="read_workspace_file",
            description="Read a file from the agent's workspace.",
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

    register(
        ToolDefinition(
            name="delegate_task",
            description="Delegate a task to a sub-agent. The sub-agent will execute the task and return the result.",
            parameters={
                "type": "object",
                "properties": {
                    "target_name": {
                        "type": "string",
                        "description": "Name or slug of the sub-agent to delegate to.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The task/message to send to the sub-agent.",
                    },
                },
                "required": ["target_name", "message"],
            },
            handler=lambda **kwargs: _delegate_task(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_subagents",
            description="List available sub-agents that can receive delegated tasks.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_subagents(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="propose_change",
            description=(
                "Propose a change to a workspace file. For level-1 targets (MEMORY.md, "
                "new skills/tools) the change is auto-applied. For level-2 targets "
                "(existing code, AGENTS.md, TOOLS.md) it requires admin approval. "
                "Level-3 targets (core, OAuth, DB) are prohibited."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "Relative path within the workspace (e.g. 'MEMORY.md', 'skills/my-skill/skill.py').",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The complete new content for the file.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title describing the change.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this change is needed.",
                    },
                },
                "required": ["target_path", "new_content", "title", "reason"],
            },
            handler=lambda **kwargs: _propose_change(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_patches",
            description="List recent patch proposals for this agent, optionally filtered by status.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status: pending_review, approved, applied, rejected, rolled_back.",
                    },
                },
            },
            handler=lambda **kwargs: _list_patches(**kwargs),
        )
    )


def _read_workspace_file(filename, _agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.workspace.manager import read_file

    content = read_file(_agent, filename)
    return {"filename": filename, "content": content}


def _list_workspace_files(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.workspace.manager import list_files

    files = list_files(_agent)
    return {"files": files}


def _delegate_task(target_name, message, _agent=None, _run_id=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services.subagent_service import delegate_task_by_name

    return delegate_task_by_name(_agent.id, target_name, message, parent_run_id=_run_id)


def _list_subagents(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services.subagent_service import list_subagents

    subagents = list_subagents(_agent.id)
    return {
        "subagents": [
            {"id": a.id, "name": a.name, "slug": a.slug, "status": a.status}
            for a in subagents
        ]
    }


def _propose_change(target_path, new_content, title, reason, _agent=None, _run_id=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services.patch_service import propose_change

    try:
        patch = propose_change(
            agent_id=_agent.id,
            target_path=target_path,
            new_content=new_content,
            title=title,
            reason=reason,
            run_id=_run_id,
        )
        return {
            "patch_id": patch.id,
            "status": patch.status,
            "security_level": patch.security_level,
            "target_path": patch.target_path,
            "message": (
                "Change auto-applied." if patch.status == "applied"
                else "Change queued for admin review." if patch.status == "pending_review"
                else f"Change rejected: {(patch.test_result_json or {}).get('error', 'unknown')}"
            ),
        }
    except ValueError as e:
        return {"error": str(e)}


def _list_patches(_agent=None, status=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services.patch_service import list_patches

    patches = list_patches(agent_id=_agent.id, status=status)
    return {
        "patches": [
            {
                "id": p.id,
                "title": p.title,
                "target_path": p.target_path,
                "status": p.status,
                "security_level": p.security_level,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in patches[:20]
        ]
    }
