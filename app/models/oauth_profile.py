from datetime import datetime, timezone

from app.extensions import db


class OAuthProfile(db.Model):
    __tablename__ = "oauth_profiles"

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(100), nullable=False, default="openai_codex")
    account_label = db.Column(db.String(255), nullable=True)
    encrypted_tokens = db.Column(db.LargeBinary, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    refresh_status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "provider": self.provider,
            "account_label": self.account_label,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "refresh_status": self.refresh_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
