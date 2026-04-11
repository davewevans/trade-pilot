"""Tests for data.market_regime — regime derivation and stability filter."""

import json

import pytest

from data.market_regime import (
    RegimeStabilityFilter,
    _classify_iv_environment,
    _classify_regime,
    _ROUTING_HINTS,
    derive_market_regime,
)


# ================================================================
# Regime classification
# ================================================================


class TestClassifyRegime:
    def test_crash_vix_35(self):
        assert _classify_regime(vix=35, below_sma_50=False, below_sma_200=False,
                                above_sma_50=True, fg_score=50) == "CRASH"

    def test_crash_vix_50(self):
        assert _classify_regime(vix=50, below_sma_50=True, below_sma_200=True,
                                above_sma_50=False, fg_score=10) == "CRASH"

    def test_bear_vix_25_below_200sma(self):
        assert _classify_regime(vix=25, below_sma_50=True, below_sma_200=True,
                                above_sma_50=False, fg_score=30) == "BEAR"

    def test_bear_vix_20_below_50sma(self):
        assert _classify_regime(vix=20, below_sma_50=True, below_sma_200=False,
                                above_sma_50=False, fg_score=40) == "BEAR"

    def test_bear_requires_below_sma(self):
        # VIX 25 but above both SMAs — not BEAR
        result = _classify_regime(vix=25, below_sma_50=False, below_sma_200=False,
                                  above_sma_50=True, fg_score=30)
        assert result != "BEAR"

    def test_euphoria(self):
        assert _classify_regime(vix=13, below_sma_50=False, below_sma_200=False,
                                above_sma_50=True, fg_score=80) == "EUPHORIA"

    def test_euphoria_requires_low_vix(self):
        result = _classify_regime(vix=15, below_sma_50=False, below_sma_200=False,
                                  above_sma_50=True, fg_score=80)
        assert result != "EUPHORIA"

    def test_euphoria_requires_high_fg(self):
        result = _classify_regime(vix=13, below_sma_50=False, below_sma_200=False,
                                  above_sma_50=True, fg_score=70)
        assert result != "EUPHORIA"

    def test_bull(self):
        assert _classify_regime(vix=16, below_sma_50=False, below_sma_200=False,
                                above_sma_50=True, fg_score=60) == "BULL"

    def test_bull_requires_above_sma50(self):
        result = _classify_regime(vix=16, below_sma_50=True, below_sma_200=False,
                                  above_sma_50=False, fg_score=60)
        assert result != "BULL"

    def test_neutral_fallback(self):
        # VIX 19 — too high for BULL, too low for BEAR
        assert _classify_regime(vix=19, below_sma_50=False, below_sma_200=False,
                                above_sma_50=True, fg_score=50) == "NEUTRAL"

    def test_neutral_when_vix_none(self):
        assert _classify_regime(vix=None, below_sma_50=False, below_sma_200=False,
                                above_sma_50=True, fg_score=50) == "NEUTRAL"

    def test_crash_takes_priority_over_bear(self):
        # VIX=40 with below-SMAs — CRASH wins over BEAR
        assert _classify_regime(vix=40, below_sma_50=True, below_sma_200=True,
                                above_sma_50=False, fg_score=10) == "CRASH"

    def test_euphoria_boundary_vix_14(self):
        # VIX exactly 14 — at boundary
        assert _classify_regime(vix=14, below_sma_50=False, below_sma_200=False,
                                above_sma_50=True, fg_score=75) == "EUPHORIA"

    def test_bull_boundary_vix_18(self):
        assert _classify_regime(vix=18, below_sma_50=False, below_sma_200=False,
                                above_sma_50=True, fg_score=50) == "BULL"


# ================================================================
# IV environment
# ================================================================


class TestIVEnvironment:
    def test_low(self):
        assert _classify_iv_environment([10, 20, 25]) == "LOW"

    def test_moderate(self):
        assert _classify_iv_environment([30, 40, 50]) == "MODERATE"

    def test_high(self):
        assert _classify_iv_environment([55, 60, 70]) == "HIGH"

    def test_empty_defaults_moderate(self):
        assert _classify_iv_environment([]) == "MODERATE"

    def test_boundary_30(self):
        assert _classify_iv_environment([30]) == "MODERATE"

    def test_boundary_50(self):
        assert _classify_iv_environment([50]) == "MODERATE"

    def test_just_above_50(self):
        assert _classify_iv_environment([51]) == "HIGH"

    def test_just_below_30(self):
        assert _classify_iv_environment([29]) == "LOW"

    def test_median_of_mixed(self):
        # [20, 40, 60] → median 40 → MODERATE
        assert _classify_iv_environment([20, 40, 60]) == "MODERATE"


# ================================================================
# derive_market_regime (full context integration)
# ================================================================


