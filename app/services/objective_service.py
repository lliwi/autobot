"""Service layer for Objectives — the agent's goal-oriented work items."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.objective import Objective


ALLOWED_STATUSES = ("active", "blocked", "waiting", "done", "cancelled")


def create_objective(agent_id: int, title: str, description: str = "",
                     context: dict | None = None, next_check_at=None) -> Objective:
    obj = Objective(
        agent_id=agent_id,
        title=title,
        description=description or None,
        status="active",
        context_json=context or None,
        next_check_at=next_check_at,
    )
    db.session.add(obj)
    db.session.commit()
    return obj


def list_objectives(agent_id: int, include_done: bool = False) -> list[Objective]:
    q = Objective.query.filter_by(agent_id=agent_id)
    if not include_done:
        q = q.filter(Objective.status.in_(("active", "blocked", "waiting")))
    return q.order_by(Objective.created_at.desc()).all()


def update_objective(obj: Objective, **changes) -> Objective:
    if "title" in changes and changes["title"]:
        obj.title = changes["title"]
    if "description" in changes:
        obj.description = changes["description"] or None
    if "status" in changes and changes["status"] in ALLOWED_STATUSES:
        obj.status = changes["status"]
    if "next_check_at" in changes:
        obj.next_check_at = changes["next_check_at"]
    if "context_json" in changes:
        obj.context_json = changes["context_json"]
    db.session.commit()
    return obj


def mark_progress(obj: Objective, note: str | None = None) -> Objective:
    """Stamp last_progress_at and optionally append a note to context_json['log']."""
    obj.last_progress_at = datetime.now(timezone.utc)
    if note:
        ctx = dict(obj.context_json or {})
        log = list(ctx.get("log") or [])
        log.append({"at": obj.last_progress_at.isoformat(), "note": note})
        ctx["log"] = log[-50:]  # keep last 50 entries
        obj.context_json = ctx
    db.session.commit()
    return obj


def delete_objective(obj: Objective) -> None:
    db.session.delete(obj)
    db.session.commit()
