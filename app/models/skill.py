from datetime import datetime, timezone

from app.extensions import db


class Skill(db.Model):
    __tablename__ = "skills"
    __table_args__ = (db.UniqueConstraint("slug", name="uq_skill_slug"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False)
    version = db.Column(db.String(50), nullable=False, default="0.1.0")
    description = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(50), nullable=False, default="manual")  # manual, generated, builtin
    manifest_json = db.Column(db.JSON, nullable=True)
    path = db.Column(db.String(512), nullable=False)  # relative path within _global/
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    agent_skills = db.relationship("AgentSkill", backref="skill", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "version": self.version,
            "description": self.description,
            "source": self.source,
            "manifest_json": self.manifest_json,
            "path": self.path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AgentSkill(db.Model):
    __tablename__ = "agent_skills"
    __table_args__ = (db.UniqueConstraint("agent_id", "skill_id", name="uq_agent_skill"),)

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    skill_id = db.Column(db.Integer, db.ForeignKey("skills.id"), nullable=False, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    agent = db.relationship("Agent", backref="agent_skills")
