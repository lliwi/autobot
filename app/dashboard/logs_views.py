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
