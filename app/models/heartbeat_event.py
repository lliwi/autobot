from datetime import datetime, timezone

from app.extensions import db


class HeartbeatEvent(db.Model):
    """One tick of the heartbeat supervisor loop.

    Records *what the supervisor saw* and *what it decided to do* — separate
    from Run so that ticks that decided to skip (no LLM call) are still
    captured for observability.
    """

    __tablename__ = "heartbeat_events"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    tick_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    # skip: nothing actionable this tick.
    # act:  triggered a Run (see run_id).
    # defer: actionable but a prior run is still in flight / cooldown.
    decision = db.Column(db.String(20), nullable=False, default="skip")
    reason = db.Column(db.String(500), nullable=True)
    # Snapshot of the world state the supervisor saw: objective ids, stuck
    # runs, HEARTBEAT.md entries, last active channel. Stored verbatim for
    # post-hoc debugging of supervisor decisions.
    snapshot_json = db.Column(db.JSON, nullable=True)
    run_id = db.Column(db.Integer, db.ForeignKey("runs.id"), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "tick_at": self.tick_at.isoformat() if self.tick_at else None,
            "decision": self.decision,
            "reason": self.reason,
            "snapshot_json": self.snapshot_json,
            "run_id": self.run_id,
        }
