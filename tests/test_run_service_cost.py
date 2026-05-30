"""Tests for run_service.estimate_cost — per-model cost estimation."""

import pytest

from app.services.run_service import estimate_cost


class TestEstimateCost:
    def test_gpt52_pricing(self, app):
        with app.app_context():
            # 1000 input + 1000 output at (0.00125, 0.01) per 1k → 0.00125 + 0.01
            cost = estimate_cost("gpt-5.2", 1000, 1000)
            assert cost == pytest.approx(0.01125)

    def test_o4_mini_pricing(self, app):
        with app.app_context():
            cost = estimate_cost("o4-mini", 1000, 1000)
            assert cost == pytest.approx(0.00075)

    def test_gpt55_pricing(self, app):
        with app.app_context():
            # The model normally used; explicit gpt-5.5 entry → (0.00125, 0.01).
            assert estimate_cost("gpt-5.5", 1000, 1000) == pytest.approx(0.01125)

    def test_mini_variant_is_cheaper(self, app):
        with app.app_context():
            # "gpt-5.4-mini" must beat "gpt-5.4"/"gpt-5" (longest key) → mini rate.
            full = estimate_cost("gpt-5.4", 1000, 1000)
            mini = estimate_cost("gpt-5.4-mini", 1000, 1000)
            assert mini < full
            assert mini == pytest.approx(0.00075)

    def test_longest_key_wins(self, app):
        with app.app_context():
            # "gpt-5.2" and "gpt-5" both match the name; the longer key applies.
            assert estimate_cost("gpt-5.2", 1000, 0) == pytest.approx(0.00125)

    def test_unknown_model_uses_default(self, app):
        with app.app_context():
            default = estimate_cost("totally-unknown-model", 1000, 1000)
            gpt = estimate_cost("gpt-5.2", 1000, 1000)
            assert default == gpt  # default mirrors gpt-5.2 pricing

    def test_zero_tokens_returns_none(self, app):
        with app.app_context():
            assert estimate_cost("gpt-5.2", 0, 0) is None
            assert estimate_cost("gpt-5.2", None, None) is None

    def test_only_output_tokens(self, app):
        with app.app_context():
            assert estimate_cost("gpt-5.2", 0, 1000) == pytest.approx(0.01)

    def test_none_model_name_uses_default(self, app):
        with app.app_context():
            assert estimate_cost(None, 1000, 0) == pytest.approx(0.00125)
