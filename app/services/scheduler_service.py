from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from app.extensions import db
from app.models.scheduled_task import ScheduledTask


def create_task(agent_id, task_type, schedule_expr=None, timezone_str="UTC", payload_json=None, enabled=True, max_retries=3, name=None):
    task = ScheduledTask(
        agent_id=agent_id,
        name=(name or "").strip() or None,
        task_type=task_type,
        schedule_expr=schedule_expr,
        timezone=timezone_str,
        payload_json=payload_json,
        enabled=enabled,
        max_retries=max_retries,
    )
    if schedule_expr and task_type == "cron":
        task.next_run_at = compute_next_run(schedule_expr, tz_name=timezone_str)
    db.session.add(task)
    db.session.commit()
    return task


def update_task(task_id, **kwargs):
    task = db.session.get(ScheduledTask, task_id)
    if task is None:
        return None
    if "name" in kwargs:
        kwargs["name"] = (kwargs["name"] or "").strip() or None
    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)
    if ("schedule_expr" in kwargs or "timezone" in kwargs) and task.task_type == "cron":
        task.next_run_at = compute_next_run(task.schedule_expr, tz_name=task.timezone)
    db.session.commit()
    return task


def toggle_task(task_id):
    task = db.session.get(ScheduledTask, task_id)
    if task is None:
        return None
    task.enabled = not task.enabled
    if task.enabled and task.schedule_expr and task.task_type == "cron":
        task.next_run_at = compute_next_run(task.schedule_expr, tz_name=task.timezone)
    db.session.commit()
    return task


def delete_task(task_id):
    task = db.session.get(ScheduledTask, task_id)
    if task is None:
        return False
    db.session.delete(task)
    db.session.commit()
    return True


def list_tasks(agent_id=None):
    query = ScheduledTask.query
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    return query.order_by(ScheduledTask.created_at.desc()).all()


def get_task(task_id):
    return db.session.get(ScheduledTask, task_id)


def mark_task_executed(task_id):
    task = db.session.get(ScheduledTask, task_id)
    if task is None:
        return None
    task.last_run_at = datetime.now(timezone.utc)
    task.retry_count = 0
    if task.schedule_expr and task.task_type == "cron":
        task.next_run_at = compute_next_run(task.schedule_expr, tz_name=task.timezone)
    elif task.task_type == "one_shot":
        task.enabled = False
        task.next_run_at = None
    db.session.commit()
    return task


def mark_task_failed(task_id):
    task = db.session.get(ScheduledTask, task_id)
    if task is None:
        return None
    task.retry_count += 1
    if task.retry_count >= task.max_retries:
        task.enabled = False
    db.session.commit()
    return task


def compute_next_run(schedule_expr, base_time=None, tz_name=None):
    """Return the next cron fire time as a tz-aware UTC datetime.

    The cron expression is interpreted in ``tz_name`` (falls back to UTC) so
    "0 18 * * *" with Europe/Madrid fires at 18:00 local, not 18:00 UTC. The
    return value is normalized to UTC because the DB column is naive and we
    want the stored wall-clock to be UTC.
    """
    tz = timezone.utc
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = timezone.utc

    if base_time is None:
        base_time = datetime.now(tz)
    elif base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=timezone.utc).astimezone(tz)
    else:
        base_time = base_time.astimezone(tz)

    try:
        cron = croniter(schedule_expr, base_time)
        nxt = cron.get_next(datetime)
        return nxt.astimezone(timezone.utc)
    except (ValueError, KeyError):
        return None
