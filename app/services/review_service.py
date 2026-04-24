"""Auto-review hook: runs a reviewer sub-agent on newly created artefacts.

When an agent creates something non-trivial (a skill, a tool, a scheduled
task) we look up a reviewer sub-agent and ask it to audit the artefact
synchronously. The outcome is returned to the creator so it can be surfaced
to the user or acted on.

The reviewer is identified by naming convention: any sub-agent whose slug or
name contains the substring "review" (case-insensitive). This avoids a DB
migration and works out-of-the-box with the existing agent topology.

Effort dial:
    Each Agent row has ``review_effort`` on a 0–10 scale. Every reviewable
    event type is assigned a threshold in ``REVIEW_LEVELS``; an event fires
    its reviewer only when the creator's ``review_effort`` is >= that
    threshold. 0 disables reviewing entirely for that agent; 10 enables the
    full audit trail. Intermediate values enable categories gradually so the
    token cost scales smoothly.

Disable globally by setting env ``AUTOBOT_AUTO_REVIEW=0``.
"""
import logging
import os

from app.models.agent import Agent

logger = logging.getLogger(__name__)


# --- Effort dial ---------------------------------------------------------
#
# Level at which each reviewable event starts firing. Add events here as new
# hooks get wired in; anything not listed is effectively level 11 (never
# reviewed automatically).
REVIEW_LEVELS: dict[str, int] = {
    # Level 1 — creation artefacts the reviewer already audits today.
    "skill_created": 1,
    "tool_created": 1,
    # Level 2 — scheduled task creation.
    "scheduled_task_created": 2,
    # Level 3 — level-2 patch proposals vote (gate).
    "patch_l2_proposed": 3,
    # Level 4 — failed runs (any trigger, status != completed).
    "run_failed": 4,
    # Level 5 — scheduled-task results (post-run audit of cron/heartbeat).
    "scheduled_task_result": 5,
    # Level 6 — tool execution errors (workspace tool returned an error).
    "tool_execution_error": 6,
    # Level 7 — sampled chats. Sampling ratio is derived in the caller from
    # (level - 6), so higher level = more frequent sampling.
    "chat_sampled": 7,
    # Level 8 — skill execution results (post-run, if we can identify that
    # a skill was the primary driver).
    "skill_execution_result": 8,
    # Level 9 — every completed chat turn.
    "chat_turn": 9,
    # Level 10 — every run, every tool call. Audit mode.
    "run_completed": 10,
    "tool_execution_any": 10,
}


def should_review(event_type: str, agent: Agent) -> bool:
    """Return True when this agent's effort level covers this event type.

    Falls back to the baseline ``is_enabled()`` gate (env-level kill switch),
    so a global off switch still works without having to edit every caller.
    An unknown event type is treated as effectively-off (threshold 11) to
    keep new events opt-in. In addition the gate closes whenever:

    - the agent's per-day review token budget has been spent, or
    - Codex subscription quota is under heavy pressure (primary_used_percent
      above ``REVIEW_CODEX_PRESSURE_PERCENT``). Both cuts prevent a stuck or
      chatty reviewer from eating the subscription window.
    """
    if not is_enabled():
        return False
    if agent is None:
        return False
    effort = int(getattr(agent, "review_effort", 0) or 0)
    if effort <= 0:
        return False
    threshold = REVIEW_LEVELS.get(event_type, 11)
    if effort < threshold:
        return False
    if _codex_quota_over_pressure():
        logger.info(
            "should_review=False: Codex quota over pressure threshold for agent=%s event=%s",
            getattr(agent, "slug", "?"), event_type,
        )
        return False
    if _agent_over_review_budget(agent):
        logger.info(
            "should_review=False: agent %s has spent its daily review budget",
            getattr(agent, "slug", "?"),
        )
        return False
    return True


# --- Budget / quota gates ------------------------------------------------

# Once Codex says we've burned more than this percent of the primary window,
# we freeze the reviewer until it resets. Primary is the short 5-hour window
# so this is the one that matters for interactive usage.
REVIEW_CODEX_PRESSURE_PERCENT: float = float(
    os.environ.get("AUTOBOT_REVIEW_CODEX_PRESSURE_PCT", "85")
)


def _codex_quota_over_pressure() -> bool:
    """Return True when the latest Codex snapshot's primary_used_percent is
    at or above ``REVIEW_CODEX_PRESSURE_PERCENT``. Missing snapshot = not over.
    """
    from app.extensions import db
    from app.models.codex_quota_snapshot import CodexQuotaSnapshot

    try:
        row = db.session.get(CodexQuotaSnapshot, 1)
    except Exception:
        return False
    if row is None or row.primary_used_percent is None:
        return False
    return float(row.primary_used_percent) >= REVIEW_CODEX_PRESSURE_PERCENT


