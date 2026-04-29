from datetime import datetime, timezone

from app.extensions import db


class PatchProposal(db.Model):
    __tablename__ = "patch_proposals"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    run_id = db.Column(db.Integer, db.ForeignKey("runs.id"), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    diff_text = db.Column(db.Text, nullable=False)
    target_path = db.Column(db.String(512), nullable=False)  # relative path within workspace
    target_type = db.Column(db.String(50), nullable=False)  # tool, skill, memory, config, agents
    security_level = db.Column(db.Integer, nullable=False, default=1)  # 1=auto, 2=review, 3=prohibited
    status = db.Column(
        db.String(50), nullable=False, default="pending_review"
    )  # draft, pending_review, approved, applied, rejected, rolled_back
    snapshot_path = db.Column(db.String(512), nullable=True)  # path to pre-change snapshot
    test_result_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    applied_at = db.Column(db.DateTime, nullable=True)
    # Audit chain: SHA-256 of (agent_id, target_path, diff_text, created_at, previous_hash).
    # previous_hash links to the prior patch in the chain; "genesis" for the first.
    content_hash = db.Column(db.String(64), nullable=True, index=True)
    previous_hash = db.Column(db.String(64), nullable=True)

    # Relationships
    agent = db.relationship("Agent", backref="patch_proposals")
    run = db.relationship("Run", backref="patch_proposals")

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "title": self.title,
            "reason": self.reason,
            "diff_text": self.diff_text,
            "target_path": self.target_path,
            "target_type": self.target_type,
            "security_level": self.security_level,
            "status": self.status,
            "snapshot_path": self.snapshot_path,
            "test_result_json": self.test_result_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "content_hash": self.content_hash,
            "previous_hash": self.previous_hash,
        }
