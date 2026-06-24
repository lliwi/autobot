import json
import logging
import os
import sys
from datetime import datetime, timezone

# Redis-backed ring buffer for the dashboard Logs view. Bounded, cross-process
# (web + worker share it), survives individual restarts. Falls back to a no-op
# if Redis is unreachable — logging must never break the app.
REDIS_LOG_KEY = "autobot:logs"
REDIS_LOG_MAX = 5000


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


class RedisRingHandler(logging.Handler):
    """Ship records to a Redis list with LPUSH + LTRIM.

    The dashboard Logs view reads back the most recent N via LRANGE. We tag
    each record with ``process`` so the UI can filter web vs. worker noise
    without relying on logger-name conventions. Errors pushing to Redis are
    swallowed — the stdout handler always keeps the trail.
    """

    def __init__(self, redis_url: str, process: str, level=logging.INFO):
        super().__init__(level=level)
        self._process = process
        self._redis = None
        try:
            import redis  # local import — the worker process doesn't always import flask

            self._redis = redis.Redis.from_url(redis_url, socket_timeout=0.5)
        except Exception:
            self._redis = None

    def emit(self, record):
        if self._redis is None:
            return
        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "process": self._process,
            }
            if record.exc_info and record.exc_info[0] is not None:
                entry["exception"] = self.format(record) if not record.exc_text else record.exc_text
            pipe = self._redis.pipeline()
            pipe.lpush(REDIS_LOG_KEY, json.dumps(entry))
            pipe.ltrim(REDIS_LOG_KEY, 0, REDIS_LOG_MAX - 1)
            pipe.execute()
        except Exception:
            # Logging must never raise.
            pass


class IncidentLogHandler(logging.Handler):
    """Raise an incident on ERROR/CRITICAL records (incident autopilot).

    Self-contained on purpose: it reads ``INCIDENT_*`` / ``REDIS_URL`` from the
    environment and pushes a deduplicated payload straight to Redis, so it works
    in any thread/process with or without a Flask app context. The worker drains
    the queue and runs diagnosis (see ``incident_service``). Anything that can go
    wrong here is swallowed — logging must never break the app, least of all the
    handler that reacts to errors.
    """

    def __init__(self, redis_url: str, process: str):
        super().__init__(level=logging.ERROR)
        self._process = process
        self._redis = None
        self._ignore = tuple(
            p.strip() for p in os.environ.get(
                "INCIDENT_IGNORE_LOGGERS",
                "app.services.incident_service,app.services.github_service,app.services.review_queue_service",
            ).split(",") if p.strip()
        )
        self._min_critical = os.environ.get("INCIDENT_MIN_SEVERITY", "error").lower() == "critical"
        self._cooldown_h = int(os.environ.get("INCIDENT_DEDUP_COOLDOWN_HOURS", "12") or 12)
        try:
            import redis
            self._redis = redis.Redis.from_url(redis_url, socket_timeout=0.5, decode_responses=True)
        except Exception:
            self._redis = None

    def emit(self, record):
        if self._redis is None:
            return
        try:
            if self._min_critical and record.levelno < logging.CRITICAL:
                return
            if record.name and any(record.name.startswith(p) for p in self._ignore):
                return

            from app.services.incident_service import (
                REDIS_QUEUE_KEY, _REDIS_DEDUP_PREFIX, signature_for,
            )
            import json as _json

            message = record.getMessage()
            tb = self.format(record) if (record.exc_info and record.exc_info[0]) else None
            severity = "critical" if record.levelno >= logging.CRITICAL else "error"
            sig = signature_for(message, record.name)
            # NX dedup: at most one enqueue per signature per cooldown window.
            if not self._redis.set(_REDIS_DEDUP_PREFIX + sig, "1", nx=True, ex=self._cooldown_h * 3600):
                return
            payload = _json.dumps({
                "severity": severity,
                "source": record.name,
                "title": message[:300],
                "message": message[:8000],
                "traceback": (tb or "")[:16000],
                "agent_id": None,
                "signature": sig,
            })
            self._redis.lpush(REDIS_QUEUE_KEY, payload)
        except Exception:
            pass  # never raise from a log handler


def _incident_autopilot_on() -> bool:
    return os.environ.get("INCIDENT_AUTOPILOT_ENABLED", "true").lower() in ("1", "true", "yes")


def _attach_incident_handler(logger_obj, redis_url: str, process: str):
    if not (redis_url and _incident_autopilot_on()):
        return
    if any(isinstance(h, IncidentLogHandler) for h in logger_obj.handlers):
        return
    logger_obj.addHandler(IncidentLogHandler(redis_url, process=process))


def _attach_common_handlers(logger_obj, level, redis_url: str, process: str):
    """Attach the shared stdout+Redis handlers to ``logger_obj``.

    Separate helper because both ``configure_logging`` (web) and the worker
    bootstrap want the same setup but on different root loggers.
    """
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(JSONFormatter())
    stdout_handler.setLevel(level)
    logger_obj.addHandler(stdout_handler)

    if redis_url:
        logger_obj.addHandler(RedisRingHandler(redis_url, process=process, level=level))


def configure_logging(app):
    log_level = app.config.get("LOG_LEVEL", "INFO")
    redis_url = app.config.get("REDIS_URL") or os.environ.get("REDIS_URL", "")

    app.logger.handlers.clear()
    _attach_common_handlers(app.logger, log_level, redis_url, process="web")
    app.logger.setLevel(log_level)

    # Also feed the root logger so libraries (httpx, apscheduler, nio, etc.)
    # land in the same ring. Without this, only app.logger output is indexed.
    root = logging.getLogger()
    # Don't duplicate stdout — the basicConfig already installed one. Just
    # attach the Redis sink to the root logger.
    if redis_url and not any(isinstance(h, RedisRingHandler) for h in root.handlers):
        root.addHandler(RedisRingHandler(redis_url, process="web", level=log_level))

    # Incident autopilot: react to ERROR/CRITICAL across all loggers.
    _attach_incident_handler(root, redis_url, process="web")

    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def configure_worker_logging(process: str = "worker", log_level: str = "INFO"):
    """Entry point for standalone processes (worker.py) to get the same pipe.

    The worker isn't a Flask app so it can't call ``configure_logging``.
    We still want its records in the ring buffer — same Redis key, different
    ``process`` tag.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    root = logging.getLogger()
    # basicConfig already added a stdout handler; just attach the Redis one.
    if redis_url and not any(isinstance(h, RedisRingHandler) for h in root.handlers):
        root.addHandler(RedisRingHandler(redis_url, process=process, level=log_level))

    # Incident autopilot: react to ERROR/CRITICAL across all loggers.
    _attach_incident_handler(root, redis_url, process=process)
