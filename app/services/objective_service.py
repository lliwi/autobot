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


def get_objective(objective_id: int) -> Objective | None:
    return db.session.get(Objective, objective_id)


# --------------------------- Phase 2: plan / sub-steps ---------------------------


def set_plan(obj: Objective, steps: list[str]) -> Objective:
    """Store an ordered execution plan in context_json['plan']."""
    ctx = dict(obj.context_json or {})
    ctx["plan"] = [{"step": s, "status": "pending", "result": None} for s in steps if s]
    ctx["current_step"] = 0 if ctx["plan"] else None
    obj.context_json = ctx
    db.session.commit()
    return obj


def advance_plan(obj: Objective, result: str | None = None, status: str = "done") -> dict:
    """Mark the current plan step as done/failed and move to the next pending one.

    Advancing a step counts as progress (stamps last_progress_at).
    """
    ctx = dict(obj.context_json or {})
    plan = list(ctx.get("plan") or [])
    i = ctx.get("current_step")
    if i is None or i >= len(plan):
        return {"error": "no current step to advance"}
    plan[i] = {**plan[i], "status": status, "result": result}
    nxt = next((j for j in range(len(plan)) if plan[j]["status"] == "pending"), None)
    ctx["plan"] = plan
    ctx["current_step"] = nxt
    obj.context_json = ctx
    obj.last_progress_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"advanced": True, "completed_step": i, "next_step": nxt, "all_steps_done": nxt is None}


def plan_incomplete_steps(obj: Objective) -> list[str]:
    ctx = obj.context_json or {}
    return [p.get("step") for p in (ctx.get("plan") or []) if p.get("status") != "done"]


# --------------------------- Phase 4: definition-of-done gate ---------------------------


def complete_objective(obj: Objective, evidence: str, force: bool = False) -> dict:
    """Mark an objective done, gated on a non-empty evidence note and (unless
    forced) all plan steps being complete.
    """
    if not evidence or not evidence.strip():
        return {"error": "evidence required: describe what was done and how it was verified"}
    incomplete = plan_incomplete_steps(obj)
    if incomplete and not force:
        return {
            "error": "objective has incomplete plan steps — finish them or pass force=true",
            "incomplete_steps": incomplete,
        }
    ctx = dict(obj.context_json or {})
    ctx["completion_evidence"] = evidence
    obj.context_json = ctx
    obj.status = "done"
    obj.last_progress_at = datetime.now(timezone.utc)
    db.session.commit()
    return {"done": True, "objective_id": obj.id, "title": obj.title}


# --------------------------- Phase 5: human escalation ---------------------------


def notify_user(agent, text: str) -> bool:
    """Enqueue a message to the agent's forward_matrix_room (best-effort)."""
    room = getattr(agent, "forward_matrix_room", None)
    if not room or not text:
        return False
    try:
        from app.services.matrix_outbox import enqueue
        enqueue(room, text)
        return True
    except Exception:
        import logging
        logging.getLogger(__name__).exception("objective notify_user failed")
        return False


def set_status(obj: Objective, status: str, note: str | None = None, notify: bool = True) -> Objective:
    """Change status; when it becomes blocked/waiting, escalate to the user."""
    if status not in ALLOWED_STATUSES:
        return obj
    prev = obj.status
    obj.status = status
    if note:
        ctx = dict(obj.context_json or {})
        ctx["status_note"] = note
        obj.context_json = ctx
    db.session.commit()
    if notify and status in ("blocked", "waiting") and prev != status:
        from app.models.agent import Agent
        agent = db.session.get(Agent, obj.agent_id)
        if agent:
            label = "BLOQUEADO" if status == "blocked" else "ESPERANDO INPUT"
            notify_user(agent, f"⚠️ Objetivo [{label}]: {obj.title}" + (f"\n{note}" if note else ""))
    return obj
