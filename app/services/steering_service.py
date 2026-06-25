"""Inline steering: talk to an agent while it's mid-task.

While a run is streaming, the user can keep typing. Each message is pushed to a
per-session Redis **interjection inbox**; ``agent_runner`` drains it at the start
of every tool-call round and injects the messages into the live conversation, so
the model takes the new input into account on its next turn. The agent itself
decides whether to fold the input into the current task or to queue it as a
separate task (an immediate follow-up via :func:`queue_followup`, or a background
``Objective`` via the existing ``create_objective`` tool).

All state is in Redis (ephemeral, cross-process) and every call is best-effort:
steering must never break the run it is steering.
"""
import json
import logging

from flask import current_app

logger = logging.getLogger(__name__)

_INBOX_KEY = "autobot:steer:inbox:{sid}"
_FOLLOWUP_KEY = "autobot:steer:followup:{sid}"
_MAX_ITEMS = 50          # hard cap so a stuck client can't grow the list forever
_TTL_SECONDS = 6 * 3600  # self-expire stale inboxes


def _redis():
    try:
        import redis
        url = current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
        return redis.Redis.from_url(url, socket_timeout=0.5, decode_responses=True)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Interjections (steer the current run)
# --------------------------------------------------------------------------- #

def push_interjection(session_id: int, message: str) -> bool:
    """Queue a mid-task message for the run on ``session_id``. Returns True if stored."""
    if not session_id or not (message or "").strip():
        return False
    r = _redis()
    if r is None:
        return False
    try:
        key = _INBOX_KEY.format(sid=session_id)
        if r.llen(key) >= _MAX_ITEMS:
            return False
        r.rpush(key, json.dumps({"message": message}))
        r.expire(key, _TTL_SECONDS)
        return True
    except Exception:
        logger.debug("steering push_interjection failed", exc_info=True)
        return False


def drain_interjections(session_id: int) -> list[str]:
    """Atomically pop all pending interjections for ``session_id`` (FIFO)."""
    if not session_id:
        return []
    r = _redis()
    if r is None:
        return []
    try:
        key = _INBOX_KEY.format(sid=session_id)
        pipe = r.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        raw_items, _ = pipe.execute()
        out = []
        for raw in raw_items or []:
            try:
                out.append(json.loads(raw).get("message", ""))
            except Exception:
                continue
        return [m for m in out if m]
    except Exception:
        logger.debug("steering drain_interjections failed", exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# Follow-ups (run as a new turn once the current one finishes)
# --------------------------------------------------------------------------- #

def queue_followup(session_id: int, message: str) -> bool:
    if not session_id or not (message or "").strip():
        return False
    r = _redis()
    if r is None:
        return False
    try:
        key = _FOLLOWUP_KEY.format(sid=session_id)
        if r.llen(key) >= _MAX_ITEMS:
            return False
        r.rpush(key, message)
        r.expire(key, _TTL_SECONDS)
        return True
    except Exception:
        logger.debug("steering queue_followup failed", exc_info=True)
        return False


def pop_followups(session_id: int) -> list[str]:
    """Pop and return all queued follow-up messages for ``session_id`` (FIFO)."""
    if not session_id:
        return []
    r = _redis()
    if r is None:
        return []
    try:
        key = _FOLLOWUP_KEY.format(sid=session_id)
        pipe = r.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        items, _ = pipe.execute()
        return [m for m in (items or []) if m]
    except Exception:
        logger.debug("steering pop_followups failed", exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# Active-run detection (used by the API to choose steer vs new run)
# --------------------------------------------------------------------------- #

def has_active_run(session_id: int) -> bool:
    """True if a run for this session is currently executing."""
    if not session_id:
        return False
    from app.models.run import Run
    return (
        Run.query.filter_by(session_id=session_id, status="running").first() is not None
    )
