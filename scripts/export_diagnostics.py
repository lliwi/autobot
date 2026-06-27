#!/usr/bin/env python3
"""Export operational diagnostics (Incidents, Reviewer, Logs) to JSON files.

Runs INSIDE a container that can build the Flask app context (web or worker),
so it reads the real database and the Redis log ring exactly as the dashboard
does. The companion wrapper ``scripts/export-diagnostics.sh`` invokes this,
copies the output out of the container, adds the raw container logs and bundles
everything into a single tarball you can hand over for debugging.

Outputs (one JSON file each) into ``--out``:
  incidents.json   IncidentReport rows  (autopilot detect/diagnose/approve)
  reviewer.json    ReviewEvent rows     (reviewer sub-agent queue + findings)
  runs.json        Run rows referenced by the incidents/reviews above (the
                   reviewer's actual execution trace: rounds_trace, errors)
  logs_ring.json   Most recent entries from the Redis log ring (web + worker)
  manifest.json    Counts, filters and generated_at

Usage (inside the container):
  python export_diagnostics.py --out /tmp/autobot-diag --days 7
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone


def _write(out_dir, name, payload):
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    return path


def _cutoff(days):
    if not days or days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def export_incidents(db, out_dir, cutoff, limit):
    from app.models.incident_report import IncidentReport

    q = IncidentReport.query
    if cutoff is not None:
        q = q.filter(IncidentReport.created_at >= cutoff)
    q = q.order_by(IncidentReport.created_at.desc())
    if limit:
        q = q.limit(limit)
    rows = q.all()
    _write(out_dir, "incidents.json", [r.to_dict() for r in rows])
    review_run_ids = {r.review_run_id for r in rows if r.review_run_id}
    return len(rows), review_run_ids


def export_reviewer(db, out_dir, cutoff, limit):
    from app.models.review_event import ReviewEvent

    q = ReviewEvent.query
    if cutoff is not None:
        q = q.filter(ReviewEvent.created_at >= cutoff)
    q = q.order_by(ReviewEvent.created_at.desc())
    if limit:
        q = q.limit(limit)
    rows = q.all()
    _write(out_dir, "reviewer.json", [r.to_dict() for r in rows])
    review_run_ids = {r.review_run_id for r in rows if r.review_run_id}
    return len(rows), review_run_ids


def export_runs(db, out_dir, run_ids):
    """The Run rows the incidents/reviews point at — full execution context."""
    from app.models.run import Run

    ids = sorted(i for i in run_ids if i)
    rows = []
    if ids:
        rows = Run.query.filter(Run.id.in_(ids)).order_by(Run.id.desc()).all()
    _write(out_dir, "runs.json", [r.to_dict() for r in rows])
    return len(rows)


def export_logs(out_dir, limit, min_level):
    """Dump the Redis log ring (newest first). Best-effort: never fatal."""
    from flask import current_app

    from app.logging_config import REDIS_LOG_KEY

    redis_url = current_app.config.get("REDIS_URL") or os.environ.get("REDIS_URL", "")
    entries = []
    error = None
    if redis_url:
        try:
            import redis

            r = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=2.0)
            raw = r.lrange(REDIS_LOG_KEY, 0, (limit - 1) if limit else -1)
            order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
            floor = order.get((min_level or "").upper(), 0)
            for item in raw:
                try:
                    entry = json.loads(item)
                except Exception:
                    entry = {"raw": item}
                if floor and order.get(str(entry.get("level", "")).upper(), 0) < floor:
                    continue
                entries.append(entry)
        except Exception as e:  # redis down / unreachable
            error = f"{type(e).__name__}: {e}"
    else:
        error = "REDIS_URL not configured"
    _write(out_dir, "logs_ring.json", {"error": error, "count": len(entries), "entries": entries})
    return len(entries), error


def main():
    parser = argparse.ArgumentParser(description="Export Incidents, Reviewer and Logs to JSON.")
    parser.add_argument("--out", required=True, help="Output directory (created if missing).")
    parser.add_argument("--days", type=int, default=7,
                        help="Only include incidents/reviews newer than N days (0 = all). Default 7.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max rows per table (0 = unlimited). Default 0.")
    parser.add_argument("--log-limit", type=int, default=2000,
                        help="Max log-ring entries to export (0 = all up to the ring cap). Default 2000.")
    parser.add_argument("--log-min-level", default="",
                        help="Drop log entries below this level (DEBUG/INFO/WARNING/ERROR/CRITICAL).")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        cutoff = _cutoff(args.days)

        n_inc, inc_runs = export_incidents(db, args.out, cutoff, args.limit)
        n_rev, rev_runs = export_reviewer(db, args.out, cutoff, args.limit)
        n_runs = export_runs(db, args.out, inc_runs | rev_runs)
        n_logs, log_err = export_logs(args.out, args.log_limit, args.log_min_level)

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filters": {
                "days": args.days,
                "row_limit": args.limit or None,
                "log_limit": args.log_limit or None,
                "log_min_level": args.log_min_level or None,
            },
            "counts": {
                "incidents": n_inc,
                "reviewer_events": n_rev,
                "runs": n_runs,
                "log_entries": n_logs,
            },
            "log_ring_error": log_err,
            "note": "May contain sensitive data (log messages, tracebacks, agent output). Handle accordingly.",
        }
        _write(args.out, "manifest.json", manifest)

    print(json.dumps(manifest["counts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
