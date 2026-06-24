import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.worker.cron_compat import build_cron_trigger

logger = logging.getLogger(__name__)

_scheduler = None
# Tracks the trigger "signature" (schedule + timezone, or heartbeat interval) of
# each managed job so _sync_jobs only reschedules a job when its schedule
# actually changed. Rescheduling on every 30s sync would reset the job's
# next_run_time and could silently drop a run that was due in that instant —
# the root cause of weekly tasks being skipped.
_job_signatures: dict[str, str] = {}


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

    # Drain the reviewer queue periodically.
    _scheduler.add_job(
        _drain_review_queue,
        trigger=IntervalTrigger(seconds=15),
        id="__drain_review_queue",
        replace_existing=True,
        kwargs={"app": app},
        max_instances=1,
        coalesce=True,
    )

    # Scan for recurring errors and spawn fix-objectives (error-learning loop).
    _scheduler.add_job(
        _scan_errors,
        trigger=IntervalTrigger(minutes=10),
        id="__scan_errors",
        replace_existing=True,
        kwargs={"app": app},
        max_instances=1,
        coalesce=True,
    )

    # Incident autopilot: persist queued ERROR/CRITICAL detections and diagnose.
    _scheduler.add_job(
        _drain_incidents,
        trigger=IntervalTrigger(seconds=20),
        id="__drain_incidents",
        replace_existing=True,
        kwargs={"app": app},
        max_instances=1,
        coalesce=True,
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
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        from app.extensions import db
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
                    signature=f"heartbeat:{agent.heartbeat_interval}",
                )

        # Sync cron tasks
        cron_tasks = ScheduledTask.query.filter_by(enabled=True).all()
        cron_job_ids = set()

        for task in cron_tasks:
            if task.task_type == "cron" and task.schedule_expr:
                job_id = f"cron_{task.id}"
                cron_job_ids.add(job_id)
                try:
                    tz_name = "UTC"
                    if task.timezone:
                        try:
                            ZoneInfo(task.timezone)  # validate only
                            tz_name = task.timezone
                        except ZoneInfoNotFoundError:
                            logger.warning(
                                "Unknown timezone %r for task %s; falling back to UTC",
                                task.timezone, task.id,
                            )
                    trigger = build_cron_trigger(task.schedule_expr, timezone=tz_name)
                    _ensure_job(
                        job_id=job_id,
                        func=_execute_cron_task,
                        trigger=trigger,
                        kwargs={"app": app, "task_id": task.id},
                        signature=f"cron:{task.schedule_expr}:{tz_name}",
                    )
                    # Keep DB next_run_at aligned with the job's *actual* next
                    # fire time once the scheduler is running. Falling back to a
                    # fresh trigger computation only during init (before the job
                    # has a next_run_time), so we never overwrite the real
                    # pending fire with a recomputed-from-now value.
                    job = _scheduler.get_job(job_id)
                    next_fire = getattr(job, "next_run_time", None) if job else None
                    if next_fire is None:
                        now = _dt.now(ZoneInfo(tz_name) if tz_name != "UTC" else _tz.utc)
                        next_fire = trigger.get_next_fire_time(None, now)
                    if next_fire is not None:
                        new_next = next_fire.astimezone(_tz.utc).replace(tzinfo=None)
                        if task.next_run_at != new_next:
                            task.next_run_at = new_next
                            db.session.commit()
                    # Health check: an enabled task whose next fire is already far
                    # in the past means the job never advanced — surface it.
                    if task.next_run_at is not None:
                        behind = (_dt.now(_tz.utc).replace(tzinfo=None) - task.next_run_at).total_seconds()
                        if behind > 3600:
                            logger.warning(
                                "Scheduled task %s (%r) is stale: next_run_at %s is %.0fs in the past",
                                task.id, task.schedule_expr, task.next_run_at, behind,
                            )
                except ValueError as e:
                    logger.error(f"Invalid cron expression for task {task.id}: {e}")

        # Remove jobs for disabled/deleted tasks. One-off drive-to-completion
        # continuation jobs (heartbeat_drive_*) are transient — leave them alone.
        expected_ids = heartbeat_job_ids | cron_job_ids | {"__sync_jobs", "__drain_review_queue", "__scan_errors"}
        for job in _scheduler.get_jobs():
            if job.id not in expected_ids and not job.id.startswith("heartbeat_drive_"):
                logger.info(f"Removing stale job: {job.id}")
                job.remove()
                _job_signatures.pop(job.id, None)


def _ensure_job(job_id, func, trigger, kwargs, signature):
    """Add a job, or reschedule it only when its trigger actually changed.

    Critically, an existing job whose ``signature`` is unchanged is left alone:
    calling ``reschedule`` on every sync would reset ``next_run_time`` and could
    drop a run that was due at that instant (this is what silently skipped
    weekly tasks). We only reschedule when the schedule/timezone changed.
    """
    existing = _scheduler.get_job(job_id)
    if existing:
        if _job_signatures.get(job_id) != signature:
            existing.reschedule(trigger)
            _job_signatures[job_id] = signature
        return
    _scheduler.add_job(
        func,
        trigger=trigger,
        id=job_id,
        replace_existing=True,
        kwargs=kwargs,
        misfire_grace_time=300,
    )
    _job_signatures[job_id] = signature


