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
