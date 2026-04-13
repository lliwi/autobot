from datetime import datetime, timezone

from app.extensions import db


class Run(db.Model):
    __tablename__ = "runs"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=True)
    trigger_type = db.Column(db.String(50), nullable=False)  # message, cron, heartbeat, internal
    status = db.Column(db.String(50), nullable=False, default="pending")
    started_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    finished_at = db.Column(db.DateTime, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    input_tokens = db.Column(db.Integer, nullable=True)
    output_tokens = db.Column(db.Integer, nullable=True)
    estimated_cost = db.Column(db.Float, nullable=True)
    error_summary = db.Column(db.Text, nullable=True)

    # Relationships
    tool_executions = db.relationship("ToolExecution", backref="run", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "trigger_type": self.trigger_type,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": self.estimated_cost,
            "error_summary": self.error_summary,
        }
