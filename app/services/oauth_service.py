import json
import secrets
from datetime import datetime, timezone

import httpx
from cryptography.fernet import Fernet
from flask import current_app

from app.extensions import db
from app.models.oauth_profile import OAuthProfile

OPENAI_AUTHORIZE_URL = "https://platform.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://platform.openai.com/oauth/token"


def _fernet():
    key = current_app.config["TOKEN_ENCRYPTION_KEY"]
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_tokens(data):
    return _fernet().encrypt(json.dumps(data).encode())


def decrypt_tokens(encrypted):
    return json.loads(_fernet().decrypt(encrypted).decode())


def get_authorize_url():
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": current_app.config["OPENAI_CLIENT_ID"],
        "redirect_uri": current_app.config["OPENAI_REDIRECT_URI"],
        "response_type": "code",
        "state": state,
        "scope": "openai.public",
    }
    url = OPENAI_AUTHORIZE_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return url, state


def handle_callback(code):
    response = httpx.post(
        OPENAI_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": current_app.config["OPENAI_CLIENT_ID"],
            "client_secret": current_app.config["OPENAI_CLIENT_SECRET"],
            "redirect_uri": current_app.config["OPENAI_REDIRECT_URI"],
        },
    )
    response.raise_for_status()
    token_data = response.json()

    expires_at = None
    if "expires_in" in token_data:
        from datetime import timedelta

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

    profile = OAuthProfile(
        provider="openai_codex",
        account_label=token_data.get("account", "default"),
        encrypted_tokens=encrypt_tokens(token_data),
        expires_at=expires_at,
        refresh_status="active",
    )
    db.session.add(profile)
    db.session.commit()
    return profile


def refresh_tokens(profile_id):
    profile = db.session.get(OAuthProfile, profile_id)
    if profile is None:
        raise ValueError("OAuth profile not found")

    tokens = decrypt_tokens(profile.encrypted_tokens)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        profile.refresh_status = "no_refresh_token"
        db.session.commit()
        return profile

    response = httpx.post(
        OPENAI_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": current_app.config["OPENAI_CLIENT_ID"],
            "client_secret": current_app.config["OPENAI_CLIENT_SECRET"],
        },
    )
    response.raise_for_status()
    new_tokens = response.json()

    if "expires_in" in new_tokens:
        from datetime import timedelta

        profile.expires_at = datetime.now(timezone.utc) + timedelta(seconds=new_tokens["expires_in"])

    profile.encrypted_tokens = encrypt_tokens(new_tokens)
    profile.refresh_status = "active"
    db.session.commit()
    return profile


def get_access_token(profile_id):
    profile = db.session.get(OAuthProfile, profile_id)
    if profile is None:
        raise ValueError("OAuth profile not found")

    # Auto-refresh if expired
    if profile.expires_at and profile.expires_at < datetime.now(timezone.utc):
        profile = refresh_tokens(profile_id)

    tokens = decrypt_tokens(profile.encrypted_tokens)
    return tokens["access_token"]
