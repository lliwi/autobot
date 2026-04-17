"""Encrypted storage for API keys and other secrets used by agents.

Values are encrypted at rest with Fernet using ``TOKEN_ENCRYPTION_KEY``. The
clear value only appears when an admin explicitly reveals it in the UI, or when
an agent calls the ``get_credential`` tool. List/preview views always show a
redacted value so secrets never leak into templates, logs, or context windows.
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models.agent import Agent
from app.models.credential import Credential


class CredentialError(Exception):
    """Raised when the credential store is misconfigured or a lookup fails."""


def _fernet():
    from cryptography.fernet import Fernet

    key = current_app.config.get("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise CredentialError(
            "TOKEN_ENCRYPTION_KEY is not set. Run `flask onboard` or generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`"
            " and add it to .env."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        raise CredentialError(f"Invalid TOKEN_ENCRYPTION_KEY: {e}") from e


def _encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode("utf-8"))


def _decrypt(token: bytes) -> str:
    return _fernet().decrypt(token).decode("utf-8")


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def list_credentials(agent_id: int | None = None) -> list[Credential]:
    """Return credentials, optionally scoped to a specific agent.

    When ``agent_id`` is ``None`` returns every credential (global + agent-
    scoped), which is what the admin dashboard wants. Pass an id for the
    per-agent inventory that ``get_credential`` uses at runtime.
    """
    query = Credential.query
    if agent_id is not None:
        query = query.filter(
            db.or_(Credential.agent_id == agent_id, Credential.agent_id.is_(None))
        )
    return query.order_by(Credential.name).all()


def get_credential(credential_id: int) -> Credential | None:
    return db.session.get(Credential, credential_id)


CREDENTIAL_TYPES = ("token", "user_password")


def _resolve_row(name: str, agent_id: int | None) -> Credential | None:
    if agent_id is not None:
        row = Credential.query.filter_by(agent_id=agent_id, name=name).first()
        if row is not None:
            return row
    return Credential.query.filter_by(agent_id=None, name=name).first()


def get_credential_value(name: str, agent_id: int | None = None) -> str | None:
    """Resolve a token credential by ``name``. Agent-scoped wins over global.

    Returns ``None`` if not found. For ``user_password`` credentials use
    ``get_credential_pair`` instead — this function returns just the decrypted
    value string.
    """
    row = _resolve_row(name, agent_id)
    if row is None:
        return None
    return _decrypt(row.encrypted_value)


def get_credential_pair(name: str, agent_id: int | None = None) -> dict | None:
    """Resolve a credential and return its full shape.

    Works for both types. Shape:
      * token          → {"type": "token", "value": "..."}
      * user_password  → {"type": "user_password", "username": "...", "password": "..."}
    """
    row = _resolve_row(name, agent_id)
    if row is None:
        return None
    if row.credential_type == "user_password":
        return {
            "type": "user_password",
            "username": row.username or "",
            "password": _decrypt(row.encrypted_value),
        }
    return {"type": "token", "value": _decrypt(row.encrypted_value)}


def set_credential(name: str, value: str, description: str | None = None,
                   agent_id: int | None = None, created_by_user_id: int | None = None,
                   credential_type: str = "token", username: str | None = None) -> Credential:
    """Create or replace the credential identified by ``(agent_id, name)``.

    Args:
        name: unique within the scope.
        value: the secret. For ``user_password`` this is the password.
        credential_type: ``token`` (single value) or ``user_password``.
        username: required when ``credential_type`` is ``user_password``.

    Raises ``CredentialError`` on bad input. Agent-scoped credentials shadow
    global ones of the same name for that agent.
    """
    name = (name or "").strip()
    if not name:
        raise CredentialError("name is required")
    if not value:
        raise CredentialError("value is required")
    if credential_type not in CREDENTIAL_TYPES:
        raise CredentialError(f"credential_type must be one of {CREDENTIAL_TYPES}")
    if credential_type == "user_password" and not (username or "").strip():
        raise CredentialError("username is required for user_password credentials")
    if agent_id is not None and db.session.get(Agent, agent_id) is None:
        raise CredentialError(f"agent {agent_id} not found")

    username_clean = (username or "").strip() or None

    existing = Credential.query.filter_by(agent_id=agent_id, name=name).first()
    if existing is not None:
        existing.encrypted_value = _encrypt(value)
        existing.credential_type = credential_type
        existing.username = username_clean if credential_type == "user_password" else None
        if description is not None:
            existing.description = description
        db.session.commit()
        return existing

    row = Credential(
        name=name,
        description=description,
        agent_id=agent_id,
        credential_type=credential_type,
        username=username_clean if credential_type == "user_password" else None,
        encrypted_value=_encrypt(value),
        created_by_user_id=created_by_user_id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def delete_credential(credential_id: int) -> bool:
    row = db.session.get(Credential, credential_id)
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def reveal_credential(credential_id: int) -> str | None:
    """Return the decrypted value. Use only when the admin explicitly asks."""
    row = db.session.get(Credential, credential_id)
    if row is None:
        return None
    return _decrypt(row.encrypted_value)


def to_dict(row: Credential, include_value: bool = False) -> dict:
    """Shape a credential for templates/JSON responses.

    Defaults to a redacted preview so accidental serialization never leaks the
    secret. The dashboard's "reveal" action is the only place that should set
    ``include_value=True``.
    """
    data = {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "agent_id": row.agent_id,
        "agent_name": row.agent.name if row.agent else None,
        "credential_type": row.credential_type,
        "username": row.username,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_value:
        data["value"] = _decrypt(row.encrypted_value)
    else:
        try:
            data["preview"] = _mask(_decrypt(row.encrypted_value))
        except Exception:
            data["preview"] = "(cannot decrypt — check TOKEN_ENCRYPTION_KEY)"
    return data
