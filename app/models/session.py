from datetime import datetime, timezone

from app.extensions import db


class Session(db.Model):
    __tablename__ = "sessions"

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    channel_type = db.Column(db.String(50), nullable=False, default="web")
    external_chat_id = db.Column(db.String(255), nullable=True)
    external_user_id = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), nullable=False, default="active")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    messages = db.relationship("Message", backref="session", lazy="dynamic", order_by="Message.created_at")
    runs = db.relationship("Run", backref="session", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "channel_type": self.channel_type,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