def _execute_heartbeat(app, agent_id, drive=False):
    """Run one supervisor tick for an agent.

    Delegates to ``heartbeat_supervisor.tick`` which builds a snapshot,
    decides (skip/defer/act) and records a HeartbeatEvent. When the tick signals
    ``drive_again`` (an objective made progress and is still active), schedule a
    quick continuation tick so the loop drives the objective to completion
    instead of waiting for the next ambient interval.
    """
    with app.app_context():
        from app.services import heartbeat_supervisor

        try:
            event = heartbeat_supervisor.tick(agent_id, drive=drive)
            logger.info(
                "heartbeat agent=%s decision=%s reason=%s run=%s drive=%s",
                agent_id, event.decision, event.reason, event.run_id, drive,
            )
            if (event.snapshot_json or {}).get("drive_again"):
                _schedule_drive_followup(app, agent_id)
        except Exception as e:
            logger.exception(f"Heartbeat tick failed for agent {agent_id}: {e}")


def _schedule_drive_followup(app, agent_id):
    """Schedule a one-off drive continuation tick a few seconds out."""
    from datetime import datetime, timedelta, timezone

    from apscheduler.triggers.date import DateTrigger

    from app.services import heartbeat_supervisor

    if _scheduler is None:
        return
    run_at = datetime.now(timezone.utc) + timedelta(
        seconds=heartbeat_supervisor.DRIVE_FOLLOWUP_SECONDS
    )
    _scheduler.add_job(
        _execute_heartbeat,
        trigger=DateTrigger(run_date=run_at),
        id=f"heartbeat_drive_{agent_id}",
        replace_existing=True,
        kwargs={"app": app, "agent_id": agent_id, "drive": True},
        max_instances=1,
        coalesce=True,
    )


def _drain_review_queue(app):
    """Process any pending ReviewEvent rows. One per tick is enough — the
    reviewer is a Codex round-trip and we don't want to hog the worker.
    """
    with app.app_context():
        from app.services import review_queue_service

        try:
            n = review_queue_service.process_batch(max_items=3)
            if n:
                logger.info("review-queue drained %d event(s)", n)
        except Exception:
            logger.exception("review-queue drain failed")


def _scan_errors(app):
    """Detect recurring error clusters and spawn fix-objectives for each agent."""
    with app.app_context():
        from app.services import error_analysis_service

        try:
            n = error_analysis_service.scan_all_active_agents()
            if n:
                logger.info("error-loop: spawned %d fix objective(s)", n)
        except Exception:
            logger.exception("error-loop scan failed")


def _drain_incidents(app):
    """Persist queued ERROR/CRITICAL detections and run reviewer diagnosis.

    Each diagnosis is a Codex round-trip, so cap the batch to keep the worker
    responsive; remaining items are picked up on the next tick.
    """
    if not app.config.get("INCIDENT_AUTOPILOT_ENABLED", True):
        return
    with app.app_context():
        from app.services import incident_service

        try:
            n = incident_service.drain_queue(max_items=5)
            if n:
                logger.info("incident autopilot: diagnosed %d incident(s)", n)
        except Exception:
            logger.exception("incident drain failed")


def _execute_cron_task(app, task_id):
    """Execute a scheduled cron task."""
    with app.app_context():
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        from croniter import croniter as _croniter

        from app.services.chat_service import run_agent_non_streaming
        from app.services.scheduler_service import get_task, mark_task_executed, mark_task_failed

        task = get_task(task_id)
        if task is None or not task.enabled:
            return

        # Pre-dispatch validation: guard against misfire across day-of-week boundary.
        # APScheduler's misfire_grace_time allows late execution up to 300 s, but a
        # delayed job near midnight could cross into an excluded day. Re-check that
        # the previous cron fire time is within the grace window before proceeding.
        try:
            tz = _tz.utc
            if task.timezone:
                try:
                    tz = ZoneInfo(task.timezone)
                except ZoneInfoNotFoundError:
                    pass
            now_local = _dt.now(tz)
            it = _croniter(task.schedule_expr, now_local)
            prev_fire = it.get_prev(_dt)
            elapsed = (now_local - prev_fire).total_seconds()
            if elapsed > 300:  # matches misfire_grace_time
                logger.warning(
                    "Skipping cron task %s: %.0fs since last valid fire %s "
                    "(expr=%r tz=%s) — possible day-of-week boundary misfire",
                    task_id, elapsed, prev_fire, task.schedule_expr, task.timezone,
                )
                return
        except Exception:
            logger.warning("Pre-dispatch cron validation failed for task %s", task_id, exc_info=True)

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
            # Link the run to this task so the execution log can trace scheduler
            # history back to the task (used by /logs and the agent's list_runs).
            from app.services.run_log_service import link_run_to_task

            link_run_to_task(result.get("run_id"), task_id)
            if result.get("error"):
                logger.warning(f"Cron task {task_id} error: {result['error']}")
                mark_task_failed(task_id)
            else:
                mark_task_executed(task_id)
                logger.info(f"Cron task {task_id} completed")
        except Exception as e:
            logger.error(f"Cron task {task_id} failed: {e}")
            mark_task_failed(task_id)
