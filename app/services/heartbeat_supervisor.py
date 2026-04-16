"""Heartbeat supervisor — the agent's "am I still alive?" loop.

Phase A is a pure rules engine that reads HEARTBEAT.md, objectives and
in-flight runs to decide act/skip/defer. Phase B adds memory across ticks so
the supervisor stops hammering objectives that aren't making progress and
stops reacting to the same world-state twice in a row.

Each tick:
  1. Build a snapshot of the agent's world (HEARTBEAT.md tasks, active
     Objectives, stuck Runs, last active channel, recent supervisor ticks,
     pending patches).
  2. Decide — by rules — whether to act, skip, or defer.
  3. If acting, run the agent with a contextualized prompt built from the
     snapshot and route the response to the most recently-active channel.
  4. After the run, update per-objective backoff counters based on whether
     last_progress_at advanced.
  5. Record a HeartbeatEvent regardless of the decision.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.agent import Agent
from app.models.heartbeat_event import HeartbeatEvent
from app.models.objective import Objective
from app.models.patch_proposal import PatchProposal
from app.models.run import Run
from app.models.session import Session

logger = logging.getLogger(__name__)

STUCK_RUN_MINUTES = 15
ACT_COOLDOWN_SECONDS = 60
# After the supervisor acts on an identical signature twice in a row without
# a successful run, hold off for longer — the world-state isn't changing.
REPEAT_DEFER_COOLDOWN = 300
# Recent-events window included in the snapshot / signature check.
RECENT_EVENT_WINDOW = 8
# After this many consecutive acts on an objective with no progress, block it
# so it requires manual attention instead of burning tokens forever.
MAX_NO_PROGRESS_BEFORE_BLOCK = 3
# Exponential backoff cap for per-objective next_check_at pushes.
MAX_OBJECTIVE_BACKOFF_SECONDS = 3600

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

    if decision == "act":
        _post_act_bookkeeping(agent, snapshot, run_id)

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
    due_objectives = []
    for o in objectives:
        ctx = o.context_json or {}
        due_objectives.append({
            "id": o.id,
            "title": o.title,
            "status": o.status,
            "due": (o.next_check_at is None) or (o.next_check_at <= now),
            "last_progress_at": o.last_progress_at.isoformat() if o.last_progress_at else None,
            "no_progress_count": int(ctx.get("heartbeat_no_progress", 0)),
        })

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

    recent_events_q = (
        HeartbeatEvent.query.filter_by(agent_id=agent.id)
        .order_by(HeartbeatEvent.tick_at.desc())
        .limit(RECENT_EVENT_WINDOW)
        .all()
    )
    recent_events = []
    for ev in recent_events_q:
        run_status = None
        if ev.run_id is not None:
            run = db.session.get(Run, ev.run_id)
            run_status = run.status if run is not None else None
        recent_events.append({
            "tick_at": ev.tick_at.isoformat() if ev.tick_at else None,
            "decision": ev.decision,
            "reason": ev.reason,
            "signature": (ev.snapshot_json or {}).get("signature"),
            "run_id": ev.run_id,
            "run_status": run_status,
        })

    pending_patches_count = PatchProposal.query.filter_by(
        agent_id=agent.id, status="pending_review"
    ).count()

    snapshot = {
        "now": now.isoformat(),
        "heartbeat_tasks": heartbeat_tasks,
        "objectives": due_objectives,
        "stuck_run_ids": stuck_run_ids,
        "last_channel": last_channel,
        "last_session_id": last_session_id,
        "last_act_at": last_act_at.isoformat() if last_act_at else None,
        "recent_events": recent_events,
        "pending_patches_count": pending_patches_count,
    }
    snapshot["signature"] = _signature(snapshot)
    return snapshot


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


def _signature(snapshot: dict) -> str:
    """A stable fingerprint of the *actionable* world-state for this tick.

    Two consecutive ticks with the same signature mean the supervisor is
    looking at the exact same problem. If acting on it didn't help, we defer.
    """
    live_tasks = sorted(
        t["text"] for t in snapshot["heartbeat_tasks"] if not t["done"] and t["text"]
    )
    due_objective_ids = sorted(
        o["id"] for o in snapshot["objectives"] if o["status"] == "active" and o["due"]
    )
    stuck_ids = sorted(snapshot["stuck_run_ids"])
    return f"tasks={live_tasks}|objs={due_objective_ids}|stuck={stuck_ids}"


# --------------------------- decision ---------------------------


def _decide_and_maybe_act(agent: Agent, snapshot: dict) -> tuple[str, str, int | None]:
    now = datetime.fromisoformat(snapshot["now"])

    live_tasks = [t for t in snapshot["heartbeat_tasks"] if not t["done"] and t["text"]]
    due_objectives = [o for o in snapshot["objectives"] if o["status"] == "active" and o["due"]]
    stuck_run_ids = snapshot["stuck_run_ids"]

    if not (live_tasks or due_objectives or stuck_run_ids):
        return "skip", "nothing actionable (no tasks, no due objectives, no stuck runs)", None

    last_act = snapshot.get("last_act_at")
    last_act_dt = datetime.fromisoformat(last_act) if last_act else None

    if last_act_dt is not None:
        elapsed = (now - last_act_dt).total_seconds()
        if elapsed < ACT_COOLDOWN_SECONDS:
            return "defer", f"cooldown active ({int(elapsed)}s < {ACT_COOLDOWN_SECONDS}s)", None

    # Phase B: same signature twice in a row without success → stretched cooldown.
    current_sig = snapshot["signature"]
    prior_acts = [e for e in snapshot["recent_events"] if e["decision"] == "act"]
    if prior_acts and last_act_dt is not None:
        same_sig_acts = [e for e in prior_acts if e.get("signature") == current_sig]
        unproductive = [
            e for e in same_sig_acts
            if e.get("run_status") in (None, "error", "stuck")
        ]
        if len(unproductive) >= 2:
            elapsed = (now - last_act_dt).total_seconds()
            if elapsed < REPEAT_DEFER_COOLDOWN:
                return (
                    "defer",
                    f"signature unchanged for {len(unproductive)} unproductive acts "
                    f"({int(elapsed)}s < {REPEAT_DEFER_COOLDOWN}s backoff)",
                    None,
                )

    live_run = Run.query.filter_by(agent_id=agent.id, status="running").first()
    if live_run is not None:
        return "defer", f"run {live_run.id} still running", None

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
    reason = (
        f"acted on {len(live_tasks)} task(s), {len(due_objectives)} objective(s), "
        f"{len(stuck_run_ids)} stuck run(s)"
    )
    if snapshot.get("pending_patches_count"):
        reason += f", {snapshot['pending_patches_count']} patch(es) pending"
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


# --------------------------- post-act bookkeeping ---------------------------


def _post_act_bookkeeping(agent: Agent, snapshot: dict, run_id: int | None) -> None:
    """After an act-run, update per-objective no-progress counters.

    Compares each objective's pre-act `last_progress_at` (captured in the
    snapshot) to the current value. If unchanged, bump the counter stored in
    `context_json['heartbeat_no_progress']` and push `next_check_at` out
    exponentially. When the counter crosses MAX_NO_PROGRESS_BEFORE_BLOCK, the
    objective is marked `blocked` so it stops firing until a human intervenes.
    """
    now = datetime.now(timezone.utc)
    for entry in snapshot.get("objectives", []):
        if entry.get("status") != "active" or not entry.get("due"):
            continue
        obj = db.session.get(Objective, entry["id"])
        if obj is None:
            continue
        pre_progress = entry.get("last_progress_at")
        post_progress = obj.last_progress_at.isoformat() if obj.last_progress_at else None

        ctx = dict(obj.context_json or {})
        if post_progress and post_progress != pre_progress:
            ctx["heartbeat_no_progress"] = 0
            ctx.pop("heartbeat_last_backoff_seconds", None)
        else:
            counter = int(ctx.get("heartbeat_no_progress", 0)) + 1
            ctx["heartbeat_no_progress"] = counter
            if counter >= MAX_NO_PROGRESS_BEFORE_BLOCK:
                obj.status = "blocked"
                ctx["heartbeat_block_reason"] = (
                    f"No progress after {counter} consecutive supervisor acts"
                )
            else:
                backoff = min(
                    ACT_COOLDOWN_SECONDS * (2 ** counter),
                    MAX_OBJECTIVE_BACKOFF_SECONDS,
                )
                ctx["heartbeat_last_backoff_seconds"] = backoff
                obj.next_check_at = now + timedelta(seconds=backoff)
        obj.context_json = ctx
    db.session.commit()


# --------------------------- prompt ---------------------------


def _build_prompt(agent: Agent, snapshot: dict, live_tasks, due_objectives, stuck_run_ids) -> str:
    parts = ["[HEARTBEAT] Supervisor tick. World state:"]
    if live_tasks:
        parts.append("\nChecklist (HEARTBEAT.md):")
        for t in live_tasks[:20]:
            parts.append(f"  - {t['text']} (priority={t['priority']})")
    if due_objectives:
        parts.append("\nActive objectives due for review:")
        for o in due_objectives[:20]:
            hint = ""
            if o.get("no_progress_count"):
                hint = f" [no_progress={o['no_progress_count']}]"
            parts.append(f"  - [{o['id']}] {o['title']} (status={o['status']}){hint}")
    if stuck_run_ids:
        parts.append(f"\nStuck runs (flagged): {stuck_run_ids}")

    pending = snapshot.get("pending_patches_count") or 0
    if pending:
        parts.append(f"\nPending patch proposals awaiting review: {pending}")

    recent = snapshot.get("recent_events") or []
    if recent:
        parts.append("\nRecent supervisor ticks (most recent first):")
        for ev in recent[:5]:
            line = f"  - {ev['tick_at']} {ev['decision']}: {ev['reason']}"
            if ev.get("run_status"):
                line += f" [run={ev['run_id']} status={ev['run_status']}]"
            parts.append(line)

    parts.append(
        "\nInstructions:"
        "\n- Act on items that actually need attention now; skip the rest."
        "\n- If a recent tick already handled an item, do NOT redo it — update MEMORY.md and move on."
        "\n- For objectives with no_progress > 0, decide if you can unblock them or mark them done/blocked."
        "\n- Be concise. Only report back if the outcome is useful to the user."
        "\n- Update MEMORY.md with any durable finding."
    )
    return "\n".join(parts)
