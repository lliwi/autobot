from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models.run import Run


def estimate_cost(model_name, input_tokens, output_tokens):
    """Estimate run cost in USD from token counts and the model's price table.

    Prices live in ``Config.MODEL_PRICING`` (USD per 1,000 tokens). The model is
    matched by the longest key that is a substring of ``model_name``; otherwise
    the ``"default"`` entry is used.
    """
    if not input_tokens and not output_tokens:
        return None
    pricing = current_app.config.get("MODEL_PRICING", {})
    rate = pricing.get("default", (0.0, 0.0))
    name = (model_name or "").lower()
    for key in sorted((k for k in pricing if k != "default"), key=len, reverse=True):
        if key.lower() in name:
            rate = pricing[key]
            break
    in_rate, out_rate = rate
    return ((input_tokens or 0) * in_rate + (output_tokens or 0) * out_rate) / 1000


def create_run(agent_id, session_id, trigger_type="message"):
    run = Run(
        agent_id=agent_id,
        session_id=session_id,
        trigger_type=trigger_type,
        status="running",
    )
    db.session.add(run)
    db.session.commit()
    return run


def save_round_trace(run_id, rounds_trace):
    """Persist the per-round timeline emitted by ``agent_runner``.

    Called from the runner's ``finally`` block so every termination path (normal
    finish, early abort, client disconnect) records what rounds ran. Best-effort
    and isolated from ``finish_run`` so a trace write never blocks finalization.
    """
    if not rounds_trace:
        return
    run = db.session.get(Run, run_id)
    if run is None:
        return
    run.rounds_trace = rounds_trace
    db.session.commit()


def finish_run(run_id, status="completed", input_tokens=None, output_tokens=None, error_summary=None):
    run = db.session.get(Run, run_id)
    if run is None:
        return

    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    run.input_tokens = input_tokens
    run.output_tokens = output_tokens
    run.error_summary = error_summary

    if run.started_at and run.finished_at:
        started = run.started_at.replace(tzinfo=None) if run.started_at.tzinfo else run.started_at
        finished = run.finished_at.replace(tzinfo=None) if run.finished_at.tzinfo else run.finished_at
        run.duration_ms = int((finished - started).total_seconds() * 1000)

    # Estimate cost from the agent's model price table (see estimate_cost).
    model_name = run.agent.model_name if run.agent is not None else None
    run.estimated_cost = estimate_cost(model_name, input_tokens, output_tokens)

    db.session.commit()

    if status == "error":
        try:
            from app.services import review_queue_service

            review_queue_service.enqueue(
                agent_id=run.agent_id,
                event_type="run_failed",
                payload={"run_id": run.id},
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to enqueue run_failed review event for run %s", run_id
            )

    return run
