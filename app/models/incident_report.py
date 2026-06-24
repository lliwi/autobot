from datetime import datetime, timezone

from app.extensions import db


class IncidentReport(db.Model):
    """An auto-detected operational incident (ERROR/CRITICAL log or failed run).

    Lifecycle:
      ``new`` → ``diagnosing`` → ``awaiting_approval`` → ``approved`` | ``dismissed`` | ``failed``

    Detection (a logging handler / run hook) creates the row deduplicated by
    ``signature``. A reviewer agent then fills in the diagnosis and a proposed
    action (open an ``issue`` or a ``pr``). The proposal stays a draft until a
    human approves it from the dashboard, at which point the Issue/PR is opened
    on GitHub and ``github_url`` is recorded.
    """

    __tablename__ = "incident_reports"

    id = db.Column(db.Integer, primary_key=True)
    # Nullable: system-wide incidents (worker/scheduler) belong to no single agent.
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=True, index=True)

    # Dedup key derived from the normalized error + source. Repeated occurrences
    # bump ``occurrences`` / ``last_seen_at`` instead of creating new rows.
    signature = db.Column(db.String(64), nullable=False, index=True)
    severity = db.Column(db.String(20), nullable=False, default="error")  # error | critical
    source = db.Column(db.String(255), nullable=True)   # logger name or "run:<id>"
    title = db.Column(db.String(300), nullable=False)
    message = db.Column(db.Text, nullable=True)
    traceback = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(30), nullable=False, default="new", index=True)
    occurrences = db.Column(db.Integer, nullable=False, default=1)

    # Filled by the reviewer during diagnosis.
    diagnosis = db.Column(db.Text, nullable=True)
    proposed_action = db.Column(db.String(10), nullable=True)   # issue | pr | none
    proposed_title = db.Column(db.Text, nullable=True)
    proposed_body = db.Column(db.Text, nullable=True)
    # For a PR proposal: {"target_path": str, "new_content": str} (single file v1).
    proposed_patch_json = db.Column(db.JSON, nullable=True)
    review_run_id = db.Column(db.Integer, db.ForeignKey("runs.id"), nullable=True)

    # Outcome.
    github_url = db.Column(db.Text, nullable=True)
    resolution_note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_seen_at = db.Column(db.DateTime, nullable=True)

    agent = db.relationship("Agent")

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "signature": self.signature,
            "severity": self.severity,
            "source": self.source,
            "title": self.title,
            "message": self.message,
            "traceback": self.traceback,
            "status": self.status,
            "occurrences": self.occurrences,
            "diagnosis": self.diagnosis,
            "proposed_action": self.proposed_action,
            "proposed_title": self.proposed_title,
            "proposed_body": self.proposed_body,
            "proposed_patch_json": self.proposed_patch_json,
            "github_url": self.github_url,
            "resolution_note": self.resolution_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }
