"""Scheduled-task (cron) management tools for the calling agent."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_schedule_tools():
    register(
        ToolDefinition(
            name="schedule_task",
            description=(
                "Create a recurring scheduled task for THIS agent. At each trigger the "
                "scheduler will invoke the agent with the given message. Use this when "
                "the user asks for a daily/weekly/periodic task (e.g. 'every day at 18:00'). "
                "The cron expression is evaluated in the task's timezone (defaults to the "
                "server timezone), so '0 18 * * *' means 18:00 local time, not UTC."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "schedule_expr": {
                        "type": "string",
                        "description": "Standard 5-field cron expression, e.g. '0 18 * * *' for every day at 18:00.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The prompt the agent will receive when the task fires.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": (
                            "IANA timezone in which the cron fields are evaluated, e.g. "
                            "'Europe/Madrid'. Defaults to the server timezone if omitted."
                        ),
                    },
                },
                "required": ["schedule_expr", "message"],
            },
            handler=lambda **kwargs: _schedule_task(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_scheduled_tasks",
            description="List scheduled tasks owned by this agent.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_scheduled_tasks(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="cancel_scheduled_task",
            description="Delete a scheduled task owned by this agent.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID of the ScheduledTask to delete."}
                },
                "required": ["task_id"],
            },
            handler=lambda **kwargs: _cancel_scheduled_task(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="set_scheduled_task_enabled",
            description=(
                "Enable (reactivate) or disable (pause) a scheduled task owned by "
                "this agent, without deleting it. Enabling recomputes its next run "
                "time. Use this to reactivate a paused task instead of creating a "
                "new one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID of the ScheduledTask."},
                    "enabled": {"type": "boolean", "description": "true to enable, false to disable."},
                },
                "required": ["task_id", "enabled"],
            },
            handler=lambda **kwargs: _set_scheduled_task_enabled(**kwargs),
        )
    )


def _schedule_task(_agent=None, _run_id=None, schedule_expr=None, message=None, timezone=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (("schedule_expr", schedule_expr), ("message", message)) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    from croniter import croniter
    if not croniter.is_valid(schedule_expr):
        return {"error": f"Invalid cron expression: {schedule_expr!r}. Expected 5 fields, e.g. '0 18 * * *'."}
    from flask import current_app

    from app.services.scheduler_service import create_task
    from app.utils.timefmt import local_str, utc_iso

    # Default to the server's timezone so "every day at 9" means 9 local, not UTC.
    tz = timezone or current_app.config.get("APP_TIMEZONE") or "UTC"

    task = create_task(
        agent_id=_agent.id,
        task_type="cron",
        schedule_expr=schedule_expr,
        timezone_str=tz,
        payload_json={"message": message},
    )

    from app.services.review_service import review_creation
    review_payload = (
        f"Cron: `{schedule_expr}` (tz={tz})\n"
        f"Next run: {utc_iso(task.next_run_at) or 'n/a'} ({local_str(task.next_run_at, tz) or 'n/a'})\n\n"
        f"Prompt that will fire:\n---\n{message}\n---"
    )
    review = review_creation(_agent, "scheduled_task", str(task.id), review_payload, run_id=_run_id)

    result = {
        "task_id": task.id,
        "schedule_expr": task.schedule_expr,
        "timezone": task.timezone,
        "next_run_at_utc": utc_iso(task.next_run_at),
        "next_run_at_local": local_str(task.next_run_at, task.timezone),
        "enabled": task.enabled,
        "message": "Scheduled task created. The worker will pick it up within ~30s.",
    }
    if review is not None:
        result["review"] = review
    return result


def _list_scheduled_tasks(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from datetime import datetime, timezone

    from app.services.scheduler_service import list_tasks
    from app.utils.timefmt import local_str, utc_iso

    tasks = list_tasks(agent_id=_agent.id)
    return {
        # Reference clock so the agent can reason about "next run" without
        # guessing the zone. All *_utc fields end in 'Z'; *_local are rendered
        # in each task's own timezone. The cron expression is evaluated in that
        # timezone, not UTC.
        "now_utc": utc_iso(datetime.now(timezone.utc)),
        "tasks": [
            {
                "id": t.id,
                "name": t.name,
                "task_type": t.task_type,
                "schedule_expr": t.schedule_expr,
                "timezone": t.timezone,
                "enabled": t.enabled,
                "next_run_at_utc": utc_iso(t.next_run_at),
                "next_run_at_local": local_str(t.next_run_at, t.timezone),
                "last_run_at_utc": utc_iso(t.last_run_at),
                "last_run_at_local": local_str(t.last_run_at, t.timezone),
                "message": (t.payload_json or {}).get("message"),
            }
            for t in tasks
        ],
    }


def _cancel_scheduled_task(_agent=None, task_id=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not task_id:
        return {"error": "Missing required argument 'task_id'"}
    from app.services.scheduler_service import delete_task, get_task

    task = get_task(task_id)
    if task is None:
        return {"error": f"Task {task_id} not found"}
    if task.agent_id != _agent.id:
        return {"error": f"Task {task_id} does not belong to this agent"}
    delete_task(task_id)
    return {"task_id": task_id, "message": "Scheduled task deleted."}


def _set_scheduled_task_enabled(_agent=None, task_id=None, enabled=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if task_id is None or enabled is None:
        return {"error": "Missing required arguments 'task_id' and 'enabled'"}
    from app.services.scheduler_service import get_task, set_task_enabled
    from app.utils.timefmt import local_str, utc_iso

    task = get_task(task_id)
    if task is None:
        return {"error": f"Task {task_id} not found"}
    if task.agent_id != _agent.id:
        return {"error": f"Task {task_id} does not belong to this agent"}
    task = set_task_enabled(task_id, enabled)
    return {
        "task_id": task.id,
        "enabled": task.enabled,
        "timezone": task.timezone,
        "next_run_at_utc": utc_iso(task.next_run_at),
        "next_run_at_local": local_str(task.next_run_at, task.timezone),
        "message": f"Scheduled task {'enabled' if task.enabled else 'disabled'}.",
    }
