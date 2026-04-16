"""Auto-review hook: runs a reviewer sub-agent on newly created artefacts.

When an agent creates something non-trivial (a skill, a tool, a scheduled
task) we look up a reviewer sub-agent and ask it to audit the artefact
synchronously. The outcome is returned to the creator so it can be surfaced
to the user or acted on.

The reviewer is identified by naming convention: any sub-agent whose slug or
name contains the substring "review" (case-insensitive). This avoids a DB
migration and works out-of-the-box with the existing agent topology.

Disable globally by setting env ``AUTOBOT_AUTO_REVIEW=0``.
"""
import logging
import os

from app.models.agent import Agent

logger = logging.getLogger(__name__)

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


def review_creation(agent: Agent, artefact_type: str, artefact_id: str,
                    payload: str, run_id: int | None = None) -> dict | None:
    """Ask a reviewer agent to audit a freshly-created artefact.

    Returns ``None`` if no reviewer is available or auto-review is disabled.
    Returns a dict ``{reviewer, summary, error, run_id}`` otherwise.

    Invokes ``run_agent_non_streaming`` directly instead of ``delegate_task``
    because the reviewer may be a sibling rather than a sub-agent.
    """
    if not is_enabled():
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
    if not is_enabled() or not is_auto_approve_l2_enabled():
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
