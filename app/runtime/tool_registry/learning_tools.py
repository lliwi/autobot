"""Error-learning digest and the autonomous-objective loop tools."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_learning_tools():
    register(
        ToolDefinition(
            name="error_digest",
            description=(
                "Summarize your recent failures (failed tool calls and errored/stuck runs) "
                "clustered by signature, so you can see which errors RECUR and fix the root "
                "cause. Returns each cluster's count, a sample message and the last run id. "
                "Recurring clusters also auto-spawn a fix objective for you to drive."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "window_hours": {"type": "integer", "description": "Look-back window in hours (default 24)."},
                    "min_count": {"type": "integer", "description": "Only clusters seen at least this many times (default 1)."},
                },
            },
            handler=lambda **kwargs: _error_digest(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="create_objective",
            description=(
                "Create a long-lived OBJECTIVE: a goal you keep working on across many runs, "
                "driven autonomously by the heartbeat supervisor between user messages. Use for "
                "anything that won't finish in one turn (a feature, an investigation, a migration). "
                "Optionally pass a `plan` (ordered list of steps) — the loop tracks the current step "
                "and drives it to completion."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short goal title."},
                    "description": {"type": "string", "description": "What 'done' looks like and any constraints."},
                    "plan": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional ordered list of steps to execute.",
                    },
                },
                "required": ["title"],
            },
            handler=lambda **kwargs: _create_objective(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_objectives",
            description="List your objectives (active/blocked/waiting by default; pass include_done=true for all).",
            parameters={
                "type": "object",
                "properties": {
                    "include_done": {"type": "boolean", "description": "Include done/cancelled objectives."},
                },
            },
            handler=lambda **kwargs: _list_objectives(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="update_objective",
            description=(
                "Update an objective: change status (active/blocked/waiting/cancelled), title, "
                "description, or replace its plan. Setting status to 'blocked' or 'waiting' "
                "notifies the user. Use 'waiting' when you need user input/approval, 'blocked' "
                "for an external dependency."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "objective_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["active", "blocked", "waiting", "cancelled"]},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "plan": {"type": "array", "items": {"type": "string"}, "description": "Replace the step plan."},
                    "note": {"type": "string", "description": "Reason, included in the user notification when blocked/waiting."},
                },
                "required": ["objective_id"],
            },
            handler=lambda **kwargs: _update_objective(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="objective_progress",
            description=(
                "Record progress on an objective so the supervisor knows it's advancing and "
                "doesn't back it off. If the objective has a plan, pass `advance_step=true` to "
                "mark the current step done and move to the next. Always note what you did."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "objective_id": {"type": "integer"},
                    "note": {"type": "string", "description": "What progress was made."},
                    "advance_step": {"type": "boolean", "description": "Mark the current plan step done and advance."},
                    "step_failed": {"type": "boolean", "description": "Mark the current step failed instead of done."},
                },
                "required": ["objective_id", "note"],
            },
            handler=lambda **kwargs: _objective_progress(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="complete_objective",
            description=(
                "Mark an objective DONE. Gated: you must pass `evidence` (what was done and how "
                "you VERIFIED it — tests run, output checked, review passed). If the objective has "
                "unfinished plan steps it is rejected unless force=true. Do not complete a "
                "development objective without real verification."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "objective_id": {"type": "integer"},
                    "evidence": {"type": "string", "description": "What was done and how it was verified."},
                    "force": {"type": "boolean", "description": "Complete even with unfinished plan steps."},
                },
                "required": ["objective_id", "evidence"],
            },
            handler=lambda **kwargs: _complete_objective(**kwargs),
        )
    )


def _error_digest(_agent=None, _run_id=None, window_hours=24, min_count=1, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services import error_analysis_service

    try:
        window = int(window_hours or 24)
        mc = int(min_count or 1)
    except (TypeError, ValueError):
        return {"error": "window_hours and min_count must be integers"}
    clusters = error_analysis_service.error_digest(_agent.id, window_hours=window, min_count=mc)
    return {"window_hours": window, "cluster_count": len(clusters), "clusters": clusters}


def _objective_brief(obj):
    ctx = obj.context_json or {}
    plan = ctx.get("plan") or []
    return {
        "id": obj.id,
        "title": obj.title,
        "status": obj.status,
        "current_step": ctx.get("current_step"),
        "plan": [{"step": p.get("step"), "status": p.get("status")} for p in plan],
        "last_progress_at": obj.last_progress_at.isoformat() if obj.last_progress_at else None,
    }


def _create_objective(_agent=None, _run_id=None, title=None, description=None, plan=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not title:
        return {"error": "title is required"}
    from app.services import objective_service

    obj = objective_service.create_objective(_agent.id, title, description or "")
    if plan:
        objective_service.set_plan(obj, [str(s) for s in plan])
    return {"created": True, "objective": _objective_brief(obj)}


def _list_objectives(_agent=None, _run_id=None, include_done=False, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services import objective_service

    objs = objective_service.list_objectives(_agent.id, include_done=bool(include_done))
    return {"count": len(objs), "objectives": [_objective_brief(o) for o in objs]}


def _update_objective(_agent=None, _run_id=None, objective_id=None, status=None,
                      title=None, description=None, plan=None, note=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not objective_id:
        return {"error": "objective_id is required"}
    from app.services import objective_service

    obj = objective_service.get_objective(objective_id)
    if obj is None or obj.agent_id != _agent.id:
        return {"error": f"Objective {objective_id} not found for this agent"}

    if title or description is not None:
        objective_service.update_objective(obj, **{k: v for k, v in
                                                   (("title", title), ("description", description))
                                                   if v is not None})
    if plan is not None:
        objective_service.set_plan(obj, [str(s) for s in plan])
    if status:
        objective_service.set_status(obj, status, note=note)
    return {"updated": True, "objective": _objective_brief(obj)}


def _objective_progress(_agent=None, _run_id=None, objective_id=None, note=None,
                        advance_step=False, step_failed=False, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not objective_id or not note:
        return {"error": "objective_id and note are required"}
    from app.services import objective_service

    obj = objective_service.get_objective(objective_id)
    if obj is None or obj.agent_id != _agent.id:
        return {"error": f"Objective {objective_id} not found for this agent"}

    objective_service.mark_progress(obj, note=note)
    result = {"progress_recorded": True}
    if advance_step:
        result["plan"] = objective_service.advance_plan(
            obj, result=note, status="failed" if step_failed else "done"
        )
    result["objective"] = _objective_brief(obj)
    return result


def _complete_objective(_agent=None, _run_id=None, objective_id=None, evidence=None, force=False, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not objective_id:
        return {"error": "objective_id is required"}
    from app.services import objective_service

    obj = objective_service.get_objective(objective_id)
    if obj is None or obj.agent_id != _agent.id:
        return {"error": f"Objective {objective_id} not found for this agent"}
    return objective_service.complete_objective(obj, evidence or "", force=bool(force))