def _agent_over_review_budget(agent: Agent) -> bool:
    """Return True when ``agent`` has exhausted its daily review token budget.

    Budget is a per-UTC-day cap on the sum of input+output tokens across all
    Run rows linked to a ReviewEvent for this agent. A ``None`` budget means
    unlimited. The query is cheap (indexed on agent_id + started_at).
    """
    budget = getattr(agent, "review_token_budget_daily", None)
    if not budget:
        return False
    spent = _review_tokens_today(agent.id)
    return spent >= int(budget)


def _review_tokens_today(agent_id: int) -> int:
    """Sum today's reviewer token spend for ``agent_id`` (UTC day).

    Joins ``review_events`` → ``runs`` so we only count tokens burned by the
    reviewer on this agent's behalf, not the agent's own tokens.
    """
    from datetime import datetime, timezone as _tz

    from sqlalchemy import func

    from app.extensions import db
    from app.models.review_event import ReviewEvent
    from app.models.run import Run

    start_of_day = datetime.now(_tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        total = (
            db.session.query(
                func.coalesce(func.sum(func.coalesce(Run.input_tokens, 0) + func.coalesce(Run.output_tokens, 0)), 0)
            )
            .select_from(ReviewEvent)
            .join(Run, Run.id == ReviewEvent.review_run_id)
            .filter(
                ReviewEvent.agent_id == agent_id,
                ReviewEvent.created_at >= start_of_day,
            )
            .scalar()
        )
    except Exception:
        logger.exception("Failed to compute review-token usage for agent %s", agent_id)
        return 0
    return int(total or 0)


def review_budget_status(agent: Agent) -> dict:
    """Dashboard helper. Returns today's token spend + budget headroom."""
    budget = getattr(agent, "review_token_budget_daily", None)
    spent = _review_tokens_today(agent.id) if agent and agent.id else 0
    remaining = None if budget is None else max(0, int(budget) - spent)
    return {
        "budget_daily": budget,
        "spent_today": spent,
        "remaining": remaining,
        "over_budget": (budget is not None and spent >= int(budget)),
        "codex_pressure": _codex_quota_over_pressure(),
    }

_REVIEW_BUDGET_CHARS = 4000
_REVIEW_INSTRUCTIONS = (
    "You are the reviewer. Audit the artefact below. Point out concrete bugs, "
    "security risks, missing validation, unclear naming, or improvement ideas. "
    "Be specific and brief (max ~120 words). Do NOT call tools. If the artefact "
    "looks fine, say 'LGTM' and explain in one sentence why."
)

_PATCH_REVIEW_INSTRUCTIONS = (
    "You are the reviewer gatekeeping a patch to an existing workspace file. "
    "Your FIRST line must be exactly `APPROVE` or `REJECT` (one word, uppercase). "
    "Then 1-5 short bullet points explaining why. Approve only if the patch is "
    "safe, coherent, and does not introduce obvious bugs, security risks, or "
    "regressions. Reject on any real concern. Do NOT call tools."
)

_APPROVE_RE = None
_REJECT_RE = None


def _compile_verdict_regex():
    import re
    global _APPROVE_RE, _REJECT_RE
    if _APPROVE_RE is None:
        _APPROVE_RE = re.compile(r"^\s*APPROVE\b", re.IGNORECASE)
        _REJECT_RE = re.compile(r"^\s*REJECT\b", re.IGNORECASE)
    return _APPROVE_RE, _REJECT_RE


def is_auto_approve_l2_enabled() -> bool:
    return os.environ.get("AUTOBOT_AUTO_APPROVE_L2", "1") not in ("0", "false", "False", "")


def is_enabled() -> bool:
    return os.environ.get("AUTOBOT_AUTO_REVIEW", "1") not in ("0", "false", "False", "")


def find_reviewer(agent: Agent) -> Agent | None:
    """Return the active agent that should review artefacts produced by ``agent``.

    Lookup order:
      1. Active sub-agent whose slug/name contains "review".
      2. Any other active agent whose slug/name contains "review" (excluding
         ``agent`` itself).

    This avoids requiring a strict parent-child topology: a shared reviewer
    agent can audit the output of multiple creators.
    """
    if agent is None or agent.id is None:
        return None

    def _looks_like_reviewer(a: Agent) -> bool:
        return "review" in f"{a.slug} {a.name}".lower()

    children = (
        Agent.query
        .filter_by(parent_agent_id=agent.id, status="active")
        .order_by(Agent.id)
        .all()
    )
    for child in children:
        if _looks_like_reviewer(child):
            return child

    siblings = (
        Agent.query
        .filter_by(status="active")
        .filter(Agent.id != agent.id)
        .order_by(Agent.id)
        .all()
    )
    for candidate in siblings:
        if _looks_like_reviewer(candidate):
            return candidate
    return None


_ARTEFACT_EVENT_MAP = {
    "skill": "skill_created",
    "tool": "tool_created",
    "scheduled_task": "scheduled_task_created",
}


def review_creation(agent: Agent, artefact_type: str, artefact_id: str,
                    payload: str, run_id: int | None = None) -> dict | None:
    """Ask a reviewer agent to audit a freshly-created artefact.

    Returns ``None`` if no reviewer is available or the creator's
    ``review_effort`` level doesn't cover this artefact type. Returns a dict
    ``{reviewer, summary, error, run_id}`` otherwise.

    Invokes ``run_agent_non_streaming`` directly instead of ``delegate_task``
    because the reviewer may be a sibling rather than a sub-agent.
    """
    event_type = _ARTEFACT_EVENT_MAP.get(artefact_type, f"{artefact_type}_created")
    if not should_review(event_type, agent):
        return None
    reviewer = find_reviewer(agent)
    if reviewer is None:
        return None
    if "review" in f"{agent.slug} {agent.name}".lower():
        # The reviewer's own outputs shouldn't be reviewed by itself.
        return None

    payload_trimmed = payload if len(payload) <= _REVIEW_BUDGET_CHARS else (
        payload[:_REVIEW_BUDGET_CHARS] + f"\n\n[...truncated, full length={len(payload)} chars]"
    )
    message = (
        f"[AUTO-REVIEW] {artefact_type}: {artefact_id}\n"
        f"Created by agent: {agent.slug}\n\n"
        f"{_REVIEW_INSTRUCTIONS}\n\n"
        f"---\n{payload_trimmed}\n---"
    )

    from app.extensions import db
    from app.models.run import Run
    from app.services.chat_service import run_agent_non_streaming

    from app.services.session_service import close_session

    try:
        result = run_agent_non_streaming(
            agent_id=reviewer.id,
            message=message,
            channel_type="internal",
            trigger_type="auto_review",
        )
    except Exception as e:
        logger.exception("Auto-review failed for %s=%s", artefact_type, artefact_id)
        return {"reviewer": reviewer.slug, "error": str(e), "summary": None, "run_id": None}

    if result.get("session_id"):
        close_session(result["session_id"])

    child_run_id = result.get("run_id")
    if run_id and child_run_id:
        child_run = db.session.get(Run, child_run_id)
        if child_run is not None:
            child_run.parent_run_id = run_id
            db.session.commit()

    return {
        "reviewer": reviewer.slug,
        "summary": (result.get("response") or "").strip() or None,
        "error": result.get("error"),
        "run_id": child_run_id,
    }


def review_patch(agent: Agent, target_path: str, diff_text: str,
                 new_content: str, reason: str,
                 run_id: int | None = None) -> dict | None:
    """Ask a reviewer agent to vote on a pending level-2 patch.

    Returns ``None`` if no reviewer is available, auto-review is disabled, or
    the agent itself is the reviewer. Returns a dict
    ``{approve, summary, error, run_id, reviewer}`` otherwise. ``approve`` is
    True only when the reviewer emitted an explicit ``APPROVE`` verdict.
    """
    if not should_review("patch_l2_proposed", agent):
        return None
    if not is_auto_approve_l2_enabled():
        return None
    reviewer = find_reviewer(agent)
    if reviewer is None:
        return None
    if "review" in f"{agent.slug} {agent.name}".lower():
        return None

    diff_trimmed = diff_text if len(diff_text) <= _REVIEW_BUDGET_CHARS else (
        diff_text[:_REVIEW_BUDGET_CHARS] + f"\n\n[...diff truncated, full length={len(diff_text)} chars]"
    )
    content_preview = new_content if len(new_content) <= _REVIEW_BUDGET_CHARS else (
        new_content[:_REVIEW_BUDGET_CHARS] + f"\n\n[...file truncated, full length={len(new_content)} chars]"
    )
    message = (
        f"[PATCH-REVIEW] {target_path}\n"
        f"Proposed by agent: {agent.slug}\n"
        f"Stated reason: {reason}\n\n"
        f"{_PATCH_REVIEW_INSTRUCTIONS}\n\n"
        f"Unified diff:\n```diff\n{diff_trimmed}\n```\n\n"
        f"Full proposed file:\n```\n{content_preview}\n```"
    )

    from app.extensions import db
    from app.models.run import Run
    from app.services.chat_service import run_agent_non_streaming

    from app.services.session_service import close_session

    try:
        result = run_agent_non_streaming(
            agent_id=reviewer.id,
            message=message,
            channel_type="internal",
            trigger_type="patch_review",
        )
    except Exception as e:
        logger.exception("Patch review failed for %s", target_path)
        return {"reviewer": reviewer.slug, "approve": False,
                "summary": None, "error": str(e), "run_id": None}

    if result.get("session_id"):
        close_session(result["session_id"])

    child_run_id = result.get("run_id")
    if run_id and child_run_id:
        child_run = db.session.get(Run, child_run_id)
        if child_run is not None:
            child_run.parent_run_id = run_id
            db.session.commit()

    summary = (result.get("response") or "").strip()
    first_line = summary.splitlines()[0] if summary else ""
    approve_re, reject_re = _compile_verdict_regex()
    approve = bool(approve_re.match(first_line)) and not reject_re.match(first_line)

    return {
        "reviewer": reviewer.slug,
        "approve": approve,
        "summary": summary or None,
        "error": result.get("error"),
        "run_id": child_run_id,
    }
