from datetime import datetime, time, timezone

from flask import Response, current_app, jsonify, request, stream_with_context

from app.api import api_bp
from app.api.middleware import auth_required
from app.extensions import db
from app.models.agent import Agent
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


@api_bp.route("/chat/context")
@auth_required
def chat_context():
    """Return token usage for the current chat context.

    Used by the UI to render a live "% of budget" indicator below the input.
    Estimates what the *next* turn would cost given the existing history +
    system prompt, without a pending user message. When no session exists
    yet the numbers are zero and the client shows a dash.
    """
    from app.runtime.context_budget import (
        count_messages_tokens,
        effective_budget,
        model_context_window,
    )
    from app.runtime.context_builder import build_context

    agent_id = request.args.get("agent_id", type=int)
    if not agent_id:
        return jsonify(error="agent_id required"), 400

    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return jsonify(error="Agent not found"), 404

    session_id = request.args.get("session_id", type=int)
    if session_id is not None:
        session = db.session.get(Session, session_id)
    else:
        start_of_day = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
        session = (
            Session.query.filter_by(agent_id=agent_id, channel_type="web")
            .filter(Session.updated_at >= start_of_day)
            .order_by(Session.updated_at.desc())
            .first()
        )

    budget = effective_budget(
        model_context_window(agent.model_name, current_app.config["MAX_CONTEXT_TOKENS"]),
        current_app.config.get("CONTEXT_RESPONSE_RESERVE_TOKENS"),
    )

    if session is None:
        return jsonify(
            total_tokens=0,
            budget=budget,
            pct=0.0,
            message_count=0,
            has_session=False,
        )

    # Build with an empty user turn to get a baseline of "what's already in
    # the context before the next message". A few overhead tokens added by
    # the empty turn don't meaningfully shift the percentage.
    messages = build_context(agent, session, "")
    total = count_messages_tokens(messages)
    pct = round((total / budget) * 100, 1) if budget > 0 else 0.0

    return jsonify(
        total_tokens=total,
        budget=budget,
        pct=pct,
        message_count=len(messages),
        has_session=True,
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


@api_bp.route("/sessions/<int:session_id>/close", methods=["POST"])
@auth_required
def close_session(session_id):
    session = db.session.get(Session, session_id)
    if session is None:
        return jsonify(error="Session not found"), 404
    if session.status != "closed":
        session.status = "closed"
        db.session.commit()
    return jsonify(ok=True, session_id=session_id)


@api_bp.route("/sessions/<int:session_id>/messages")
@auth_required
def get_session_messages(session_id):
    from app.models.message import Message

    messages = (
        Message.query.filter_by(session_id=session_id).order_by(Message.created_at.asc()).all()
    )
    return jsonify([m.to_dict() for m in messages])
