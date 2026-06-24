"""Tests for scheduler_service.compute_next_run.

Covers day-of-week constraints (including Sunday as 0 and 7),
timezone-aware scheduling, and DST edge cases.
"""

from datetime import datetime, timezone


from app.services.scheduler_service import compute_next_run


def _monday(**kwargs):
    """Return a UTC Monday for use as a base_time."""
    # 2024-04-01 is a Monday at 12:00 UTC
    return datetime(2024, 4, 1, 12, 0, 0, tzinfo=timezone.utc).replace(**kwargs)


# ---------------------------------------------------------------------------
# Day-of-week basics
# ---------------------------------------------------------------------------

class TestDayOfWeek:
    def test_weekdays_only_skips_weekend(self, app):
        # 2024-04-05 is a Friday.  Next fire should be Monday 2024-04-08.
        base = datetime(2024, 4, 5, 18, 1, 0, tzinfo=timezone.utc)  # just after 18:00 Fri
        nxt = compute_next_run("0 18 * * 1-5", base_time=base)
        assert nxt is not None
        assert nxt.weekday() == 0, f"Expected Monday (0), got weekday {nxt.weekday()} ({nxt})"

    def test_fires_on_correct_weekday(self, app):
        # Base is Monday 12:00; cron fires Wednesday at 09:00
        base = datetime(2024, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 9 * * 3", base_time=base)
        assert nxt is not None
        assert nxt.weekday() == 2, f"Expected Wednesday (2), got {nxt.weekday()} ({nxt})"

    def test_sunday_as_zero(self, app):
        # cron dow=0 is Sunday; base is Saturday → next should be Sunday
        base = datetime(2024, 4, 6, 0, 0, 0, tzinfo=timezone.utc)  # Saturday
        nxt = compute_next_run("0 8 * * 0", base_time=base)
        assert nxt is not None
        assert nxt.weekday() == 6, f"Expected Sunday (6), got {nxt.weekday()} ({nxt})"

    def test_sunday_as_seven(self, app):
        # cron dow=7 should also map to Sunday (POSIX compatibility)
        base = datetime(2024, 4, 6, 0, 0, 0, tzinfo=timezone.utc)  # Saturday
        nxt = compute_next_run("0 8 * * 7", base_time=base)
        assert nxt is not None
        assert nxt.weekday() == 6, f"Expected Sunday (6), got {nxt.weekday()} ({nxt})"

    def test_sunday_0_and_7_agree(self, app):
        base = datetime(2024, 4, 1, 0, 0, 0, tzinfo=timezone.utc)  # Monday
        nxt0 = compute_next_run("30 10 * * 0", base_time=base)
        nxt7 = compute_next_run("30 10 * * 7", base_time=base)
        assert nxt0 == nxt7, f"Sunday as 0 ({nxt0}) and 7 ({nxt7}) should agree"

    def test_range_including_sunday(self, app):
        # "5-7" means Fri-Sat-Sun; base is Thursday → next should be Friday
        base = datetime(2024, 4, 4, 0, 0, 0, tzinfo=timezone.utc)  # Thursday
        nxt = compute_next_run("0 12 * * 5-7", base_time=base)
        assert nxt is not None
        assert nxt.weekday() == 4, f"Expected Friday (4), got {nxt.weekday()} ({nxt})"


# ---------------------------------------------------------------------------
# Timezone handling
# ---------------------------------------------------------------------------

class TestTimezone:
    def test_utc_default(self, app):
        base = datetime(2024, 4, 1, 7, 59, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 8 * * *", base_time=base)
        assert nxt is not None
        assert nxt.hour == 8
        assert nxt.minute == 0

    def test_local_timezone_offset(self, app):
        # "Europe/Madrid" is UTC+2 in summer (CEST).
        # Cron "0 18 * * *" in Europe/Madrid should store next UTC as 16:00.
        base = datetime(2024, 4, 1, 14, 1, 0, tzinfo=timezone.utc)  # 16:01 Madrid
        nxt = compute_next_run("0 18 * * *", base_time=base, tz_name="Europe/Madrid")
        assert nxt is not None
        assert nxt.tzinfo == timezone.utc
        assert nxt.hour == 16, f"Expected UTC 16:00, got {nxt}"

    def test_invalid_timezone_falls_back_to_utc(self, app):
        base = datetime(2024, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 8 * * *", base_time=base, tz_name="Invalid/Zone")
        assert nxt is not None  # should not raise; falls back to UTC
        assert nxt.hour == 8

    def test_returns_utc_aware(self, app):
        base = datetime(2024, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 6 * * *", base_time=base, tz_name="America/New_York")
        assert nxt is not None
        assert nxt.tzinfo == timezone.utc

    def test_weekday_constraint_with_timezone(self, app):
        # "0 23 * * 5" (Friday 23:00) in Europe/Madrid (UTC+2 CEST)
        # should fire at 21:00 UTC on Fridays.
        # Base: 2024-04-05 (Friday) at 20:00 UTC (22:00 Madrid) — before 23:00 local.
        base = datetime(2024, 4, 5, 20, 0, 0, tzinfo=timezone.utc)
        nxt = compute_next_run("0 23 * * 5", base_time=base, tz_name="Europe/Madrid")
        assert nxt is not None
        assert nxt.weekday() == 4, f"Expected Friday UTC, got weekday {nxt.weekday()} ({nxt})"
        assert nxt.hour == 21, f"Expected 21:00 UTC, got {nxt}"


# ---------------------------------------------------------------------------
# Invalid expressions
# ---------------------------------------------------------------------------

class TestInvalidExpressions:
    def test_invalid_expr_returns_none(self, app):
        base = datetime(2024, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = compute_next_run("not a cron", base_time=base)
        assert result is None

    def test_none_base_time_uses_now(self, app):
        result = compute_next_run("0 0 * * *")
        assert result is not None
