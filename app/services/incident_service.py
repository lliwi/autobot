"""Incident autopilot: detect → diagnose → draft Issue/PR → (human) approve.

Flow:
  1. A logging handler / run hook calls :func:`enqueue` with an ERROR/CRITICAL
     event. Cheap Redis ``SET NX`` dedup throttles floods of the same signature.
  2. The worker drains the Redis queue and calls :func:`ingest`, which creates
     (or bumps) an ``IncidentReport`` with authoritative DB-level dedup.
  3. :func:`process_new` asks a reviewer agent to diagnose each new incident and
     draft either a GitHub Issue or a PR. The draft sits in ``awaiting_approval``.
  4. A human approves from the dashboard → :func:`approve` opens the Issue/PR on
     GitHub via :mod:`app.services.github_service`.

Nothing reaches GitHub without explicit human approval (per product decision).
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from flask import current_app

from app.extensions import db
from app.models.agent import Agent
from app.models.incident_report import IncidentReport

logger = logging.getLogger(__name__)

REDIS_QUEUE_KEY = "autobot:incidents:queue"
_REDIS_DEDUP_PREFIX = "autobot:incidents:seen:"
_TERMINAL = ("approved", "dismissed", "failed")

_FENCED_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# --------------------------------------------------------------------------- #
# Signature / dedup
# --------------------------------------------------------------------------- #

def signature_for(message: str, source: str | None) -> str:
    from app.services.error_analysis_service import normalize_error

    norm = normalize_error(message)
    raw = f"{source or ''}|{norm}"
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:64]


def _redis():
    try:
        import redis
        url = current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
        return redis.Redis.from_url(url, socket_timeout=0.5, decode_responses=True)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Detection-side (called from logging handler / hooks)
# --------------------------------------------------------------------------- #

def enqueue(severity: str, source: str, title: str, message: str,
            traceback: str | None = None, agent_id: int | None = None) -> bool:
    """Throttle + push an incident onto the Redis queue. Returns True if pushed.

    Best-effort and exception-safe — detection must never break the caller
    (it runs inside a logging handler). The cheap Redis ``SET NX`` here only
    prevents flood enqueues; authoritative dedup happens later in :func:`ingest`.
    """
    try:
        r = _redis()
        if r is None:
            return False
        sig = signature_for(message, source)
        cooldown_h = int(current_app.config.get("INCIDENT_DEDUP_COOLDOWN_HOURS", 12))
        # NX = only set if absent; EX = cooldown window in seconds.
        if not r.set(_REDIS_DEDUP_PREFIX + sig, "1", nx=True, ex=cooldown_h * 3600):
            return False
        payload = json.dumps({
            "severity": severity,
            "source": source,
            "title": title[:300],
            "message": (message or "")[:8000],
            "traceback": (traceback or "")[:16000],
            "agent_id": agent_id,
            "signature": sig,
        })
        r.lpush(REDIS_QUEUE_KEY, payload)
        return True
    except Exception:
        return False


def drain_queue(max_items: int = 20) -> int:
    """Pop queued detections, persist them as incidents, then diagnose new ones.

    Runs in the worker (has app context). Returns how many were diagnosed.
    """
    r = _redis()
    if r is None:
        return 0
    for _ in range(max(1, max_items)):
        raw = r.rpop(REDIS_QUEUE_KEY)
        if not raw:
            break
        try:
            data = json.loads(raw)
            ingest(
                severity=data.get("severity") or "error",
                source=data.get("source"),
                title=data.get("title") or "(sin título)",
                message=data.get("message"),
                traceback=data.get("traceback"),
                agent_id=data.get("agent_id"),
                signature=data.get("signature"),
            )
        except Exception:
            logger.debug("incident drain: skipped malformed payload")
    return process_new(max_items=max_items)


def ingest(*, severity, source, message, title=None, traceback=None,
           agent_id=None, signature=None) -> IncidentReport | None:
    """Create or bump an IncidentReport. DB-level dedup within the cooldown.

    Returns the (possibly newly created) row, or the bumped existing one.
    ``title`` defaults to the first line of ``message``.
    """
    title = (title or (message or "").strip().splitlines()[0] if (message or "").strip() else None) or "(incident)"
    sig = signature or signature_for(message, source)
    cooldown_h = int(current_app.config.get("INCIDENT_DEDUP_COOLDOWN_HOURS", 12))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=cooldown_h)

    existing = (
        IncidentReport.query
        .filter(IncidentReport.signature == sig)
        .filter(IncidentReport.created_at >= cutoff)
        .order_by(IncidentReport.id.desc())
        .first()
    )
    if existing is not None:
        existing.occurrences = (existing.occurrences or 1) + 1
        existing.last_seen_at = datetime.now(timezone.utc)
        db.session.commit()
        return existing

    incident = IncidentReport(
        agent_id=agent_id,
        signature=sig,
        severity=severity if severity in ("error", "critical") else "error",
        source=source,
        title=title[:300],
        message=message,
        traceback=traceback,
        status="new",
        occurrences=1,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.session.add(incident)
    db.session.commit()
    logger.info("incident created id=%s sig=%s source=%s", incident.id, sig[:8], source)
    return incident


# --------------------------------------------------------------------------- #
# Diagnosis (reviewer agent)
# --------------------------------------------------------------------------- #

_PROMPT = """[INCIDENT AUTOPILOT] You are the on-call reviewer. An automated detector \
raised this {severity} incident. Diagnose it and draft ONE remediation that a \
human will approve before anything is opened on GitHub.

