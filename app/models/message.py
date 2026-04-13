from datetime import datetime, timezone

from app.extensions import db


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=False)
    role = db.Column(db.String(50), nullable=False)  # user, assistant, system, tool
    content = db.Column(db.Text, nullable=False)
    metadata_json = db.Column(db.JSON, nullable=True)
    token_count = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.Index("ix_messages_session_created", "session_id", "created_at"),)

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "metadata_json": self.metadata_json,
            "token_count": self.token_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
