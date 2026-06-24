"""Format stored (naive-UTC) datetimes unambiguously for agents and APIs.

DB datetime columns are stored as naive UTC. Calling ``.isoformat()`` on them
yields e.g. ``"2026-05-30T07:20:00"`` with no offset, which an agent can't tell
apart from local time — the root of "the scheduler is in a different timezone"
confusion. These helpers always mark the zone explicitly.
"""

from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def as_utc(dt):
    """Return ``dt`` as a tz-aware UTC datetime (treating naive as UTC), or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_iso(dt):
    """Explicit-UTC ISO string ending in ``Z`` (e.g. ``2026-05-30T07:20:00Z``)."""
    aware = as_utc(dt)
    if aware is None:
        return None
    return aware.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def local_str(dt, tz_name, fmt="%Y-%m-%d %H:%M %Z"):
    """Render a stored UTC datetime in ``tz_name`` (falls back to UTC)."""
    aware = as_utc(dt)
    if aware is None:
        return None
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    return aware.astimezone(tz).strftime(fmt)
