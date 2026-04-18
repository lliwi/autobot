"""Async review-event queue.

Producers call ``enqueue(agent_id, event_type, payload)`` from hooks when
something reviewable happens. The call is gated by ``review_service.should_review``
so only events the agent's effort level covers create a row — the queue stays
proportional to configured effort.

A worker (see ``app.worker.scheduler._drain_review_queue``) periodically calls
``process_one`` / ``process_batch`` to drain the queue: claim a pending row,
dispatch to its handler, persist the outcome on the row.

Handlers are registered in ``_HANDLERS``; each takes ``(agent, event)`` and
returns a dict ``{summary, error, review_run_id}`` (all optional). Add a new
event type by:
  1. Mapping its threshold in ``review_service.REVIEW_LEVELS``.
  2. Calling ``enqueue`` from the producer hook.
  3. Registering a handler below.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from app.extensions import db
from app.models.agent import Agent
from app.models.review_event import ReviewEvent
from app.services import review_service

logger = logging.getLogger(__name__)


# JSON schema the reviewer must emit in a trailing ```json fenced block:
#   {
#     "verdict": "LGTM" | "ADVISE" | "CRITICAL",
#     "summary": "one-sentence headline",
#     "patches": [
#       {
#         "target_path": "MEMORY.md",            # relative to agent workspace
#         "new_content": "full new file body",   # REQUIRED — reviewer must provide
#         "title": "short title",
#         "reason": "why this fixes the issue"
#       }
#     ]
#   }
_STRUCTURED_OUTPUT_TEMPLATE = (
    "After your prose answer, append EXACTLY one fenced code block tagged `json`\n"
    "matching this schema (no extra text after the block):\n"
    "```json\n"
    "{\n"
    '  "verdict": "LGTM|ADVISE|CRITICAL",\n'
    '  "summary": "one-sentence headline",\n'
    '  "patches": [\n'
    '    {"target_path": "MEMORY.md", "new_content": "...", "title": "...", "reason": "..."}\n'
    "  ]\n"
    "}\n"
    "```\n"
    "Rules: use `patches: []` when no change is needed. `target_path` must be\n"
    "relative to the audited agent's workspace. `new_content` must be the FULL\n"
    "new file body, not a diff. Security level 3 targets (app/, migrations/,\n"
    "config.py, Flask core) are forbidden — never propose patches there."
)

_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_BARE_JSON_RE = re.compile(r"\{[\s\S]*\}\s*$")

_VALID_VERDICTS = {"LGTM", "ADVISE", "CRITICAL"}


_MAX_BATCH_DEFAULT = 5


def enqueue(agent_id: int, event_type: str, payload: dict | None = None) -> ReviewEvent | None:
    """Create a pending review event if the agent's effort dial covers this type.

    Returns the new row, or ``None`` when the gate is closed (effort too low,
    unknown event, global kill switch, agent not found, or producer is the
    reviewer itself).
    """
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return None
    if not review_service.should_review(event_type, agent):
        return None
    # Don't review the reviewer's own output.
    if "review" in f"{agent.slug} {agent.name}".lower():
        return None

    event = ReviewEvent(
        agent_id=agent_id,
        event_type=event_type,
        payload_json=payload or {},
        status="pending",
    )
    db.session.add(event)
    db.session.commit()
    logger.info("review-queue enqueued event=%s agent=%s id=%s", event_type, agent_id, event.id)
    return event


def _claim_next() -> ReviewEvent | None:
    """Atomically grab the oldest pending row and mark it processing.

    Uses ``FOR UPDATE SKIP LOCKED`` so multiple workers (or multiple threads
    inside one worker) can drain concurrently without stepping on each other.
    """
    stmt = (
        select(ReviewEvent)
        .where(ReviewEvent.status == "pending")
        .order_by(ReviewEvent.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    try:
        event = db.session.execute(stmt).scalar_one_or_none()
    except Exception:
        # SQLite / other backends that don't support SKIP LOCKED fall back to a
        # plain query — races are tolerable for the smoke-test path.
        event = (
            ReviewEvent.query
            .filter_by(status="pending")
            .order_by(ReviewEvent.created_at.asc())
            .first()
        )
    if event is None:
        return None
    event.status = "processing"
    db.session.commit()
    return event


def process_one() -> bool:
    """Drain one event. Returns ``True`` if an event was processed."""
    event = _claim_next()
    if event is None:
        return False

    agent = db.session.get(Agent, event.agent_id)
    if agent is None:
        event.status = "error"
        event.error = "Agent no longer exists"
        event.processed_at = datetime.now(timezone.utc)
        db.session.commit()
        return True

    handler = _HANDLERS.get(event.event_type)
    if handler is None:
        event.status = "skipped"
        event.error = f"No handler registered for event_type={event.event_type}"
        event.processed_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.warning("review-queue no handler for event=%s id=%s", event.event_type, event.id)
        return True

    try:
        result = handler(agent, event) or {}
        event.summary = (result.get("summary") or "")[:10000] or None
        event.review_run_id = result.get("review_run_id")
        event.error = result.get("error")
        event.findings_json = result.get("findings")
        event.status = "error" if event.error else "done"
    except Exception as e:  # noqa: BLE001
        logger.exception("review-queue handler crashed for event id=%s", event.id)
        event.status = "error"
        event.error = str(e)[:2000]
    finally:
        event.processed_at = datetime.now(timezone.utc)
        db.session.commit()
    return True


def process_batch(max_items: int = _MAX_BATCH_DEFAULT) -> int:
    """Drain up to ``max_items`` events. Returns how many were processed."""
    n = 0
    for _ in range(max(1, max_items)):
        if not process_one():
            break
        n += 1
    return n


# --- Handlers ------------------------------------------------------------


def _truncate(text: str, max_chars: int = 4000) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[...truncated, full length={len(text)} chars]"


def _invoke_reviewer(reviewer: Agent, message: str, trigger_type: str = "auto_review") -> dict:
    from app.services.chat_service import run_agent_non_streaming

    result = run_agent_non_streaming(
        agent_id=reviewer.id,
        message=message,
        channel_type="internal",
        trigger_type=trigger_type,
    )
    summary = (result.get("response") or "").strip() or None
    return {
        "summary": summary,
        "error": result.get("error"),
        "review_run_id": result.get("run_id"),
    }


def _parse_findings(response_text: str) -> dict | None:
    """Extract the structured JSON block the reviewer appended to its reply.

    Returns a dict ``{verdict, summary, patches}`` or ``None`` if parsing
    fails. Handlers that don't ask for structured output should not call this.
    """
    if not response_text:
        return None
    match = _FENCED_JSON_RE.search(response_text)
    payload_str = None
    if match:
        payload_str = match.group(1).strip()
    else:
        # Be lenient: reviewers sometimes drop the fence and emit bare JSON.
        bare = _BARE_JSON_RE.search(response_text.strip())
        if bare:
            payload_str = bare.group(0).strip()
    if not payload_str:
        return None
    try:
        data = json.loads(payload_str)
    except json.JSONDecodeError as e:
        logger.info("reviewer structured output failed to parse: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    verdict = str(data.get("verdict") or "").upper().strip()
    if verdict not in _VALID_VERDICTS:
        verdict = "ADVISE"
    patches = data.get("patches") or []
    if not isinstance(patches, list):
        patches = []
    clean_patches = []
    for p in patches:
        if not isinstance(p, dict):
            continue
        target = (p.get("target_path") or "").strip()
        content = p.get("new_content")
        if not target or content is None:
            continue
        clean_patches.append({
            "target_path": target,
            "new_content": content,
            "title": (p.get("title") or f"Reviewer patch: {target}")[:240],
            "reason": (p.get("reason") or data.get("summary") or "Reviewer-proposed change")[:2000],
        })
    return {
        "verdict": verdict,
        "summary": (data.get("summary") or "").strip() or None,
        "patches": clean_patches,
    }


def _apply_findings(agent: Agent, findings: dict, review_run_id: int | None) -> list[dict]:
    """Convert reviewer-suggested patches into PatchProposal rows.

    Each patch is fed through ``patch_service.propose_change`` so existing
    security levels and approval rules apply. Returns a list of dicts we
    store on the event for the dashboard.
    """
    from app.services.patch_service import propose_change

    results = []
    for p in findings.get("patches", []):
        try:
            patch = propose_change(
                agent_id=agent.id,
                target_path=p["target_path"],
                new_content=p["new_content"],
                title=p["title"],
                reason=f"[reviewer] {p['reason']}",
                run_id=review_run_id,
            )
            results.append({
                "patch_id": patch.id,
                "target_path": patch.target_path,
                "status": patch.status,
                "security_level": patch.security_level,
            })
        except ValueError as e:
            results.append({
                "target_path": p["target_path"],
                "status": "error",
                "error": str(e),
            })
        except Exception as e:  # noqa: BLE001
            logger.exception("Failed to apply reviewer patch on %s", p["target_path"])
            results.append({
                "target_path": p["target_path"],
                "status": "error",
                "error": str(e),
            })
    return results


def _handle_run_failed(agent: Agent, event: ReviewEvent) -> dict | None:
    """Audit a run that finished with status=error.

    Payload shape: ``{"run_id": int}``. Looks up the run, summarises its
    context (trigger, duration, error) and asks the reviewer for a short
    diagnosis. Level-4+ effort.
    """
    from app.models.run import Run

    run_id = (event.payload_json or {}).get("run_id")
    if not run_id:
        return {"error": "run_failed event missing run_id"}
    run = db.session.get(Run, run_id)
    if run is None:
        return {"error": f"run {run_id} not found"}

    reviewer = review_service.find_reviewer(agent)
    if reviewer is None:
        return {"error": "No reviewer agent available"}

    err = _truncate(run.error_summary or "", 2000)
    message = (
        f"[AUTO-REVIEW] run_failed\n"
        f"Agent under review: {agent.slug} (id={agent.id})\n"
        f"Run: id={run.id} trigger={run.trigger_type} status={run.status}\n"
        f"Duration: {run.duration_ms or '-'} ms\n"
        f"Tokens: in={run.input_tokens or 0} out={run.output_tokens or 0}\n"
        f"Started: {run.started_at.isoformat() if run.started_at else '-'}\n\n"
        f"Error summary:\n---\n{err or '(empty)'}\n---\n\n"
        "You are the reviewer auditing a failed run. In <=150 words:\n"
        "1. Name the most likely root cause in plain language.\n"
        "2. Suggest ONE concrete next step the agent should try.\n"
        "3. If the fix requires editing a workspace file (MEMORY.md, SOUL.md, a\n"
        "   skill, a tool, AGENTS.md), emit it as a patch in the JSON block.\n"
        "   Level-3 targets (app/, migrations/, config.py) are forbidden.\n"
        "Do NOT call tools.\n\n"
        f"{_STRUCTURED_OUTPUT_TEMPLATE}"
    )
    result = _invoke_reviewer(reviewer, message, trigger_type="review_run_failed")
    findings = _parse_findings(result.get("summary") or "")
    if findings is not None:
        applied = _apply_findings(agent, findings, result.get("review_run_id"))
        result["findings"] = {
            "verdict": findings["verdict"],
            "summary": findings["summary"],
            "patches": applied,
        }
    return result


_HANDLERS: dict[str, Callable[[Agent, ReviewEvent], dict | None]] = {
    "run_failed": _handle_run_failed,
}
