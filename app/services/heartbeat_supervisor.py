"""Heartbeat supervisor — the agent's "am I still alive?" loop.

Each tick:
  1. Build a snapshot of the agent's world (HEARTBEAT.md tasks, active
     Objectives, stuck Runs, last active channel).
  2. Decide — by rules, not by LLM — whether to act, skip, or defer.
  3. If acting, run the agent with a contextualized prompt built from the
     snapshot and route the response to the most recently-active channel.
  4. Record a HeartbeatEvent regardless of the decision.

This is deliberately separate from the scheduler (time-trigger infrastructure)
and from run_agent_non_streaming (single execution). The scheduler fires
`tick(agent_id)`; tick owns the supervisory logic.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.agent import Agent
from app.models.heartbeat_event import HeartbeatEvent
from app.models.objective import Objective
from app.models.run import Run
from app.models.session import Session

logger = logging.getLogger(__name__)

# A run stuck in "running" longer than this is considered abandoned.
STUCK_RUN_MINUTES = 15

# Minimum gap between two "act" ticks for the same agent, to avoid storming
# the model when the heartbeat interval is short and tasks are always matching.
ACT_COOLDOWN_SECONDS = 60

_INTERVAL_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def tick(agent_id: int) -> HeartbeatEvent:
    """Run one supervisor tick for an agent. Returns the recorded event.

    Must be called inside an app context.
    """
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")

    snapshot = _build_snapshot(agent)
    decision, reason, run_id = _decide_and_maybe_act(agent, snapshot)

    event = HeartbeatEvent(
        agent_id=agent.id,
        decision=decision,
        reason=reason,
        snapshot_json=snapshot,
        run_id=run_id,
    )
    db.session.add(event)
    db.session.commit()
    return event


# --------------------------- snapshot ---------------------------


def _build_snapshot(agent: Agent) -> dict:
    """Collect the state the supervisor needs to decide."""
    from app.workspace.manager import read_file

    now = datetime.now(timezone.utc)

    heartbeat_md = read_file(agent, "HEARTBEAT.md") or ""
    heartbeat_tasks = _parse_heartbeat_md(heartbeat_md)

    objectives = Objective.query.filter_by(agent_id=agent.id).filter(
        Objective.status.in_(("active", "blocked", "waiting"))
    ).all()
    due_objectives = [
        {
            "id": o.id,
            "title": o.title,
            "status": o.status,
            "due": (o.next_check_at is None) or (o.next_check_at <= now),
            "last_progress_at": o.last_progress_at.isoformat() if o.last_progress_at else None,
        }
        for o in objectives
    ]

    stuck_cutoff = now - timedelta(minutes=STUCK_RUN_MINUTES)
    stuck_runs = Run.query.filter_by(agent_id=agent.id, status="running").filter(
        Run.started_at < stuck_cutoff
    ).all()
    stuck_run_ids = [r.id for r in stuck_runs]

    last_active_session = (
        Session.query.filter_by(agent_id=agent.id)
        .order_by(Session.updated_at.desc())
        .first()
    )
    last_channel = last_active_session.channel_type if last_active_session else None
    last_session_id = last_active_session.id if last_active_session else None

    last_act_event = (
        HeartbeatEvent.query.filter_by(agent_id=agent.id, decision="act")
        .order_by(HeartbeatEvent.tick_at.desc())
        .first()
    )
    last_act_at = _as_aware_utc(last_act_event.tick_at) if last_act_event else None

    return {
        "now": now.isoformat(),
        "heartbeat_tasks": heartbeat_tasks,
        "objectives": due_objectives,
        "stuck_run_ids": stuck_run_ids,
        "last_channel": last_channel,
        "last_session_id": last_session_id,
        "last_act_at": last_act_at.isoformat() if last_act_at else None,
    }


def _as_aware_utc(dt):
    """Treat naive datetimes coming back from Postgres as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_heartbeat_md(content: str) -> list[dict]:
    """Extract list-item tasks from HEARTBEAT.md.

    Recognizes ``[done]`` marker and inline ``every: <N>[smhd]`` / ``priority: ...``.
    """
    tasks = []
    for line in content.splitlines():
        line = line.rstrip()
        m = re.match(r"^\s*-\s+(.*)$", line)
        if not m:
            continue
        body = m.group(1)
        done = "[done]" in body.lower()
        body_clean = re.sub(r"\[done\]", "", body, flags=re.IGNORECASE).strip()

        every_seconds = None
        every_m = re.search(r"every\s*:\s*(\d+)\s*([smhd])", body_clean, re.IGNORECASE)
        if every_m:
            every_seconds = int(every_m.group(1)) * _INTERVAL_UNITS[every_m.group(2).lower()]
            body_clean = body_clean[: every_m.start()].rstrip(" .,") + body_clean[every_m.end():]

        priority = "normal"
        prio_m = re.search(r"priority\s*:\s*(high|normal|low)", body_clean, re.IGNORECASE)
        if prio_m:
            priority = prio_m.group(1).lower()
            body_clean = body_clean[: prio_m.start()].rstrip(" .,") + body_clean[prio_m.end():]

        tasks.append({
            "text": body_clean.strip(" .,-"),
            "done": done,
            "every_seconds": every_seconds,
            "priority": priority,
        })
    return tasks


