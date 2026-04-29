from datetime import datetime, timezone

from app.extensions import db


class WorkspaceFileVersion(db.Model):
    __tablename__ = "workspace_file_versions"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    filename = db.Column(db.String(64), nullable=False)
    content = db.Column(db.Text, nullable=False)
    saved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    saved_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )

    agent = db.relationship("Agent", backref="workspace_file_versions")
    saved_by = db.relationship("User", backref="workspace_file_versions")

    __table_args__ = (
        db.Index("ix_wfv_agent_file_time", "agent_id", "filename", "saved_at"),
    )
