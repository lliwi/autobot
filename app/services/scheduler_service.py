from datetime import datetime, timezone

from croniter import croniter

from app.extensions import db
from app.models.scheduled_task import ScheduledTask


def create_task(agent_id, task_type, schedule_expr=None, timezone_str="UTC", payload_json=None, enabled=True, max_retries=3):
    task = ScheduledTask(
        agent_id=agent_id,
        task_type=task_type,
        schedule_expr=schedule_expr,
        timezone=timezone_str,
        payload_json=payload_json,
        enabled=enabled,
        max_retries=max_retries,
    )
    if schedule_expr and task_type == "cron":
        task.next_run_at = compute_next_run(schedule_expr)
    db.session.add(task)
    db.session.commit()
    return task


def update_task(task_id, **kwargs):
    task = db.session.get(ScheduledTask, task_id)
    if task is None:
        return None
    for key, value in kwargs.items():
        if hasattr(task, key):
            setattr(task, key, value)
    if "schedule_expr" in kwargs and task.task_type == "cron":
        task.next_run_at = compute_next_run(task.schedule_expr)
    db.session.commit()
    return task


def toggle_task(task_id):
    task = db.session.get(ScheduledTask, task_id)
    if task is None:
        return None
    task.enabled = not task.enabled
    if task.enabled and task.schedule_expr and task.task_type == "cron":
        task.next_run_at = compute_next_run(task.schedule_expr)
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
        task.next_run_at = compute_next_run(task.schedule_expr)
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


def compute_next_run(schedule_expr, base_time=None):
    if base_time is None:
        base_time = datetime.now(timezone.utc)
    try:
        cron = croniter(schedule_expr, base_time)
        return cron.get_next(datetime)
    except (ValueError, KeyError):
        return None