Source: {source}
Occurrences: {occurrences}
Title: {title}

Message / error:
---
{message}
---
Traceback (may be empty):
---
{traceback}
---

Decide the remediation:
- "pr": ONLY if you are confident of a concrete, bounded fix to a SINGLE file and \
you can provide its full new content. Prefer this for clear code bugs.
- "issue": when human judgement is needed, the root cause is unclear, or the fix \
spans multiple files. This is the safe default.
- "none": if it's benign/transient and needs no action.

Do NOT call any tools. Reply with a short human-readable diagnosis, then a single \
fenced JSON block:

```json
{{
  "diagnosis": "<=120 words root-cause analysis",
  "action": "issue" | "pr" | "none",
  "title": "concise Issue/PR title",
  "body": "Markdown body: what happened, root cause, proposed fix, validation steps",
  "patch": {{ "target_path": "path/from/repo/root.py", "new_content": "FULL new file content" }}
}}
```
Include "patch" ONLY when action is "pr". Omit it otherwise."""


def process_new(max_items: int = 20) -> int:
    incidents = (
        IncidentReport.query
        .filter(IncidentReport.status == "new")
        .order_by(IncidentReport.id.asc())
        .limit(max_items)
        .all()
    )
    n = 0
    for inc in incidents:
        try:
            diagnose(inc.id)
            n += 1
        except Exception:
            logger.exception("incident diagnosis failed id=%s", inc.id)
    return n


def diagnose(incident_id: int) -> IncidentReport | None:
    incident = db.session.get(IncidentReport, incident_id)
    if incident is None or incident.status not in ("new", "diagnosing"):
        return incident

    reviewer = _pick_reviewer(incident.agent_id)
    if reviewer is None:
        incident.status = "awaiting_approval"
        incident.proposed_action = "issue"
        incident.proposed_title = f"[autobot] {incident.title}"[:240]
        incident.proposed_body = _fallback_body(incident)
        incident.diagnosis = "No reviewer agent available — drafted a plain Issue from the raw error."
        db.session.commit()
        return incident

    incident.status = "diagnosing"
    db.session.commit()

    from app.services.chat_service import run_agent_non_streaming
    message = _PROMPT.format(
        severity=incident.severity,
        source=incident.source or "-",
        occurrences=incident.occurrences,
        title=incident.title,
        message=(incident.message or "(empty)")[:4000],
        traceback=(incident.traceback or "(none)")[:4000],
    )
    result = run_agent_non_streaming(
        agent_id=reviewer.id, message=message,
        channel_type="internal", trigger_type="incident_review",
    )
    parsed = _parse(result.get("response") or "")

    incident.review_run_id = result.get("run_id")
    if parsed is None:
        # Reviewer produced nothing usable: fall back to an Issue draft.
        incident.proposed_action = "issue"
        incident.proposed_title = f"[autobot] {incident.title}"[:240]
        incident.proposed_body = _fallback_body(incident)
        incident.diagnosis = (result.get("response") or "").strip()[:4000] or "No diagnosis produced."
    else:
        incident.diagnosis = parsed["diagnosis"]
        incident.proposed_action = parsed["action"]
        incident.proposed_title = parsed["title"]
        incident.proposed_body = parsed["body"]
        incident.proposed_patch_json = parsed["patch"]
    incident.status = "awaiting_approval"
    db.session.commit()
    logger.info("incident diagnosed id=%s action=%s", incident.id, incident.proposed_action)
    return incident


def _parse(text: str) -> dict | None:
    if not text:
        return None
    m = _FENCED_JSON_RE.search(text) or _BARE_JSON_RE.search(text)
    if not m:
        return None
    raw = m.group(1) if m.re is _FENCED_JSON_RE else m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "issue").lower().strip()
    if action not in ("issue", "pr", "none"):
        action = "issue"
    patch = None
    if action == "pr":
        p = data.get("patch") or {}
        target = (p.get("target_path") or "").strip()
        content = p.get("new_content")
        if target and content is not None:
            patch = {"target_path": target, "new_content": content}
        else:
            action = "issue"  # PR requested without a usable patch → downgrade
    return {
        "diagnosis": (data.get("diagnosis") or "").strip()[:4000],
        "action": action,
        "title": (data.get("title") or "").strip()[:240] or "[autobot] incident",
        "body": (data.get("body") or "").strip(),
        "patch": patch,
    }


def _fallback_body(incident: IncidentReport) -> str:
    return (
        f"**Auto-detected {incident.severity} incident**\n\n"
        f"- Source: `{incident.source or '-'}`\n"
        f"- Occurrences: {incident.occurrences}\n\n"
        f"### Error\n```\n{(incident.message or '')[:3000]}\n```\n\n"
        + (f"### Traceback\n```\n{incident.traceback[:5000]}\n```\n" if incident.traceback else "")
    )


def _pick_reviewer(agent_id: int | None) -> Agent | None:
    if agent_id:
        from app.services.review_service import find_reviewer
        agent = db.session.get(Agent, agent_id)
        if agent is not None:
            r = find_reviewer(agent)
            if r is not None:
                return r
    # System-wide incident (or no dedicated reviewer): any active "review" agent,
    # else any active agent at all.
    review = (
        Agent.query.filter_by(status="active")
        .filter(db.or_(Agent.slug.ilike("%review%"), Agent.name.ilike("%review%")))
        .order_by(Agent.id).first()
    )
    return review or Agent.query.filter_by(status="active").order_by(Agent.id).first()


# --------------------------------------------------------------------------- #
# Human-gated resolution
# --------------------------------------------------------------------------- #

def approve(incident_id: int) -> tuple[IncidentReport | None, str | None]:
    """Open the drafted Issue/PR on GitHub. Returns (incident, error)."""
    incident = db.session.get(IncidentReport, incident_id)
    if incident is None:
        return None, "Incident not found"
    if incident.status != "awaiting_approval":
        return incident, f"Incident is '{incident.status}', not awaiting approval"

    from app.services import github_service
    if not github_service.is_configured():
        return incident, "GitHub no está configurado (GH_TOKEN / AUTOBOT_GITHUB_REPO)."

    action = incident.proposed_action or "issue"
    title = incident.proposed_title or f"[autobot] {incident.title}"
    body = (incident.proposed_body or "") + _provenance_footer(incident)

    try:
        if action == "none":
            incident.status = "dismissed"
            incident.resolution_note = "Reviewer marked as benign/no-action."
            db.session.commit()
            return incident, None
        if action == "pr" and incident.proposed_patch_json:
            url = github_service.create_pr_with_file_change(
                target_path=incident.proposed_patch_json["target_path"],
                new_content=incident.proposed_patch_json["new_content"],
                title=title, body=body,
            )
        else:
            url = github_service.create_issue(title, body, labels=["autobot", "incident"])
    except Exception as e:  # noqa: BLE001
        logger.exception("incident approve failed id=%s", incident_id)
        incident.status = "failed"
        incident.resolution_note = f"GitHub error: {str(e)[:500]}"
        db.session.commit()
        return incident, str(e)

    incident.github_url = url
    incident.status = "approved"
    incident.resolution_note = f"Opened {action} on GitHub."
    db.session.commit()
    return incident, None


def dismiss(incident_id: int, note: str | None = None) -> IncidentReport | None:
    incident = db.session.get(IncidentReport, incident_id)
    if incident is None:
        return None
    incident.status = "dismissed"
    incident.resolution_note = (note or "Dismissed by user.")[:2000]
    db.session.commit()
    return incident


def _provenance_footer(incident: IncidentReport) -> str:
    return (
        f"\n\n---\n*Auto-drafted by autobot incident autopilot "
        f"(incident #{incident.id}, signature `{incident.signature[:12]}`, "
        f"{incident.occurrences} occurrence(s)). Reviewed and approved by a human.*"
    )


# --------------------------------------------------------------------------- #
# Queries (dashboard)
# --------------------------------------------------------------------------- #

def pending() -> list[IncidentReport]:
    return (
        IncidentReport.query
        .filter(IncidentReport.status.notin_(_TERMINAL))
        .order_by(IncidentReport.created_at.desc())
        .all()
    )


def recent(limit: int = 50, status: str | None = None) -> list[IncidentReport]:
    q = IncidentReport.query
    if status:
        q = q.filter(IncidentReport.status == status)
    return q.order_by(IncidentReport.created_at.desc()).limit(limit).all()


def get(incident_id: int) -> IncidentReport | None:
    return db.session.get(IncidentReport, incident_id)
