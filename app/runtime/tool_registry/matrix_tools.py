"""Matrix messaging tools (send DM/room, status, DM-room resolution)."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_matrix_tools():
    register(
        ToolDefinition(
            name="matrix_send",
            description=(
                "Send a direct message to a Matrix user via the bot that already runs "
                "in this process (reuses its login — no extra credentials needed). "
                "Use this to proactively deliver results from cron/heartbeat tasks to "
                "a user outside of an ongoing conversation. The target must be a full "
                "Matrix ID like '@alice:example.org'. The bot will open a DM room if "
                "one does not already exist."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Full Matrix ID, e.g. '@alice:example.org'.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Plain-text body of the message to send.",
                    },
                },
                "required": ["user_id", "message"],
            },
            handler=lambda **kwargs: _matrix_send(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="matrix_status",
            description=(
                "Return the current Matrix integration status: whether the bot is "
                "connected and logged in, how many rooms it has joined, which rooms "
                "are active, and how many messages are pending in the outbox queue. "
                "Use this to diagnose delivery problems or verify connectivity."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda **kwargs: _matrix_status(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="matrix_dm_room",
            description=(
                "Look up or create the DM room ID between the bot and a Matrix user. "
                "Returns the room_id so you can pass it to matrix_send or use it for "
                "debugging. Useful when diagnosing routing issues."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Full Matrix user ID, e.g. '@alice:example.org'.",
                    },
                },
                "required": ["user_id"],
            },
            handler=lambda **kwargs: _matrix_dm_room(**kwargs),
        )
    )


def _matrix_send(_agent=None, user_id=None, message=None, **kwargs):
    """Send a Matrix DM or room message.

    Works from any process. When the MatrixBot is available in-process the
    message is sent synchronously. Otherwise it is placed in a Redis outbox
    and dispatched by the worker's drain loop (typically within a few seconds).

    ``user_id`` accepts either a Matrix user ID (``@user:server``) for a DM
    or a room ID (``!room:server``) to post into a specific room.
    """
    if not user_id:
        return {"error": "Missing required argument 'user_id' (Matrix user ID or room ID)."}
    if not message:
        return {"error": "Missing required argument 'message'."}

    target = user_id.strip()
    is_room = target.startswith("!")
    is_user = target.startswith("@") and ":" in target
    if not is_room and not is_user:
        return {"error": f"Invalid Matrix target '{target}'. Expected '@user:server' or '!room:server'."}

    from flask import current_app

    bot = getattr(current_app, "matrix_bot", None)
    if bot is not None:
        # In-process (worker): send immediately.
        if is_room:
            return bot.send_to_room(target, message)
        return bot.send_dm(target, message)

    # Web process: enqueue via Redis outbox; worker will dispatch.
    try:
        from app.services.matrix_outbox import enqueue
        enqueue(target, message)
        return {"ok": True, "queued": True, "note": "Message queued — will be sent by the worker within seconds."}
    except Exception as e:
        return {"error": f"Failed to queue Matrix message: {e}"}


def _matrix_status(**kwargs):
    """Return Matrix integration health: connection state, rooms, outbox queue."""
    from flask import current_app

    bot = getattr(current_app, "matrix_bot", None)

    try:
        from app.services.matrix_outbox import queue_length
        pending = queue_length()
    except Exception:
        pending = -1

    if bot is None:
        return {
            "connected": False,
            "note": "MatrixBot not running in this process (web process). Check worker logs.",
            "outbox_pending": pending,
        }

    client = getattr(bot, "_client", None)
    is_ready = bot._ready.is_set() if hasattr(bot, "_ready") else False

    rooms_info = []
    if client and hasattr(client, "rooms"):
        for room_id, room in client.rooms.items():
            rooms_info.append({
                "room_id": room_id,
                "display_name": getattr(room, "display_name", None) or getattr(room, "name", None),
                "member_count": getattr(room, "member_count", None),
            })

    return {
        "connected": is_ready,
        "user_id": current_app.config.get("MATRIX_USER_ID", ""),
        "homeserver": current_app.config.get("MATRIX_HOMESERVER", ""),
        "joined_rooms": len(rooms_info),
        "rooms": rooms_info,
        "outbox_pending": pending,
        "default_agent_slug": current_app.config.get("MATRIX_DEFAULT_AGENT_SLUG", ""),
    }


def _matrix_dm_room(_agent=None, user_id=None, **kwargs):
    """Resolve (or create) the DM room between the bot and *user_id*."""
    from flask import current_app

    if not user_id:
        return {"error": "user_id is required"}

    target = user_id.strip()
    if not target.startswith("@") or ":" not in target:
        return {"error": f"Invalid Matrix user ID '{target}'. Expected '@user:server'."}

    bot = getattr(current_app, "matrix_bot", None)
    if bot is None:
        # Try Redis cache even from web process
        try:
            import redis as _redis
            r = _redis.Redis.from_url(
                current_app.config.get("REDIS_URL", "redis://localhost:6379/0"),
                socket_timeout=0.5, decode_responses=True,
            )
            cached = r.get(f"autobot:matrix:dm:{target}")
            if cached:
                return {"ok": True, "room_id": cached, "source": "redis_cache"}
        except Exception:
            pass
        return {"ok": False, "error": "MatrixBot not running in this process. Room ID may be in cache — check redis key autobot:matrix:dm:<user_id>."}

    import asyncio
    client = getattr(bot, "_client", None)
    if client is None:
        return {"ok": False, "error": "Matrix client not initialised"}

    room_id = bot._find_dm_room(client, target)
    source = "memory_or_redis"
    if room_id is None:
        future = asyncio.run_coroutine_threadsafe(
            bot._create_dm_room(client, target), bot._loop,
        )
        try:
            room_id = future.result(timeout=15)
            source = "created"
        except Exception as e:
            return {"ok": False, "error": f"Could not create DM room: {e}"}

    if room_id:
        return {"ok": True, "room_id": room_id, "source": source, "user_id": target}
    return {"ok": False, "error": "Could not resolve DM room"}
