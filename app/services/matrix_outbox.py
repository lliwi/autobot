"""Redis-based outbox for Matrix messages.

The web process cannot reach the MatrixBot directly (it runs only in the
worker). This module provides a thin Redis list that bridges the gap:

  - ``enqueue(target, body)``   — called from web or worker process
  - ``drain(bot, max_items)``   — called by the worker's polling loop
  - ``queue_length()``          — observability helper

Messages are JSON-encoded dicts pushed to ``autobot:matrix_outbox``. Each
item carries a retry counter; failed sends are re-queued up to
``_MAX_RETRIES`` times before being dropped with an ERROR log.

The drain loop dispatches via ``bot.send_to_room`` (room IDs ``!…``) or
``bot.send_dm`` (user IDs ``@…``).
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_QUEUE_KEY = "autobot:matrix_outbox"
_MAX_QUEUE_LEN = 500
_MAX_RETRIES = 3


def _get_redis():
    from flask import current_app
    import redis as _redis
    return _redis.Redis.from_url(
        current_app.config.get("REDIS_URL", "redis://localhost:6379/0"),
        socket_timeout=1.0,
        decode_responses=True,
    )


def enqueue(target: str, body: str) -> None:
    """Push a Matrix send request onto the outbox queue.

    *target* is either a Matrix room ID (``!…``) or a user ID (``@…``).
    Non-fatal: if Redis is unavailable the error is logged and swallowed.
    """
    if not target or not body:
        return
    try:
        r = _get_redis()
        payload = json.dumps({"target": target, "body": body, "attempts": 0})
        pipe = r.pipeline()
        pipe.rpush(_QUEUE_KEY, payload)
        pipe.ltrim(_QUEUE_KEY, -_MAX_QUEUE_LEN, -1)
        pipe.execute()
    except Exception:
        logger.exception("matrix_outbox.enqueue failed for target=%s", target)


def queue_length() -> int:
    """Return the number of messages currently waiting in the outbox."""
    try:
        r = _get_redis()
        return r.llen(_QUEUE_KEY) or 0
    except Exception:
        return -1


def drain(bot, max_items: int = 50) -> int:
    """Pop up to *max_items* from the queue and dispatch via *bot*.

    Failed sends are re-queued with an incremented attempt counter. After
    ``_MAX_RETRIES`` failures the item is dropped and logged at ERROR level
    so the problem is visible in the log stream.

    Returns the number of messages successfully sent.
    """
    try:
        r = _get_redis()
    except Exception:
        logger.exception("matrix_outbox.drain: cannot connect to Redis")
        return 0

    sent = 0
    requeued = []

    for _ in range(max_items):
        raw = r.lpop(_QUEUE_KEY)
        if raw is None:
            break
        try:
            item = json.loads(raw)
            target = item.get("target", "")
            body = item.get("body", "")
            attempts = int(item.get("attempts", 0))

            if not target or not body:
                continue

            if target.startswith("!"):
                result = bot.send_to_room(target, body)
            elif target.startswith("@"):
                result = bot.send_dm(target, body)
            else:
                logger.warning("matrix_outbox: unknown target format %r, skipping", target)
                continue

            if result.get("ok"):
                sent += 1
            else:
                err = result.get("error", "unknown error")
                attempts += 1
                if attempts < _MAX_RETRIES:
                    logger.warning(
                        "matrix_outbox: delivery failed for %s (attempt %d/%d): %s — re-queuing",
                        target, attempts, _MAX_RETRIES, err,
                    )
                    requeued.append(json.dumps({"target": target, "body": body, "attempts": attempts}))
                else:
                    logger.error(
                        "matrix_outbox: DROPPED message for %s after %d attempts. "
                        "Last error: %s. Body: %.120s",
                        target, attempts, err, body,
                    )
        except Exception:
            logger.exception("matrix_outbox.drain: error processing item %r", raw)

    # Re-push failed items at the back of the queue
    if requeued:
        pipe = r.pipeline()
        for payload in requeued:
            pipe.rpush(_QUEUE_KEY, payload)
        pipe.ltrim(_QUEUE_KEY, -_MAX_QUEUE_LEN, -1)
        pipe.execute()

    return sent
