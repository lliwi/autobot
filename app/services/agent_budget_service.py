"""Per-agent daily budget enforcement for token and cost caps."""
from datetime import datetime, timezone

from sqlalchemy import func

from app.extensions import db
from app.models.run import Run


def today_usage(agent_id: int) -> dict:
    """Return today's total tokens and cost for an agent (UTC day)."""
    today = datetime.now(timezone.utc).date()
    row = (
        db.session.query(
            func.coalesce(func.sum(Run.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(Run.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(Run.estimated_cost), 0).label("cost"),
        )
        .filter(
            Run.agent_id == agent_id,
            func.date(Run.started_at) == today,
            Run.status.in_(["completed", "error"]),
        )
        .one()
    )
    return {
        "tokens": int(row.input_tokens) + int(row.output_tokens),
        "cost": float(row.cost),
    }


def check_budget(agent) -> str | None:
    """Return an error message if the agent has exceeded its daily budget, else None."""
    if agent.daily_token_budget is None and agent.daily_cost_budget is None:
        return None
    usage = today_usage(agent.id)
    if agent.daily_token_budget and usage["tokens"] >= agent.daily_token_budget:
        return (
            f"Daily token budget exceeded ({usage['tokens']:,} / {agent.daily_token_budget:,} tokens). "
            "Runs are paused until midnight UTC."
        )
    if agent.daily_cost_budget and usage["cost"] >= agent.daily_cost_budget:
        return (
            f"Daily cost budget exceeded (${usage['cost']:.4f} / ${agent.daily_cost_budget:.4f}). "
            "Runs are paused until midnight UTC."
        )
    return None
