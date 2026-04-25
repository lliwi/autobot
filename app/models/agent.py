from datetime import datetime, timezone

from app.extensions import db


class Agent(db.Model):
    __tablename__ = "agents"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False, default="inactive")
    workspace_path = db.Column(db.String(512), nullable=False)
    model_name = db.Column(db.String(100), nullable=False, default="gpt-5.2")
    parent_agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=True)
    heartbeat_interval = db.Column(db.Integer, nullable=True, default=15)  # minutes, null = disabled
    group_response_policy = db.Column(db.String(50), nullable=False, default="mention")  # always, mention, allowlist
    # 0=off … 10=full audit. Each level ADDS an event category to the reviewer's
    # scope; see app.services.review_service.REVIEW_LEVELS for the mapping.
    review_effort = db.Column(db.SmallInteger, nullable=False, default=3)
    # Per-agent daily cap on review tokens (input+output). None = unlimited.
    # Enforced in review_service.should_review — when today's consumption
    # reaches the cap the gate closes for the rest of the UTC day.
    review_token_budget_daily = db.Column(db.Integer, nullable=True)
    # Hard cap on tool-call rounds in a single run. Prevents runaway loops.
    # None falls back to agent_runner.DEFAULT_MAX_TOOL_ROUNDS.
    max_tool_rounds = db.Column(db.Integer, nullable=True)
    # Matrix room_id to forward web chat responses to (e.g. "!abc123:matrix.org").
    # When set, every web chat assistant reply is also sent to this room.
    forward_matrix_room = db.Column(db.String(255), nullable=True)
    # When set, Matrix messages from this room are also appended to today's web
    # session so both channels share a unified conversation history.
    sync_matrix_room = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
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
            "parent_agent_id": self.parent_agent_id,
            "heartbeat_interval": self.heartbeat_interval,
            "group_response_policy": self.group_response_policy,
            "review_effort": self.review_effort,
            "review_token_budget_daily": self.review_token_budget_daily,
            "max_tool_rounds": self.max_tool_rounds,
            "forward_matrix_room": self.forward_matrix_room,
            "sync_matrix_room": self.sync_matrix_room,
            "children_count": len(self.children) if self.children else 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
