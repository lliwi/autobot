import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler = None


def init_scheduler(app):
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")

    # Add a periodic job to sync tasks from DB every 30 seconds
    _scheduler.add_job(
        _sync_jobs,
        trigger=IntervalTrigger(seconds=30),
        id="__sync_jobs",
        replace_existing=True,
        kwargs={"app": app},
    )

    # Initial sync
    with app.app_context():
        _sync_jobs(app)

    _scheduler.start()
    logger.info("Scheduler started")
    return _scheduler


def shutdown_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")


def _sync_jobs(app):
    """Sync APScheduler jobs from database state."""
    with app.app_context():
        from app.models.agent import Agent
        from app.models.scheduled_task import ScheduledTask

        # Sync heartbeat jobs for active agents
        active_agents = Agent.query.filter_by(status="active").all()
        heartbeat_job_ids = set()

        for agent in active_agents:
            if agent.heartbeat_interval and agent.heartbeat_interval > 0:
                job_id = f"heartbeat_{agent.id}"
                heartbeat_job_ids.add(job_id)
                _ensure_job(
                    job_id=job_id,
                    func=_execute_heartbeat,
                    trigger=IntervalTrigger(minutes=agent.heartbeat_interval),
                    kwargs={"app": app, "agent_id": agent.id},
                )

        # Sync cron tasks
        cron_tasks = ScheduledTask.query.filter_by(enabled=True).all()
        cron_job_ids = set()

        for task in cron_tasks:
            if task.task_type == "cron" and task.schedule_expr:
                job_id = f"cron_{task.id}"
                cron_job_ids.add(job_id)
                try:
                    trigger = CronTrigger.from_crontab(task.schedule_expr)
                    _ensure_job(
                        job_id=job_id,
                        func=_execute_cron_task,
                        trigger=trigger,
                        kwargs={"app": app, "task_id": task.id},
                    )
                except ValueError as e:
                    logger.error(f"Invalid cron expression for task {task.id}: {e}")

        # Remove jobs for disabled/deleted tasks
        expected_ids = heartbeat_job_ids | cron_job_ids | {"__sync_jobs"}
        for job in _scheduler.get_jobs():
            if job.id not in expected_ids:
                logger.info(f"Removing stale job: {job.id}")
                job.remove()


def _ensure_job(job_id, func, trigger, kwargs):
    """Add or update a job in the scheduler."""
    existing = _scheduler.get_job(job_id)
    if existing:
        existing.reschedule(trigger)
    else:
        _scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            kwargs=kwargs,
            misfire_grace_time=300,
        )


def _execute_heartbeat(app, agent_id):
    """Run one supervisor tick for an agent.

    Delegates to ``heartbeat_supervisor.tick`` which builds a snapshot,
    decides (skip/defer/act) and records a HeartbeatEvent.
    """
    with app.app_context():
        from app.services import heartbeat_supervisor

        try:
            event = heartbeat_supervisor.tick(agent_id)
            logger.info(
                "heartbeat agent=%s decision=%s reason=%s run=%s",
                agent_id, event.decision, event.reason, event.run_id,
            )
        except Exception as e:
            logger.exception(f"Heartbeat tick failed for agent {agent_id}: {e}")


def _execute_cron_task(app, task_id):
    """Execute a scheduled cron task."""
    with app.app_context():
        from app.services.chat_service import run_agent_non_streaming
        from app.services.scheduler_service import get_task, mark_task_executed, mark_task_failed

        task = get_task(task_id)
        if task is None or not task.enabled:
            return

        payload = task.payload_json or {}
        message = payload.get("message", f"[CRON] Execute scheduled task: {task.id}")

        logger.info(f"Executing cron task {task_id} for agent {task.agent_id}")
        try:
            result = run_agent_non_streaming(
                agent_id=task.agent_id,
                message=message,
                channel_type="internal",
                trigger_type="cron",
            )
            if result.get("error"):
                logger.warning(f"Cron task {task_id} error: {result['error']}")
                mark_task_failed(task_id)
            else:
                mark_task_executed(task_id)
                logger.info(f"Cron task {task_id} completed")
        except Exception as e:
            logger.error(f"Cron task {task_id} failed: {e}")
            mark_task_failed(task_id)
