from datetime import datetime, timezone

from app.extensions import db


class ReviewEvent(db.Model):
    """Async queue row for reviewer sub-agent work.

    Produced by hooks throughout the runtime whenever an agent does something
    the reviewer should audit (failed runs, tool errors, chat samples…).
    Consumed by a worker job that invokes the reviewer agent and records the
    outcome back on the row.
    """
    __tablename__ = "review_events"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    payload_json = db.Column(db.JSON, nullable=True)

    # pending, processing, done, error, skipped
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    processed_at = db.Column(db.DateTime, nullable=True)

    review_run_id = db.Column(db.Integer, db.ForeignKey("runs.id"), nullable=True)
    summary = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)
    # Parsed structured output from the reviewer plus any follow-up actions we
    # took (patches proposed, their IDs, their status). Schema documented in
    # ``review_queue_service._parse_findings``.
    findings_json = db.Column(db.JSON, nullable=True)

    agent = db.relationship("Agent", backref="review_events")
    review_run = db.relationship("Run", foreign_keys=[review_run_id])

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "payload_json": self.payload_json,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "review_run_id": self.review_run_id,
            "summary": self.summary,
            "error": self.error,
            "findings_json": self.findings_json,
        }
