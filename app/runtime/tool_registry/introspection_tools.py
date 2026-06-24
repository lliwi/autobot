"""Read-only introspection over the agent's own execution history and patches."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_introspection_tools():
    register(
        ToolDefinition(
            name="list_runs",
            description=(
                "Inspect THIS agent's recent execution history (the run log) to "
                "self-diagnose. Each run is one invocation — manual (message), "
                "scheduler (cron/heartbeat), delegation or review — with status, "
                "duration and any error. To find what failed, pass status='error'; "
                "to audit scheduled tasks, pass trigger_type='cron'. Use scope='all' "
                "to look across every agent (system-wide view)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status: running, completed, error.",
                    },
                    "trigger_type": {
                        "type": "string",
                        "description": "Filter by trigger: message, cron, heartbeat, delegation, auto_review.",
                    },
                    "scope": {
                        "type": "string",
                        "description": "'own' (default, this agent only) or 'all' (every agent).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max runs to return (default 20, max 50).",
                    },
                },
            },
            handler=lambda **kwargs: _list_runs(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="get_run",
            description=(
                "Get the full detail of one run from the execution log, including "
                "every tool call it made (tool_executions: name, status, input and "
                "output). Use after list_runs to drill into why a run failed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "run_id": {"type": "integer", "description": "ID of the run to inspect."},
                    "scope": {
                        "type": "string",
                        "description": "'own' (default) or 'all' to read another agent's run.",
                    },
                },
                "required": ["run_id"],
            },
            handler=lambda **kwargs: _get_run(**kwargs),
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


def _list_runs(_agent=None, status=None, trigger_type=None, scope="own", limit=20, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services import run_log_service

    scope = "all" if scope == "all" else "own"
    runs = run_log_service.recent_runs(
        agent_id=_agent.id, status=status, trigger_type=trigger_type, scope=scope, limit=limit
    )
    return {"runs": [run_log_service.summarize_run(r) for r in runs]}


def _get_run(_agent=None, run_id=None, scope="own", **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not run_id:
        return {"error": "Missing required argument 'run_id'"}
    from app.services import run_log_service

    scope = "all" if scope == "all" else "own"
    return run_log_service.run_detail(run_id, requesting_agent_id=_agent.id, scope=scope)


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
