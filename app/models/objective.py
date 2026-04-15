from datetime import datetime, timezone

from app.extensions import db


class Objective(db.Model):
    """A goal-oriented, long-lived piece of work the agent is tracking.

    Distinct from a ScheduledTask (time-triggered) and from a Run (single
    execution): an Objective represents an intent that may span many runs and
    remain "active" between user interactions. The heartbeat supervisor loop
    reads active Objectives each tick and decides whether to act on them.
    """

    __tablename__ = "objectives"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    # active: keep checking; blocked: waiting on external dependency;
    # waiting: waiting on user input; done: resolved; cancelled: dropped.
    status = db.Column(db.String(50), nullable=False, default="active", index=True)
    # Earliest wall-clock time at which this objective should be re-evaluated.
    # Heartbeat ticks that fire before this time will skip the objective.
    next_check_at = db.Column(db.DateTime, nullable=True, index=True)
    last_progress_at = db.Column(db.DateTime, nullable=True)
    # Free-form JSON the agent can use to store progress, sub-state, etc.
    context_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    agent = db.relationship("Agent", backref="objectives")

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "next_check_at": self.next_check_at.isoformat() if self.next_check_at else None,
            "last_progress_at": self.last_progress_at.isoformat() if self.last_progress_at else None,
            "context_json": self.context_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
