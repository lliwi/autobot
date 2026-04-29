"""Parse and persist Codex subscription rate-limit snapshots.

The Codex backend attaches quota headers to every /responses call. The agent
runtime forwards them here at the end of each successful round so the dashboard
can display how much of the 5-hour and weekly windows have been consumed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.extensions import db
from app.models.codex_quota_snapshot import CodexQuotaSnapshot

logger = logging.getLogger(__name__)

# Header names are case-insensitive in HTTP but httpx normalizes to lowercase.
_H_PLAN = "x-codex-plan-type"
_H_PRIMARY_PCT = "x-codex-primary-used-percent"
_H_PRIMARY_WIN = "x-codex-primary-window-minutes"
_H_PRIMARY_RESET_AT = "x-codex-primary-reset-at"
_H_SECONDARY_PCT = "x-codex-secondary-used-percent"
_H_SECONDARY_WIN = "x-codex-secondary-window-minutes"
_H_SECONDARY_RESET_AT = "x-codex-secondary-reset-at"
_H_CREDITS_UNLIMITED = "x-codex-credits-unlimited"
_H_CREDITS_BALANCE = "x-codex-credits-balance"


def _f(headers: dict, key: str) -> float | None:
    v = headers.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(headers: dict, key: str) -> int | None:
    v = _f(headers, key)
    return int(v) if v is not None else None


def _dt_from_unix(headers: dict, key: str) -> datetime | None:
    ts = _i(headers, key)
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _bool(headers: dict, key: str) -> bool | None:
    v = headers.get(key)
    if v is None or v == "":
        return None
    return str(v).strip().lower() in ("1", "true", "yes")


def save_snapshot(headers: dict) -> None:
    """Upsert the latest snapshot (id=1) from the given response headers.

    Silently ignores missing/malformed values — we never want a rate-limit
    accounting glitch to break a run.
    """
    # Snapshot is only meaningful if at least one usage percent is present.
    if _f(headers, _H_PRIMARY_PCT) is None and _f(headers, _H_SECONDARY_PCT) is None:
        return

    try:
        row = db.session.get(CodexQuotaSnapshot, 1)
        if row is None:
            row = CodexQuotaSnapshot(id=1)
            db.session.add(row)

        row.plan_type = headers.get(_H_PLAN) or row.plan_type
        row.primary_used_percent = _f(headers, _H_PRIMARY_PCT)
        row.primary_window_minutes = _i(headers, _H_PRIMARY_WIN)
        row.primary_reset_at = _dt_from_unix(headers, _H_PRIMARY_RESET_AT)
        row.secondary_used_percent = _f(headers, _H_SECONDARY_PCT)
        row.secondary_window_minutes = _i(headers, _H_SECONDARY_WIN)
        row.secondary_reset_at = _dt_from_unix(headers, _H_SECONDARY_RESET_AT)
        row.credits_unlimited = _bool(headers, _H_CREDITS_UNLIMITED)
        row.credits_balance = headers.get(_H_CREDITS_BALANCE) or None

        # Only keep the codex-specific headers in raw_headers for debugging.
        row.raw_headers = {
            k: v for k, v in headers.items()
            if isinstance(k, str) and k.lower().startswith("x-codex-")
        }
        row.fetched_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception as e:
        # Never block a run because of metric accounting.
        logger.warning("Failed to persist Codex quota snapshot: %s", e)
        db.session.rollback()


def _pct(used: float | None) -> dict | None:
    """Return {used, remaining} rounded to 2 decimals or None."""
    if used is None:
        return None
    used = max(0.0, min(100.0, float(used)))
    return {"used": round(used, 2), "remaining": round(100.0 - used, 2)}


def _iso(dt: datetime | None) -> str | None:
    """Serialize a datetime as an ISO-8601 string that carries an explicit UTC
    offset. The DB column is ``DateTime`` (not ``DateTime(timezone=True)``),
    so SQLAlchemy strips tzinfo on write and reads back naive values — feeding
    those directly into ``new Date(...)`` on the browser makes it guess local
    time and misrender by the offset. Everything we store here is already UTC,
    so we just re-apply ``timezone.utc`` before serializing.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def refresh_quota() -> bool:
    """Make a minimal non-streaming call to the Codex responses endpoint to
    capture fresh quota headers and persist them. Returns True if updated.

    Uses the shortest possible prompt (a single space) with max_output_tokens=1
    so the inference cost is negligible — just enough for the backend to return
    the x-codex-* rate-limit headers.
    """
    from app.runtime.model_client import CODEX_RESPONSES_URL, DEFAULT_MODEL
    from app.services import codex_auth as _auth

    if not _auth.is_logged_in():
        return False
    try:
        token = _auth.get_access_token()
        account_id = _auth.get_account_id() or ""
        body = {
            "model": DEFAULT_MODEL,
            "store": False,
            "stream": True,
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "."}]}],
        }
        req_headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": _auth.ORIGINATOR,
            "content-type": "application/json",
            "accept": "text/event-stream",
        }
        # The quota headers arrive with the HTTP response before any body bytes,
        # so we can close the stream immediately after reading them.
        with httpx.Client(timeout=15.0) as client:
            with client.stream("POST", CODEX_RESPONSES_URL, json=body, headers=req_headers) as resp:
                if resp.status_code != 200:
                    return False
                quota_headers = {
                    k: v for k, v in resp.headers.items()
                    if isinstance(k, str) and k.lower().startswith("x-codex-")
                }
        if quota_headers:
            save_snapshot(quota_headers)
            return True
    except Exception as e:
        logger.warning("Quota refresh failed: %s", e)
    return False


def get_latest_snapshot() -> dict | None:
    """Return a dashboard-friendly dict or None if no snapshot has been captured yet."""
    row = db.session.get(CodexQuotaSnapshot, 1)
    if row is None:
        return None

    return {
        "plan_type": row.plan_type,
        "primary": {
            **(_pct(row.primary_used_percent) or {"used": None, "remaining": None}),
            "window_minutes": row.primary_window_minutes,
            "resets_at": _iso(row.primary_reset_at),
        },
        "secondary": {
            **(_pct(row.secondary_used_percent) or {"used": None, "remaining": None}),
            "window_minutes": row.secondary_window_minutes,
            "resets_at": _iso(row.secondary_reset_at),
        },
        "credits": {
            "unlimited": row.credits_unlimited,
            "balance": row.credits_balance,
        },
        "fetched_at": _iso(row.fetched_at),
    }
