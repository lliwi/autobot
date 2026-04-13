from datetime import datetime, timezone

from app.extensions import db


class Agent(db.Model):
    __tablename__ = "agents"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False, default="inactive")
    workspace_path = db.Column(db.String(512), nullable=False)
    model_name = db.Column(db.String(100), nullable=False, default="o4-mini")
    oauth_profile_id = db.Column(db.Integer, db.ForeignKey("oauth_profiles.id"), nullable=True)
    parent_agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    oauth_profile = db.relationship("OAuthProfile", backref="agents")
    parent_agent = db.relationship("Agent", remote_side=[id], backref="children")
    sessions = db.relationship("Session", backref="agent", lazy="dynamic")
    runs = db.relationship("Run", backref="agent", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "status": self.status,
            "workspace_path": self.workspace_path,
            "model_name": self.model_name,
            "oauth_profile_id": self.oauth_profile_id,
            "parent_agent_id": self.parent_agent_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
