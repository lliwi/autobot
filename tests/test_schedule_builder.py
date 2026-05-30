"""Tests for schedule_builder: friendly frequency selection -> cron."""

import pytest

from app.services import schedule_builder


class TestBuildCron:
    def test_minutes(self):
        assert schedule_builder.build_cron({"freq_type": "minutes", "interval": 15}) == "*/15 * * * *"

    def test_minutes_default_interval(self):
        assert schedule_builder.build_cron({"freq_type": "minutes"}) == "*/1 * * * *"

    def test_hourly_every_hour(self):
        assert schedule_builder.build_cron({"freq_type": "hourly", "interval": 1, "minute": 30}) == "30 * * * *"

    def test_hourly_every_n_hours(self):
        assert schedule_builder.build_cron({"freq_type": "hourly", "interval": 3, "minute": 0}) == "0 */3 * * *"

    def test_daily(self):
        assert schedule_builder.build_cron({"freq_type": "daily", "hour": 9, "minute": 0}) == "0 9 * * *"

    def test_weekly_single_day(self):
        assert schedule_builder.build_cron({"freq_type": "weekly", "hour": 18, "minute": 30, "weekdays": [3]}) == "30 18 * * 3"

    def test_weekly_multiple_days_sorted_deduped(self):
        cron = schedule_builder.build_cron(
            {"freq_type": "weekly", "hour": 8, "minute": 0, "weekdays": [5, 1, 3, 1]}
        )
        assert cron == "0 8 * * 1,3,5"

    def test_monthly(self):
        assert schedule_builder.build_cron({"freq_type": "monthly", "day": 1, "hour": 8, "minute": 0}) == "0 8 1 * *"

    def test_advanced_passthrough(self):
        assert schedule_builder.build_cron({"freq_type": "cron", "expr": "*/15 * * * *"}) == "*/15 * * * *"

    def test_advanced_strips_whitespace(self):
        assert schedule_builder.build_cron({"freq_type": "cron", "expr": "  0 9 * * 1-5 "}) == "0 9 * * 1-5"


class TestBuildCronValidation:
    def test_empty_config(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({})

    def test_invalid_freq_type(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "yearly"})

    def test_empty_cron_expr(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "cron", "expr": "  "})

    def test_minutes_out_of_range(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "minutes", "interval": 99})

    def test_daily_hour_out_of_range(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "daily", "hour": 24, "minute": 0})

    def test_weekly_no_days(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "weekly", "hour": 8, "minute": 0, "weekdays": []})

    def test_weekly_invalid_day(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "weekly", "hour": 8, "minute": 0, "weekdays": [9]})

    def test_monthly_day_out_of_range(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "monthly", "day": 32, "hour": 8, "minute": 0})

    def test_non_integer_value(self):
        with pytest.raises(ValueError):
            schedule_builder.build_cron({"freq_type": "daily", "hour": "nine", "minute": 0})


class TestDescribe:
    def test_minutes(self):
        assert schedule_builder.describe({"freq_type": "minutes", "interval": 15}) == "Cada 15 minutos"

    def test_minutes_singular(self):
        assert schedule_builder.describe({"freq_type": "minutes", "interval": 1}) == "Cada minuto"

    def test_daily(self):
        assert schedule_builder.describe({"freq_type": "daily", "hour": 9, "minute": 5}) == "Cada día a las 09:05"

    def test_weekly(self):
        out = schedule_builder.describe({"freq_type": "weekly", "hour": 18, "minute": 30, "weekdays": [1, 3]})
        assert out == "Lunes, Miércoles a las 18:30"

    def test_monthly(self):
        out = schedule_builder.describe({"freq_type": "monthly", "day": 1, "hour": 8, "minute": 0})
        assert out == "El día 1 de cada mes a las 08:00"

    def test_cron(self):
        assert schedule_builder.describe({"freq_type": "cron", "expr": "*/15 * * * *"}) == "Cron: */15 * * * *"

    def test_none(self):
        assert schedule_builder.describe(None) == ""


class TestConfigFromForm:
    def _form(self, data):
        from werkzeug.datastructures import MultiDict
        return MultiDict(data)

    def test_no_freq_type_returns_none(self):
        assert schedule_builder.config_from_form(self._form([])) is None

    def test_minutes(self):
        cfg = schedule_builder.config_from_form(self._form([("freq_type", "minutes"), ("interval_minutes", "20")]))
        assert cfg == {"freq_type": "minutes", "interval": 20}

    def test_daily(self):
        cfg = schedule_builder.config_from_form(self._form([("freq_type", "daily"), ("at_time", "09:30")]))
        assert cfg == {"freq_type": "daily", "hour": 9, "minute": 30}

    def test_weekly(self):
        form = self._form([
            ("freq_type", "weekly"),
            ("at_time", "18:00"),
            ("weekdays", "1"),
            ("weekdays", "5"),
        ])
        cfg = schedule_builder.config_from_form(form)
        assert cfg == {"freq_type": "weekly", "hour": 18, "minute": 0, "weekdays": [1, 5]}

    def test_monthly(self):
        form = self._form([("freq_type", "monthly"), ("at_time", "08:15"), ("day_of_month", "3")])
        cfg = schedule_builder.config_from_form(form)
        assert cfg == {"freq_type": "monthly", "hour": 8, "minute": 15, "day": 3}

    def test_advanced(self):
        form = self._form([("freq_type", "cron"), ("schedule_expr", "*/10 * * * *")])
        cfg = schedule_builder.config_from_form(form)
        assert cfg == {"freq_type": "cron", "expr": "*/10 * * * *"}

    def test_roundtrip_form_to_cron(self):
        """config_from_form output feeds straight into build_cron."""
        form = self._form([("freq_type", "weekly"), ("at_time", "07:45"), ("weekdays", "1"), ("weekdays", "3"), ("weekdays", "5")])
        cfg = schedule_builder.config_from_form(form)
        assert schedule_builder.build_cron(cfg) == "45 7 * * 1,3,5"
