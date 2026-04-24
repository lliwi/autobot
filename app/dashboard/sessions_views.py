from datetime import datetime, timedelta, timezone

from flask import render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required
from sqlalchemy import func

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.message import Message
from app.models.run import Run
from app.models.session import Session


@dashboard_bp.route("/sessions")
@login_required
def sessions_list():
    agent_id = request.args.get("agent_id", type=int)
    channel = request.args.get("channel", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 40

    q = (
        db.session.query(
            Session,
            func.count(Message.id).label("msg_count"),
        )
        .outerjoin(Message, Message.session_id == Session.id)
        .group_by(Session.id)
    )
    if agent_id:
        q = q.filter(Session.agent_id == agent_id)
    if channel:
        q = q.filter(Session.channel_type == channel)
    if status_filter:
        q = q.filter(Session.status == status_filter)
    q = q.order_by(Session.updated_at.desc())

    pagination = q.paginate(page=page, per_page=per_page, error_out=False)
    rows = pagination.items  # list of (Session, msg_count)

    agents = Agent.query.order_by(Agent.name).all()
    channels = [r[0] for r in db.session.query(Session.channel_type).distinct().all()]

    return render_template(
        "dashboard/sessions_list.html",
        rows=rows,
        pagination=pagination,
        agents=agents,
        channels=channels,
        filter_agent_id=agent_id,
        filter_channel=channel,
        filter_status=status_filter,
    )


@dashboard_bp.route("/sessions/<int:session_id>")
@login_required
def session_detail(session_id):
    session = db.session.get(Session, session_id)
    if session is None:
        flash("Session not found.", "danger")
        return redirect(url_for("dashboard.sessions_list"))

    messages = (
        Message.query.filter_by(session_id=session_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    run_count = Run.query.filter_by(session_id=session_id).count()

    return render_template(
        "dashboard/session_detail.html",
        session=session,
        messages=messages,
        run_count=run_count,
    )


@dashboard_bp.route("/sessions/<int:session_id>/close", methods=["POST"])
@login_required
def session_close(session_id):
    session = db.session.get(Session, session_id)
    if session is None:
        flash("Session not found.", "danger")
        return redirect(url_for("dashboard.sessions_list"))
    if session.status != "closed":
        session.status = "closed"
        db.session.commit()
    next_url = request.form.get("next") or url_for("dashboard.sessions_list")
    flash(f"Session #{session_id} closed.", "success")
    return redirect(next_url)


@dashboard_bp.route("/sessions/close-bulk", methods=["POST"])
@login_required
def sessions_close_bulk():
    """Close a filtered set of active sessions.

    Accepts form params:
      channel   — restrict to this channel type (blank = all)
      agent_id  — restrict to this agent (blank = all)
      older_than_days — only close sessions with updated_at older than N days (0 = all)
      keep_latest — if '1', keep the most-recent session per agent (for web sessions)
    """
    channel = request.form.get("channel", "").strip()
    agent_id = request.form.get("agent_id", type=int)
    older_than_days = request.form.get("older_than_days", type=int, default=0)
    keep_latest = request.form.get("keep_latest") == "1"

    q = Session.query.filter_by(status="active")
    if channel:
        q = q.filter(Session.channel_type == channel)
    if agent_id:
        q = q.filter(Session.agent_id == agent_id)
    if older_than_days and older_than_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        q = q.filter(Session.updated_at < cutoff)

    targets = q.all()

    if keep_latest and targets:
        # Determine the latest session id per agent among the candidates.
        from collections import defaultdict
        latest: dict[int, int] = defaultdict(int)
        for s in targets:
            if s.id > latest[s.agent_id]:
                latest[s.agent_id] = s.id
        targets = [s for s in targets if s.id != latest[s.agent_id]]

    count = 0
    for s in targets:
        s.status = "closed"
        count += 1
    db.session.commit()

    flash(f"{count} session(s) closed.", "success" if count else "info")
    # Return to the list preserving any active filters
    return redirect(url_for(
        "dashboard.sessions_list",
        channel=channel,
        agent_id=agent_id or "",
    ))
