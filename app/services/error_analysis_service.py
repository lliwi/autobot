"""Error-learning loop — detect recurring failures and turn them into fix objectives.

Pipeline:
  1. Scan recent failed ToolExecutions (status=error) and Runs (status in
     error/stuck) within a window.
  2. Cluster them by a normalized *signature* (tool + message with ids/paths/
     numbers stripped) so one-off blips don't trigger work.
  3. For any signature seen >= threshold times, create a fix Objective for the
     owning agent (deduped per signature) — the autonomous heartbeat loop then
     drives it to completion, and the agent records the root cause in MEMORY.md.

Guardrails: cluster + threshold (no one-offs), per-signature dedup + cooldown,
and meta-tool exclusion (don't react to the error/objective tools themselves).
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.objective import Objective
from app.models.run import Run
from app.models.tool_execution import ToolExecution

logger = logging.getLogger(__name__)

# Defaults (tunable)
ERROR_WINDOW_HOURS = 24
ERROR_THRESHOLD = 3
FIX_OBJECTIVE_COOLDOWN_HOURS = 24

# Don't create fix-objectives about the introspection/learning tools themselves.
_EXCLUDED_TOOLS = {
    "error_digest", "create_objective", "list_objectives", "update_objective",
    "objective_progress", "complete_objective", "list_runs", "get_run",
}

# Order matters: quoted values and URLs before bare paths/numbers.
_NORM_PATTERNS = [
    (re.compile(r"'[^']*'"), "'<v>'"),
    (re.compile(r'"[^"]*"'), '"<v>"'),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b[0-9a-f]{8,}\b", re.I), "<hex>"),
    (re.compile(r"/[\w.\-/]+"), "<path>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalize_error(msg: str) -> str:
    """Collapse variable parts of an error message into a stable signature."""
    s = str(msg or "").strip()
    for pat, repl in _NORM_PATTERNS:
        s = pat.sub(repl, s)
    return s.strip()[:200]


def _naive(dt):
    return dt.replace(tzinfo=None) if (dt and dt.tzinfo) else dt


def error_digest(agent_id: int, window_hours: int = ERROR_WINDOW_HOURS,
                 min_count: int = 1) -> list[dict]:
    """Return recent failures for an agent, clustered by signature.

    Each cluster: {signature, tool, count, sample, last_run_id, last_at}.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)
    clusters: dict[str, dict] = {}

    def _add(tool, message, run_id, at):
        norm = normalize_error(message)
        if not norm:
            return
        if tool in _EXCLUDED_TOOLS:
            return
        sig = f"{tool}|{norm}"
        c = clusters.get(sig)
        at_n = _naive(at)
        if c is None:
            clusters[sig] = {
                "signature": sig, "tool": tool, "count": 1,
                "sample": str(message)[:300], "last_run_id": run_id, "last_at": at_n,
            }
        else:
            c["count"] += 1
            if at_n and (c["last_at"] is None or at_n >= c["last_at"]):
                c["last_at"] = at_n
                c["last_run_id"] = run_id
                c["sample"] = str(message)[:300]

    # Failed tool executions
    execs = (
        ToolExecution.query
        .filter(ToolExecution.agent_id == agent_id, ToolExecution.status == "error")
        .filter(ToolExecution.started_at >= cutoff)
        .all()
    )
    for e in execs:
        out = e.output_json or {}
        msg = out.get("error") if isinstance(out, dict) else None
        _add(e.tool_name, msg or "tool error (no message)", e.run_id, e.started_at)

    # Failed / stuck runs
    runs = (
        Run.query
        .filter(Run.agent_id == agent_id, Run.status.in_(("error", "stuck")))
        .filter(Run.started_at >= cutoff)
        .all()
    )
    for r in runs:
        if r.error_summary:
            _add("(run)", r.error_summary, r.id, r.started_at)

    out = [c for c in clusters.values() if c["count"] >= min_count]
    out.sort(key=lambda c: c["count"], reverse=True)
    for c in out:
        c["last_at"] = c["last_at"].isoformat() if c["last_at"] else None
    return out


def _has_open_or_recent_fix(agent_id: int, signature: str, cooldown_hours: int) -> bool:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=cooldown_hours)
    for o in Objective.query.filter_by(agent_id=agent_id).all():
        ctx = o.context_json or {}
        if ctx.get("error_signature") != signature:
            continue
        if o.status in ("active", "blocked", "waiting"):
            return True
        if o.status in ("done", "cancelled") and o.created_at and _naive(o.created_at) >= cutoff:
            return True
    return False


def scan_agent(agent_id: int, threshold: int = ERROR_THRESHOLD,
               window_hours: int = ERROR_WINDOW_HOURS,
               cooldown_hours: int = FIX_OBJECTIVE_COOLDOWN_HOURS) -> list[Objective]:
    """Create fix-objectives for recurring error clusters. Returns new objectives."""
    from app.services import objective_service

    created = []
    for cluster in error_digest(agent_id, window_hours=window_hours, min_count=threshold):
        sig = cluster["signature"]
        if _has_open_or_recent_fix(agent_id, sig, cooldown_hours):
            continue
        title = f"Arreglar error recurrente ({cluster['count']}x) en {cluster['tool']}"
        description = (
            f"Firma: {sig}\n"
            f"Muestra: {cluster['sample']}\n"
            f"Último run: {cluster['last_run_id']}\n\n"
            "Reproduce el fallo, halla la causa raíz y aplícale el fix. Si es un bug de una "
            "tool, edita su tool.py y SUBE la versión en manifest.json; si toca código L2/L3 "
            "(core/OAuth/DB/seguridad) NO lo apliques: usa update_objective status='waiting' "
            "para pedir aprobación. Verifica que el error ya no ocurre y documenta causa+fix "
            "en MEMORY.md."
        )
        obj = objective_service.create_objective(
            agent_id, title, description,
            context={
                "error_signature": sig,
                "error_count": cluster["count"],
                "sample_error": cluster["sample"],
                "last_run_id": cluster["last_run_id"],
                "kind": "error_fix",
            },
        )
        objective_service.set_plan(obj, [
            "Reproducir el error",
            "Identificar la causa raíz",
            "Aplicar el fix (subir versión si es una tool)",
            "Verificar que el error ya no ocurre",
            "Documentar causa y fix en MEMORY.md",
        ])
        created.append(obj)
        logger.info("error-loop: created fix objective %s for agent %s (sig=%s count=%s)",
                    obj.id, agent_id, sig, cluster["count"])
    return created


def scan_all_active_agents(threshold: int = ERROR_THRESHOLD,
                           window_hours: int = ERROR_WINDOW_HOURS) -> int:
    """Scan every active agent that has the heartbeat loop enabled. Returns total created."""
    from app.models.agent import Agent

    total = 0
    agents = Agent.query.filter_by(status="active").all()
    for ag in agents:
        if not (ag.heartbeat_interval and ag.heartbeat_interval > 0):
            continue
        try:
            total += len(scan_agent(ag.id, threshold=threshold, window_hours=window_hours))
        except Exception:
            logger.exception("error-loop scan failed for agent %s", ag.id)
    return total