class TestDeriveMarketRegime:
    def _make_context(self, vix=18, fg=50, above_50=True, above_200=True, ivr=40):
        return {
            "macro": {
                "vix": vix,
                "fear_greed_score": fg,
            },
            "technicals": {
                "above_sma_50": above_50,
                "above_sma_200": above_200,
            },
            "iv_rank": ivr,
        }

    def test_adds_all_fields(self):
        ctx = self._make_context()
        result = derive_market_regime(ctx)
        assert "market_regime" in result
        assert "iv_environment" in result
        assert "strategy_routing_hint" in result

    def test_bull_regime(self):
        ctx = self._make_context(vix=16, fg=60, above_50=True)
        derive_market_regime(ctx)
        assert ctx["market_regime"] == "BULL"

    def test_crash_regime(self):
        ctx = self._make_context(vix=40)
        derive_market_regime(ctx)
        assert ctx["market_regime"] == "CRASH"

    def test_iv_environment_from_single_ivr(self):
        ctx = self._make_context(ivr=55)
        derive_market_regime(ctx)
        assert ctx["iv_environment"] == "HIGH"

    def test_iv_environment_from_iv_ranks_list(self):
        ctx = self._make_context()
        ctx["iv_ranks"] = [20, 25, 28]
        derive_market_regime(ctx)
        assert ctx["iv_environment"] == "LOW"

    def test_prefers_spx_technicals(self):
        ctx = self._make_context(vix=16, above_50=False)  # per-symbol below 50
        ctx["spx_technicals"] = {"above_sma_50": True, "above_sma_200": True}
        derive_market_regime(ctx)
        assert ctx["market_regime"] == "BULL"  # uses spx_technicals


# ================================================================
# Strategy routing hints
# ================================================================


class TestRoutingHints:
    def test_all_regimes_have_hints(self):
        for regime in ("CRASH", "BEAR", "NEUTRAL", "BULL", "EUPHORIA"):
            assert regime in _ROUTING_HINTS
            assert len(_ROUTING_HINTS[regime]) > 10

    def test_hint_present_in_context(self):
        ctx = {
            "macro": {"vix": 16, "fear_greed_score": 60},
            "technicals": {"above_sma_50": True, "above_sma_200": True},
        }
        derive_market_regime(ctx)
        assert "strategy_routing_hint" in ctx
        assert ctx["strategy_routing_hint"] == _ROUTING_HINTS["BULL"]


# ================================================================
# Stability filter
# ================================================================


class TestRegimeStabilityFilter:
    @pytest.fixture
    def history_path(self, tmp_path):
        return tmp_path / "regime_history.json"

    def test_requires_n_readings_to_confirm(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        f.record_reading("BULL")
        assert f.get_confirmed_regime() == "NEUTRAL"  # default, not yet confirmed
        f.record_reading("BULL")
        assert f.get_confirmed_regime() == "NEUTRAL"
        f.record_reading("BULL")
        assert f.get_confirmed_regime() == "BULL"

    def test_mixed_readings_dont_confirm(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        f.record_reading("BULL")
        f.record_reading("BEAR")
        f.record_reading("BULL")
        assert f.get_confirmed_regime() == "NEUTRAL"

    def test_unstable_keeps_previous_confirmed(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        # Confirm BULL
        f.record_reading("BULL")
        f.record_reading("BULL")
        f.record_reading("BULL")
        assert f.get_confirmed_regime() == "BULL"
        # Unstable readings
        f.record_reading("BEAR")
        f.record_reading("BULL")
        assert f.get_confirmed_regime() == "BULL"  # stays BULL

    def test_regime_change_after_n_consistent(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        # Confirm BULL
        for _ in range(3):
            f.record_reading("BULL")
        assert f.get_confirmed_regime() == "BULL"
        # Change to BEAR
        for _ in range(3):
            f.record_reading("BEAR")
        assert f.get_confirmed_regime() == "BEAR"

    def test_record_reading_returns_true_on_change(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=2)
        assert f.record_reading("CRASH") is False
        assert f.record_reading("CRASH") is True  # now confirmed

    def test_is_stable(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        f.record_reading("BULL")
        assert f.is_stable() is False
        f.record_reading("BULL")
        assert f.is_stable() is False
        f.record_reading("BULL")
        assert f.is_stable() is True

    def test_is_unstable_after_flip(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        f.record_reading("BULL")
        f.record_reading("BULL")
        f.record_reading("BEAR")  # broke the streak
        assert f.is_stable() is False

    def test_persists_across_instances(self, history_path):
        f1 = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        f1.record_reading("BEAR")
        f1.record_reading("BEAR")
        f1.record_reading("BEAR")
        assert f1.get_confirmed_regime() == "BEAR"

        # New instance reads from same file
        f2 = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        assert f2.get_confirmed_regime() == "BEAR"
        assert f2._readings == ["BEAR", "BEAR", "BEAR"]

    def test_only_keeps_last_n_readings(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=3)
        for _ in range(10):
            f.record_reading("NEUTRAL")
        assert len(f._readings) == 3

    def test_custom_required_readings(self, history_path):
        f = RegimeStabilityFilter(history_path=history_path, required_readings=5)
        for i in range(4):
            f.record_reading("CRASH")
        assert f.get_confirmed_regime() == "NEUTRAL"  # not enough
        f.record_reading("CRASH")
        assert f.get_confirmed_regime() == "CRASH"  # 5th reading confirms

    def test_creates_parent_directory(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "history.json"
        f = RegimeStabilityFilter(history_path=deep_path)
        f.record_reading("BULL")
        assert deep_path.exists()
