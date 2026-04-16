from datetime import datetime, timezone

from app.extensions import db


class ApprovalRule(db.Model):
    """Standing user-approval for level-2 patches.

    When the user approves a patch and ticks "always allow edits like this",
    we store the (agent, target pattern) pair here. Subsequent level-2 patches
    whose target matches will be auto-applied instead of sitting in
    pending_review. This is how the user grants the agent a durable ability to
    self-improve a specific file or directory.

    ``pattern`` matches exactly or as a prefix when it ends with ``*``. A
    ``NULL`` ``agent_id`` means the rule applies to every agent — use
    sparingly.
    """

    __tablename__ = "approval_rules"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=True, index=True)
    pattern = db.Column(db.String(512), nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    agent = db.relationship("Agent", backref="approval_rules")

    def matches(self, target_path: str) -> bool:
        pat = self.pattern.strip()
        if pat.endswith("*"):
            return target_path.startswith(pat[:-1])
        return target_path == pat

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "pattern": self.pattern,
            "note": self.note,
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
