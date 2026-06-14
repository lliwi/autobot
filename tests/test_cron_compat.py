"""Regression tests for cron day-of-week handling in the worker (issue #27).

APScheduler numbers DOW Monday-based and rejects 7; standard cron is
Sunday-based with 7 == Sunday. ``cron_compat`` reconciles the two so the worker
fires weekly tasks on the weekday the cron string actually means, matching
``scheduler_service.compute_next_run`` (croniter).
"""

from datetime import datetime

import pytest
from zoneinfo import ZoneInfo

from app.worker.cron_compat import build_cron_trigger, cron_dow_to_apscheduler

TZ = "Europe/Madrid"
# 2026-06-14 is a Sunday; start the search just after midnight local.
_BASE = datetime(2026, 6, 14, 0, 30, tzinfo=ZoneInfo(TZ))

_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _next_weekday(expr):
    trig = build_cron_trigger(expr, timezone=TZ)
    nf = trig.get_next_fire_time(None, _BASE)
    assert nf is not None, f"{expr} produced no fire time"
    return _NAMES[nf.weekday()]


class TestSingleDow:
    @pytest.mark.parametrize("dow,expected", [
        (0, "Sun"),
        (1, "Mon"),
        (2, "Tue"),
        (3, "Wed"),
        (4, "Thu"),
        (5, "Fri"),
        (6, "Sat"),
        (7, "Sun"),  # 7 must not crash and must mean Sunday
    ])
    def test_dow_maps_to_correct_weekday(self, dow, expected):
        assert _next_weekday(f"0 14 * * {dow}") == expected

    def test_friday_is_not_saturday(self):
        # The exact symptom from the issue.
        assert _next_weekday("0 14 * * 5") == "Fri"

    def test_saturday_is_not_sunday(self):
        assert _next_weekday("0 9 * * 6") == "Sat"


class TestRangesAndLists:
    def test_weekday_range_1_5(self):
        # mon-fri: first fire after Sunday base is Monday.
        assert _next_weekday("0 14 * * 1-5") == "Mon"

    def test_list_with_sunday(self):
        assert cron_dow_to_apscheduler("0,1,2,3,4") == "0,1,2,3,6"

    def test_weekend_list_5_6(self):
        # Fri(->4), Sat(->5)
        assert cron_dow_to_apscheduler("5,6") == "4,5"

    def test_range_1_5_field(self):
        assert cron_dow_to_apscheduler("1-5") == "0,1,2,3,4"

    def test_star_passthrough(self):
        assert cron_dow_to_apscheduler("*") == "*"

    def test_name_passthrough(self):
        # APScheduler already maps weekday names correctly.
        assert cron_dow_to_apscheduler("mon-fri") == "mon-fri"

    def test_step_every_two_days(self):
        # cron */2 over Sun..Sat -> Sun,Tue,Thu,Sat (0,2,4,6) -> aps 6,1,3,5
        assert cron_dow_to_apscheduler("*/2") == "1,3,5,6"


class TestConsistencyWithCroniter:
    """The worker trigger must agree with compute_next_run for every DOW."""

    @pytest.mark.parametrize("dow", list(range(8)))
    def test_trigger_matches_croniter(self, dow):
        from app.services.scheduler_service import compute_next_run

        expr = f"0 14 * * {dow}"
        croniter_next = compute_next_run(expr, base_time=_BASE, tz_name=TZ)
        trigger_next = build_cron_trigger(expr, timezone=TZ).get_next_fire_time(None, _BASE)

        assert croniter_next.weekday() == trigger_next.weekday(), (
            f"dow={dow}: croniter={croniter_next} trigger={trigger_next}"
        )


class TestNonStandardExpr:
    def test_non_five_field_delegates_to_apscheduler(self):
        # Not a 5-field expr: we delegate to APScheduler, which (like before this
        # fix) only accepts 5-field crontab and rejects shortcuts. The worker
        # catches this ValueError and logs the task as having an invalid cron.
        with pytest.raises(ValueError):
            build_cron_trigger("@hourly", timezone=TZ)
