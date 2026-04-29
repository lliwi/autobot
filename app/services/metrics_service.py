from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.extensions import db
from app.models.agent import Agent
from app.models.run import Run
from app.models.session import Session
from app.models.tool_execution import ToolExecution


def runs_per_day(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            func.date(Run.started_at).label("day"),
            func.count(Run.id).label("count"),
        )
        .filter(Run.started_at >= since)
        .group_by(func.date(Run.started_at))
        .order_by(func.date(Run.started_at))
        .all()
    )
    return [{"day": str(r.day), "count": r.count} for r in rows]


def response_times(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            func.date(Run.started_at).label("day"),
            func.avg(Run.duration_ms).label("avg_ms"),
        )
        .filter(Run.started_at >= since, Run.duration_ms.isnot(None))
        .group_by(func.date(Run.started_at))
        .order_by(func.date(Run.started_at))
        .all()
    )
    return [{"day": str(r.day), "avg_ms": round(r.avg_ms, 1) if r.avg_ms else 0} for r in rows]


def error_counts(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            func.date(Run.started_at).label("day"),
            func.count(Run.id).label("count"),
        )
        .filter(Run.started_at >= since, Run.status == "error")
        .group_by(func.date(Run.started_at))
        .order_by(func.date(Run.started_at))
        .all()
    )
    return [{"day": str(r.day), "count": r.count} for r in rows]


def usage_by_agent(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            Agent.name.label("agent_name"),
            func.count(Run.id).label("runs"),
            func.coalesce(func.sum(Run.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(Run.estimated_cost), 0).label("total_cost"),
        )
        .join(Agent, Run.agent_id == Agent.id)
        .filter(Run.started_at >= since)
        .group_by(Agent.name)
        .order_by(func.count(Run.id).desc())
        .all()
    )
    return [
        {
            "agent_name": r.agent_name,
            "runs": r.runs,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "total_cost": round(float(r.total_cost), 4),
        }
        for r in rows
    ]


def usage_by_channel(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            Session.channel_type.label("channel"),
            func.count(Run.id).label("runs"),
            func.coalesce(func.sum(Run.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.output_tokens), 0).label("output_tokens"),
        )
        .join(Session, Run.session_id == Session.id)
        .filter(Run.started_at >= since)
        .group_by(Session.channel_type)
        .order_by(func.count(Run.id).desc())
        .all()
    )
    return [
        {"channel": r.channel or "unknown", "runs": r.runs, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}
        for r in rows
    ]


def usage_by_tool(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            ToolExecution.tool_name.label("tool_name"),
            func.count(ToolExecution.id).label("count"),
        )
        .filter(ToolExecution.started_at >= since)
        .group_by(ToolExecution.tool_name)
        .order_by(func.count(ToolExecution.id).desc())
        .all()
    )
    return [{"tool_name": r.tool_name, "count": r.count} for r in rows]


def cost_per_day(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            func.date(Run.started_at).label("day"),
            func.coalesce(func.sum(Run.estimated_cost), 0).label("cost"),
        )
        .filter(Run.started_at >= since, Run.estimated_cost.isnot(None))
        .group_by(func.date(Run.started_at))
        .order_by(func.date(Run.started_at))
        .all()
    )
    return [{"day": str(r.day), "cost": round(float(r.cost), 6)} for r in rows]


def cost_per_agent(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(
            Agent.name.label("agent_name"),
            func.coalesce(func.sum(Run.estimated_cost), 0).label("cost"),
            func.count(Run.id).label("runs"),
        )
        .join(Agent, Run.agent_id == Agent.id)
        .filter(Run.started_at >= since)
        .group_by(Agent.name)
        .order_by(func.coalesce(func.sum(Run.estimated_cost), 0).desc())
        .all()
    )
    return [
        {"agent_name": r.agent_name, "cost": round(float(r.cost), 6), "runs": r.runs}
        for r in rows
    ]


def cost_summary(days=30):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    row = (
        db.session.query(
            func.coalesce(func.sum(Run.estimated_cost), 0).label("total"),
            func.count(Run.id).label("runs"),
        )
        .filter(Run.started_at >= since)
        .one()
    )
    today = datetime.now(timezone.utc).date()
    today_row = (
        db.session.query(func.coalesce(func.sum(Run.estimated_cost), 0).label("total"))
        .filter(func.date(Run.started_at) == today)
        .one()
    )
    return {
        "total_cost": round(float(row.total), 6),
        "total_runs": row.runs,
        "today_cost": round(float(today_row.total), 6),
    }
