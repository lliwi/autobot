"""Query helpers over the execution log (``runs`` + ``tool_executions``).

Every agent invocation — manual (`trigger_type="message"`), scheduler
(`cron`/`heartbeat`), delegation, review — already produces a ``Run`` row
(see [run_service](app/services/run_service.py)) and per-tool ``ToolExecution``
rows. This module exposes that history for self-diagnosis: both the agent-facing
tools (``list_runs`` / ``get_run``) and the /logs "Executions" tab read through
here.

Scope: ``"own"`` restricts to the requesting agent's runs (the safe default for
tools); ``"all"`` spans every agent (used by the dashboard and by an
orchestrator that supervises the whole system).
"""

import json

from app.extensions import db
from app.models.run import Run
from app.models.tool_execution import ToolExecution
from app.utils.timefmt import utc_iso

MAX_LIMIT = 50
_OUTPUT_PREVIEW = 2000  # chars; tool_execution outputs can be large


def recent_runs(agent_id=None, status=None, trigger_type=None, scope="own", limit=20):
    """Return recent ``Run`` rows, newest first.

    ``scope="own"`` filters to ``agent_id``; ``scope="all"`` spans all agents.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, MAX_LIMIT))

    query = Run.query
    if scope != "all":
        query = query.filter(Run.agent_id == agent_id)
    if status:
        query = query.filter(Run.status == status)
    if trigger_type:
        query = query.filter(Run.trigger_type == trigger_type)
    return query.order_by(Run.started_at.desc()).limit(limit).all()


def run_detail(run_id, requesting_agent_id=None, scope="own"):
    """Return one run's summary plus its tool executions, or an ``error`` dict.

    With ``scope="own"`` a run that belongs to another agent is refused.
    """
    run = db.session.get(Run, run_id)
    if run is None:
        return {"error": f"Run {run_id} not found"}
    if scope != "all" and run.agent_id != requesting_agent_id:
        return {"error": f"Run {run_id} does not belong to this agent"}

    execs = (
        ToolExecution.query.filter_by(run_id=run.id)
        .order_by(ToolExecution.started_at.asc())
        .all()
    )
    return {
        "run": summarize_run(run),
        "tool_executions": [summarize_execution(e) for e in execs],
        "rounds_trace": run.rounds_trace or [],
    }


def link_run_to_task(run_id, task_id):
    """Attach a run to the ScheduledTask that triggered it. No-op if missing."""
    if not run_id:
        return
    run = db.session.get(Run, run_id)
    if run is None:
        return
    run.scheduled_task_id = task_id
    db.session.commit()


def summarize_run(run):
    task_name = None
    if run.scheduled_task_id and run.scheduled_task is not None:
        task_name = run.scheduled_task.name
    return {
        "id": run.id,
        "agent_id": run.agent_id,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "started_at": utc_iso(run.started_at),
        "finished_at": utc_iso(run.finished_at),
        "duration_ms": run.duration_ms,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "estimated_cost": run.estimated_cost,
        "scheduled_task_id": run.scheduled_task_id,
        "scheduled_task_name": task_name,
        "error_summary": run.error_summary,
    }


def summarize_execution(execution):
    return {
        "id": execution.id,
        "tool_name": execution.tool_name,
        "status": execution.status,
        "started_at": utc_iso(execution.started_at),
        "finished_at": utc_iso(execution.finished_at),
        "input": _preview(execution.input_json),
        "output": _preview(execution.output_json),
    }


def _preview(value):
    """Stringify and truncate a JSON-ish value so big outputs don't flood context."""
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > _OUTPUT_PREVIEW:
        return text[:_OUTPUT_PREVIEW] + f"… (+{len(text) - _OUTPUT_PREVIEW} chars truncated)"
    return text
