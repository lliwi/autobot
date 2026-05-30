import json
import os

from flask import current_app, render_template, request
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.logging_config import REDIS_LOG_KEY, REDIS_LOG_MAX

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
PROCESSES = ["", "web", "worker"]
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000

# Execution-log ("Executions" tab) filter vocabularies.
RUN_STATUSES = ["running", "completed", "error"]
RUN_TRIGGERS = ["message", "cron", "heartbeat", "delegation", "internal", "auto_review", "patch_review"]
RUNS_DEFAULT_LIMIT = 50


def _redis_client():
    url = current_app.config.get("REDIS_URL") or os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis

        return redis.Redis.from_url(url, socket_timeout=1.0)
    except Exception:
        return None


def _fetch_entries(limit: int) -> tuple[list[dict], str | None]:
    client = _redis_client()
    if client is None:
        return [], "Redis not configured."
    try:
        # LPUSH is newest-first, so LRANGE 0..N returns most recent first already.
        raw = client.lrange(REDIS_LOG_KEY, 0, max(limit - 1, 0))
    except Exception as e:
        return [], f"Redis error: {type(e).__name__}: {e}"
    entries = []
    for item in raw:
        try:
            entries.append(json.loads(item))
        except (json.JSONDecodeError, TypeError):
            continue
    return entries, None


def _apply_filters(entries, level, process, logger_q, message_q):
    # Levels are ordered by severity — filter keeps records at or above the
    # selected threshold, so asking for WARNING also surfaces ERROR/CRITICAL.
    if level:
        try:
            min_idx = LEVELS.index(level)
            allowed = set(LEVELS[min_idx:])
            entries = [e for e in entries if (e.get("level") or "").upper() in allowed]
        except ValueError:
            pass
    if process:
        entries = [e for e in entries if (e.get("process") or "") == process]
    if logger_q:
        q = logger_q.lower()
        entries = [e for e in entries if q in (e.get("logger") or "").lower()]
    if message_q:
        q = message_q.lower()
        entries = [e for e in entries if q in (e.get("message") or "").lower()]
    return entries


@dashboard_bp.route("/logs")
@login_required
def logs():
    tab = request.args.get("tab", "app")
    if tab == "runs":
        return _logs_runs_tab()

    try:
        limit = int(request.args.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    level = request.args.get("level", "").upper()
    if level not in LEVELS:
        level = ""
    process = request.args.get("process", "")
    if process not in PROCESSES:
        process = ""
    logger_q = request.args.get("logger", "").strip()
    message_q = request.args.get("q", "").strip()

    # Pull a larger window than the final limit so filters don't produce a
    # near-empty page when the filtered subset is sparse in the recent tail.
    fetch_window = min(MAX_LIMIT, max(limit * 4, limit))
    entries, error = _fetch_entries(fetch_window)
    entries = _apply_filters(entries, level, process, logger_q, message_q)[:limit]

    partial = request.args.get("partial") == "1"
    template = "dashboard/_logs_table.html" if partial else "dashboard/logs.html"
    return render_template(
        template,
        tab="app",
        entries=entries,
        error=error,
        level=level,
        process=process,
        logger_q=logger_q,
        message_q=message_q,
        limit=limit,
        levels=LEVELS,
        processes=PROCESSES,
        ring_max=REDIS_LOG_MAX,
    )


def _logs_runs_tab():
    """Render the 'Executions' tab — the agent run log (runs + tool_executions)."""
    from app.models.agent import Agent
    from app.services import run_log_service

    status = request.args.get("status", "")
    if status not in RUN_STATUSES:
        status = ""
    trigger_type = request.args.get("trigger_type", "")
    if trigger_type not in RUN_TRIGGERS:
        trigger_type = ""
    agent_id = request.args.get("agent_id", type=int)
    run_id = request.args.get("run_id", type=int)

    try:
        limit = int(request.args.get("limit", RUNS_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = RUNS_DEFAULT_LIMIT
    limit = max(1, min(limit, run_log_service.MAX_LIMIT))

    # Dashboard is a human/admin view → system-wide scope, optionally narrowed to
    # one agent via the filter.
    runs = run_log_service.recent_runs(
        agent_id=agent_id,
        status=status or None,
        trigger_type=trigger_type or None,
        scope="own" if agent_id else "all",
        limit=limit,
    )

    detail = run_log_service.run_detail(run_id, scope="all") if run_id else None
    if detail and detail.get("error"):
        detail = None

    agents = Agent.query.order_by(Agent.name).all()

    partial = request.args.get("partial") == "1"
    template = "dashboard/_runs_table.html" if partial else "dashboard/logs.html"
    return render_template(
        template,
        tab="runs",
        runs=runs,
        detail=detail,
        run_id=run_id,
        agents=agents,
        agent_id=agent_id,
        status=status,
        trigger_type=trigger_type,
        limit=limit,
        run_statuses=RUN_STATUSES,
        run_triggers=RUN_TRIGGERS,
    )
