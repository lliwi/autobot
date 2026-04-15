from datetime import datetime, time, timezone

from flask import Response, jsonify, request, stream_with_context

from app.api import api_bp
from app.api.middleware import auth_required
from app.extensions import db
from app.models.session import Session


@api_bp.route("/chat", methods=["POST"])
@auth_required
def chat():
    data = request.get_json()
    if not data or "agent_id" not in data or "message" not in data:
        return jsonify(error="agent_id and message required"), 400

    from app.services.chat_service import stream_response

    def generate():
        for chunk in stream_response(
            agent_id=data["agent_id"],
            message=data["message"],
            session_id=data.get("session_id"),
        ):
            yield f"data: {chunk}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api_bp.route("/chat/history")
@auth_required
def chat_history():
    """Return today's most recent web session for an agent and its messages."""
    from app.models.message import Message

    agent_id = request.args.get("agent_id", type=int)
    if not agent_id:
        return jsonify(error="agent_id required"), 400

    start_of_day = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)

    session = (
        Session.query.filter_by(agent_id=agent_id, channel_type="web")
        .filter(Session.updated_at >= start_of_day)
        .order_by(Session.updated_at.desc())
        .first()
    )
    if session is None:
        return jsonify(session=None, messages=[])

    messages = (
        Message.query.filter_by(session_id=session.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return jsonify(
        session=session.to_dict(),
        messages=[m.to_dict() for m in messages],
    )


@api_bp.route("/sessions")
@auth_required
def list_sessions():
    sessions = Session.query.order_by(Session.created_at.desc()).limit(50).all()
    return jsonify([s.to_dict() for s in sessions])


@api_bp.route("/sessions/<int:session_id>")
@auth_required
def get_session(session_id):
    session = db.session.get(Session, session_id)
    if session is None:
        return jsonify(error="Session not found"), 404
    return jsonify(session.to_dict())


@api_bp.route("/sessions/<int:session_id>/messages")
@auth_required
def get_session_messages(session_id):
    from app.models.message import Message

    messages = (
        Message.query.filter_by(session_id=session_id).order_by(Message.created_at.asc()).all()
    )
    return jsonify([m.to_dict() for m in messages])
