from datetime import datetime, timezone

from app.extensions import db


class ToolExecution(db.Model):
    __tablename__ = "tool_executions"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("runs.id"), nullable=False, index=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False)
    tool_name = db.Column(db.String(255), nullable=False)
    input_json = db.Column(db.JSON, nullable=True)
    output_json = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(50), nullable=False, default="pending")
    started_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    finished_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "input_json": self.input_json,
            "output_json": self.output_json,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
