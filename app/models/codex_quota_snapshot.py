from datetime import datetime, timezone

from app.extensions import db


class CodexQuotaSnapshot(db.Model):
    """Latest rate-limit data reported by the Codex backend.

    The ``chatgpt.com/backend-api/codex/responses`` endpoint returns per-response
    headers describing quota usage for the ChatGPT subscription:

      * ``x-codex-plan-type``                       — e.g. "plus", "pro"
      * ``x-codex-primary-used-percent``            — usage of the short window (0-100)
      * ``x-codex-primary-window-minutes``          — short window size (e.g. 300 = 5h)
      * ``x-codex-primary-reset-at``                — unix ts when the short window resets
      * ``x-codex-secondary-used-percent``          — usage of the weekly window (0-100)
      * ``x-codex-secondary-window-minutes``        — weekly window size (e.g. 10080)
      * ``x-codex-secondary-reset-at``              — unix ts when the weekly window resets
      * ``x-codex-credits-unlimited``               — bool-ish
      * ``x-codex-credits-balance``                 — string balance (may be empty)

    This table stores a single upserted row (``id=1``) with the most recent
    snapshot, plus ``raw_headers`` for debugging shape changes.
    """

    __tablename__ = "codex_quota_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    plan_type = db.Column(db.String(64), nullable=True)

    primary_used_percent = db.Column(db.Float, nullable=True)
    primary_window_minutes = db.Column(db.Integer, nullable=True)
    primary_reset_at = db.Column(db.DateTime, nullable=True)

    secondary_used_percent = db.Column(db.Float, nullable=True)
    secondary_window_minutes = db.Column(db.Integer, nullable=True)
    secondary_reset_at = db.Column(db.DateTime, nullable=True)

    credits_unlimited = db.Column(db.Boolean, nullable=True)
    credits_balance = db.Column(db.String(64), nullable=True)

    raw_headers = db.Column(db.JSON, nullable=True)
    fetched_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
