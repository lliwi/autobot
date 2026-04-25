import json

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.session import Session


def _redis():
    from flask import current_app
    import redis as _r
    return _r.Redis.from_url(
        current_app.config.get("REDIS_URL", "redis://localhost:6379/0"),
        socket_timeout=1.0, decode_responses=True,
    )


def _bot_status() -> dict:
    try:
        raw = _redis().get("autobot:matrix:status")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {"connected": False, "user_id": "", "homeserver": "", "rooms": [], "last_seen": None}


def _dm_mappings() -> dict:
    """Return {user_id: room_id} from Redis DM cache."""
    try:
        r = _redis()
        keys = r.keys("autobot:matrix:dm:*")
        return {k.removeprefix("autobot:matrix:dm:"): r.get(k) for k in keys}
    except Exception:
        return {}


def _queue_length() -> int:
    try:
        return _redis().llen("autobot:matrix_outbox") or 0
    except Exception:
        return -1


@dashboard_bp.route("/matrix")
@login_required
def matrix_dashboard():
    status = _bot_status()
    dm_mappings = _dm_mappings()
    queue_len = _queue_length()

    # Enrich rooms with agent mapping
    agent_by_room: dict[str, str] = {}
    for agent in Agent.query.filter(
        db.or_(Agent.sync_matrix_room.isnot(None), Agent.forward_matrix_room.isnot(None))
    ).all():
        if agent.sync_matrix_room:
            agent_by_room[agent.sync_matrix_room] = agent.slug
        if agent.forward_matrix_room:
            agent_by_room.setdefault(agent.forward_matrix_room, agent.slug)

    default_agent = Agent.query.filter_by(matrix_default=True, status="active").first()

    # Recent Matrix sessions
    recent_sessions = (
        Session.query.filter_by(channel_type="matrix")
        .order_by(Session.updated_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "dashboard/matrix_dashboard.html",
        status=status,
        dm_mappings=dm_mappings,
        queue_len=queue_len,
        agent_by_room=agent_by_room,
        default_agent=default_agent,
        recent_sessions=recent_sessions,
        agents=Agent.query.order_by(Agent.name).all(),
    )


@dashboard_bp.route("/matrix/send", methods=["POST"])
@login_required
def matrix_send():
    target = (request.form.get("target") or "").strip()
    body = (request.form.get("body") or "").strip()
    if not target or not body:
        flash("Target and message are required.", "danger")
        return redirect(url_for("dashboard.matrix_dashboard"))
    try:
        from app.services.matrix_outbox import enqueue
        enqueue(target, body)
        flash(f"Message queued for {target}.", "success")
    except Exception as e:
        flash(f"Failed to queue: {e}", "danger")
    return redirect(url_for("dashboard.matrix_dashboard"))


@dashboard_bp.route("/matrix/queue/clear", methods=["POST"])
@login_required
def matrix_queue_clear():
    try:
        _redis().delete("autobot:matrix_outbox")
        flash("Outbox queue cleared.", "success")
    except Exception as e:
        flash(f"Failed to clear queue: {e}", "danger")
    return redirect(url_for("dashboard.matrix_dashboard"))


@dashboard_bp.route("/matrix/dm-cache/clear", methods=["POST"])
@login_required
def matrix_dm_cache_clear():
    user_id = (request.form.get("user_id") or "").strip()
    try:
        r = _redis()
        if user_id:
            r.delete(f"autobot:matrix:dm:{user_id}")
            flash(f"DM cache cleared for {user_id}.", "success")
        else:
            keys = r.keys("autobot:matrix:dm:*")
            if keys:
                r.delete(*keys)
            flash(f"Cleared {len(keys)} DM cache entries.", "success")
    except Exception as e:
        flash(f"Failed: {e}", "danger")
    return redirect(url_for("dashboard.matrix_dashboard"))


@dashboard_bp.route("/matrix/rooms/leave", methods=["POST"])
@login_required
def matrix_leave_room():
    room_id = (request.form.get("room_id") or "").strip()
    if not room_id:
        flash("room_id is required.", "danger")
        return redirect(url_for("dashboard.matrix_dashboard"))
    try:
        _redis().rpush("autobot:matrix:leave_queue", room_id)
        flash(f"Leave request queued for {room_id}.", "success")
    except Exception as e:
        flash(f"Failed to queue leave request: {e}", "danger")
    return redirect(url_for("dashboard.matrix_dashboard"))


@dashboard_bp.route("/matrix/sync-token/clear", methods=["POST"])
@login_required
def matrix_sync_token_clear():
    """Delete the saved sync token so the worker replays recent events on next start."""
    try:
        _redis().delete("autobot:matrix:next_batch")
        flash("Sync token cleared — restart the worker to replay events.", "success")
    except Exception as e:
        flash(f"Failed: {e}", "danger")
    return redirect(url_for("dashboard.matrix_dashboard"))
