from datetime import datetime, timezone

from app.extensions import db


class Tool(db.Model):
    """A tool in the global catalog.

    Tools are global (like skills): they live in ``workspaces/_global/tools/``
    and are shared across agents through the ``agent_tools`` junction table.
    Per-agent access/enablement is expressed by an :class:`AgentTool` row.
    """

    __tablename__ = "tools"
    __table_args__ = (db.UniqueConstraint("slug", name="uq_tool_slug"),)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False)
    version = db.Column(db.String(50), nullable=False, default="0.1.0")
    description = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(50), nullable=False, default="workspace")  # workspace, generated, manual
    manifest_json = db.Column(db.JSON, nullable=True)
    path = db.Column(db.String(512), nullable=False)  # relative path within _global/
    timeout = db.Column(db.Integer, nullable=True, default=30)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    agent_tools = db.relationship("AgentTool", backref="tool", cascade="all, delete-orphan")

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
            "timeout": self.timeout,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AgentTool(db.Model):
    __tablename__ = "agent_tools"
    __table_args__ = (db.UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool"),)

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    tool_id = db.Column(db.Integer, db.ForeignKey("tools.id"), nullable=False, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    agent = db.relationship("Agent", backref="agent_tools")
