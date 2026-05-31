"""Tests for timefmt: unambiguous UTC/local rendering of stored datetimes."""

from datetime import datetime, timezone

from app.utils.timefmt import as_utc, local_str, utc_iso


class TestUtcIso:
    def test_naive_treated_as_utc_and_marked_z(self):
        dt = datetime(2026, 5, 30, 7, 20, 0)  # naive (as stored in DB)
        assert utc_iso(dt) == "2026-05-30T07:20:00Z"

    def test_aware_converted_to_utc(self):
        from zoneinfo import ZoneInfo

        dt = datetime(2026, 5, 30, 9, 20, 0, tzinfo=ZoneInfo("Europe/Madrid"))  # 07:20 UTC
        assert utc_iso(dt) == "2026-05-30T07:20:00Z"

    def test_none(self):
        assert utc_iso(None) is None

    def test_drops_microseconds(self):
        dt = datetime(2026, 5, 30, 7, 20, 0, 123456)
        assert utc_iso(dt) == "2026-05-30T07:20:00Z"


class TestLocalStr:
    def test_renders_in_given_zone(self):
        dt = datetime(2026, 5, 30, 7, 20, 0)  # naive UTC → 09:20 CEST
        out = local_str(dt, "Europe/Madrid")
        assert out.startswith("2026-05-30 09:20")

    def test_unknown_zone_falls_back_to_utc(self):
        dt = datetime(2026, 5, 30, 7, 20, 0)
        out = local_str(dt, "Invalid/Zone")
        assert out.startswith("2026-05-30 07:20")

    def test_none(self):
        assert local_str(None, "Europe/Madrid") is None


class TestAsUtc:
    def test_naive(self):
        assert as_utc(datetime(2026, 1, 1, 0, 0, 0)).tzinfo == timezone.utc

    def test_none(self):
        assert as_utc(None) is None
