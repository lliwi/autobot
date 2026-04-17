from datetime import datetime, timezone

from app.extensions import db


class Credential(db.Model):
    """Encrypted secret (API key, token, password…) available to agents.

    Values are encrypted at rest with Fernet using ``TOKEN_ENCRYPTION_KEY``.
    Never read ``encrypted_value`` directly — go through
    ``app.services.credential_service``.

    Scope:
      * ``agent_id`` NULL  → global, any agent may fetch by ``name``.
      * ``agent_id`` set   → only that agent may fetch; shadows a global
        credential of the same name for that agent.
    """

    __tablename__ = "credentials"
    __table_args__ = (db.UniqueConstraint("agent_id", "name", name="uq_credentials_agent_name"),)

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=True, index=True)
    name = db.Column(db.String(128), nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    # 'token' (just value) | 'user_password' (username + value holds password)
    credential_type = db.Column(db.String(32), nullable=False, default="token")
    username = db.Column(db.String(256), nullable=True)
    encrypted_value = db.Column(db.LargeBinary, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    agent = db.relationship("Agent", backref="credentials")
