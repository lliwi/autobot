"""Redis-based outbox for Matrix messages.

The web process cannot reach the MatrixBot directly (it runs only in the
worker). This module provides a thin Redis list that bridges the gap:

  - ``enqueue(room_id, body)``  — called from web or worker process
  - ``drain(bot, max_items)``   — called by the worker's polling loop

Messages are JSON-encoded dicts pushed to ``autobot:matrix_outbox``. The
drain loop pops them one-by-one and dispatches via ``bot.send_to_room`` or
``bot.send_dm`` depending on whether the target looks like a room ID or a
user ID.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_QUEUE_KEY = "autobot:matrix_outbox"
_MAX_QUEUE_LEN = 500   # cap to prevent unbounded growth on idle workers


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
    Non-fatal: if Redis is unavailable the error is logged and swallowed so
    the agent turn doesn't fail just because Matrix is down.
    """
    if not target or not body:
        return
    try:
        r = _get_redis()
        payload = json.dumps({"target": target, "body": body})
        pipe = r.pipeline()
        pipe.rpush(_QUEUE_KEY, payload)
        pipe.ltrim(_QUEUE_KEY, -_MAX_QUEUE_LEN, -1)
        pipe.execute()
    except Exception:
        logger.exception("matrix_outbox.enqueue failed for target=%s", target)


def drain(bot, max_items: int = 50) -> int:
    """Pop up to *max_items* from the queue and dispatch via *bot*.

    Returns the number of messages sent. Called from the worker's polling loop.
    """
    try:
        r = _get_redis()
    except Exception:
        logger.exception("matrix_outbox.drain: cannot connect to Redis")
        return 0

    sent = 0
    for _ in range(max_items):
        raw = r.lpop(_QUEUE_KEY)
        if raw is None:
            break
        try:
            item = json.loads(raw)
            target = item.get("target", "")
            body = item.get("body", "")
            if not target or not body:
                continue
            if target.startswith("!"):
                result = bot.send_to_room(target, body)
            elif target.startswith("@"):
                result = bot.send_dm(target, body)
            else:
                logger.warning("matrix_outbox: unknown target format %r, skipping", target)
                continue
            if not result.get("ok"):
                logger.warning("matrix_outbox: send failed for %s: %s", target, result.get("error"))
            else:
                sent += 1
        except Exception:
            logger.exception("matrix_outbox.drain: error processing item %r", raw)
    return sent
