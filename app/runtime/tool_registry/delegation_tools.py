"""Sub-agent delegation tools."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_delegation_tools():
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


def _delegate_task(_agent=None, _run_id=None, target_name=None, message=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (("target_name", target_name), ("message", message)) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
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
