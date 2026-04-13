from datetime import datetime, timezone

from app.extensions import db
from app.models.run import Run


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
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)

    # Estimate cost (rough, for o4-mini)
    if input_tokens and output_tokens:
        run.estimated_cost = (input_tokens * 0.00015 + output_tokens * 0.0006) / 1000

    db.session.commit()
    return run
