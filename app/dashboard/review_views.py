"""Dashboard views for the reviewer activity queue.

Surfaces ``ReviewEvent`` rows to the operator: what fired, what the reviewer
said, which patches (if any) came out of each event, and how many tokens
each audit burned.
"""
from datetime import datetime, timedelta, timezone

from flask import render_template, request
from flask_login import login_required
from sqlalchemy import func

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.patch_proposal import PatchProposal
from app.models.review_event import ReviewEvent
from app.models.run import Run
from app.services import review_service


@dashboard_bp.route("/review")
@login_required
def review_activity():
    agent_id = request.args.get("agent_id", type=int)
    status = request.args.get("status")
    event_type = request.args.get("event_type")

    query = ReviewEvent.query
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    if status:
        query = query.filter_by(status=status)
    if event_type:
        query = query.filter_by(event_type=event_type)

    events = query.order_by(ReviewEvent.created_at.desc()).limit(100).all()

    # For each event find any patches created during its review run.
    patches_by_run: dict[int, list] = {}
    run_ids = [e.review_run_id for e in events if e.review_run_id]
    if run_ids:
        for p in PatchProposal.query.filter(PatchProposal.run_id.in_(run_ids)).all():
            patches_by_run.setdefault(p.run_id, []).append(p)

    # Tokens spent per event (from the reviewer's own Run row).
    run_tokens = {}
    if run_ids:
        for rid, itok, otok in db.session.query(Run.id, Run.input_tokens, Run.output_tokens).filter(Run.id.in_(run_ids)).all():
            run_tokens[rid] = (itok or 0) + (otok or 0)

    agents = Agent.query.order_by(Agent.name).all()

    # Aggregate stats for the last 24h.
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    agg_query = (
        db.session.query(ReviewEvent.status, func.count(ReviewEvent.id))
        .filter(ReviewEvent.created_at >= since)
    )
    if agent_id:
        agg_query = agg_query.filter(ReviewEvent.agent_id == agent_id)
    agg = dict(agg_query.group_by(ReviewEvent.status).all())

    agg_tokens = (
        db.session.query(func.coalesce(func.sum(func.coalesce(Run.input_tokens, 0) + func.coalesce(Run.output_tokens, 0)), 0))
        .select_from(ReviewEvent)
        .join(Run, Run.id == ReviewEvent.review_run_id)
        .filter(ReviewEvent.created_at >= since)
    )
    if agent_id:
        agg_tokens = agg_tokens.filter(ReviewEvent.agent_id == agent_id)
    tokens_24h = int(agg_tokens.scalar() or 0)

    event_types = [row[0] for row in db.session.query(ReviewEvent.event_type).distinct().all()]
    statuses = ["pending", "processing", "done", "error", "skipped"]

    return render_template(
        "dashboard/review_activity.html",
        events=events,
        patches_by_run=patches_by_run,
        run_tokens=run_tokens,
        agents=agents,
        event_types=event_types,
        statuses=statuses,
        filter_agent_id=agent_id,
        filter_status=status,
        filter_event_type=event_type,
        aggregate={
            "pending": agg.get("pending", 0),
            "processing": agg.get("processing", 0),
            "done": agg.get("done", 0),
            "error": agg.get("error", 0),
            "skipped": agg.get("skipped", 0),
            "tokens_24h": tokens_24h,
        },
        codex_pressure_pct=int(review_service.REVIEW_CODEX_PRESSURE_PERCENT),
    )
