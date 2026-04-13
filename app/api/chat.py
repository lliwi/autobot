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