# --------------------------- decision ---------------------------


def _decide_and_maybe_act(agent: Agent, snapshot: dict) -> tuple[str, str, int | None]:
    now = datetime.fromisoformat(snapshot["now"])

    # Collect actionable signals.
    live_tasks = [t for t in snapshot["heartbeat_tasks"] if not t["done"] and t["text"]]
    due_objectives = [o for o in snapshot["objectives"] if o["status"] == "active" and o["due"]]
    stuck_run_ids = snapshot["stuck_run_ids"]

    if not (live_tasks or due_objectives or stuck_run_ids):
        return "skip", "nothing actionable (no tasks, no due objectives, no stuck runs)", None

    # Cooldown: if we acted very recently, defer.
    last_act = snapshot.get("last_act_at")
    if last_act:
        last_act_dt = datetime.fromisoformat(last_act)
        elapsed = (now - last_act_dt).total_seconds()
        if elapsed < ACT_COOLDOWN_SECONDS:
            return "defer", f"cooldown active ({int(elapsed)}s < {ACT_COOLDOWN_SECONDS}s)", None

    # There's already a live run for this agent — defer instead of stacking.
    live_run = Run.query.filter_by(agent_id=agent.id, status="running").first()
    if live_run is not None:
        return "defer", f"run {live_run.id} still running", None

    # Mark stuck runs as such so they stop counting next tick.
    if stuck_run_ids:
        _mark_runs_stuck(stuck_run_ids)

    prompt = _build_prompt(agent, snapshot, live_tasks, due_objectives, stuck_run_ids)

    from app.services.chat_service import run_agent_non_streaming

    channel = snapshot.get("last_channel") or "internal"
    try:
        result = run_agent_non_streaming(
            agent_id=agent.id,
            message=prompt,
            channel_type=channel,
            trigger_type="heartbeat",
        )
    except Exception as e:
        logger.exception("Heartbeat act failed for agent %s", agent.id)
        return "act", f"act raised: {e}", None

    run_id = result.get("run_id")
    reason = f"acted on {len(live_tasks)} task(s), {len(due_objectives)} objective(s), {len(stuck_run_ids)} stuck run(s)"
    return "act", reason, run_id


def _mark_runs_stuck(run_ids: list[int]) -> None:
    if not run_ids:
        return
    now = datetime.now(timezone.utc)
    for rid in run_ids:
        run = db.session.get(Run, rid)
        if run is not None and run.status == "running":
            run.status = "stuck"
            run.finished_at = now
            run.error_summary = (run.error_summary or "") + f"\n[supervisor] Marked stuck after >{STUCK_RUN_MINUTES}m without completion."
    db.session.commit()


def _build_prompt(agent: Agent, snapshot: dict, live_tasks, due_objectives, stuck_run_ids) -> str:
    parts = ["[HEARTBEAT] Supervisor tick. World state:"]
    if live_tasks:
        parts.append("\nChecklist (HEARTBEAT.md):")
        for t in live_tasks[:20]:
            parts.append(f"  - {t['text']} (priority={t['priority']})")
    if due_objectives:
        parts.append("\nActive objectives due for review:")
        for o in due_objectives[:20]:
            parts.append(f"  - [{o['id']}] {o['title']} (status={o['status']})")
    if stuck_run_ids:
        parts.append(f"\nStuck runs (flagged): {stuck_run_ids}")
    parts.append(
        "\nInstructions:"
        "\n- Act on items that actually need attention now; skip the rest."
        "\n- Be concise. Only report back if the outcome is useful to the user."
        "\n- Update MEMORY.md with any durable finding."
    )
    return "\n".join(parts)
