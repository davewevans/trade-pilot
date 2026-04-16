"""Tests for win-rate + liquidity multiplier integration in pre_check_entry.

Covers all 5 strategies (4 spread strategies via pre_check_entry, wheel via
evaluate_entry_liquidity). Both kill switches are tested independently.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from strategies.bear_call_spread_strategy import BearCallSpreadStrategy
from strategies.bull_put_spread_strategy import BullPutSpreadStrategy
from strategies.iron_condor_strategy import IronCondorStrategy
from strategies.long_call_vertical_strategy import LongCallVerticalStrategy
from strategies.wheel_strategy import WheelState, WheelStrategy


# ── Context builders ───────────────────────────────────────────────────────────

def _bps_context(symbol="AAPL") -> dict:
    """Minimal context that passes all BullPutSpread hard checks."""
    return {
        "symbol": symbol,
        "confirmed_market_regime": "BULL",
        "iv_rank": 45,
        "technicals": {"above_sma_50": True, "current_price": 200.0},
        "fundamentals": {"days_to_earnings": 35},
        "volatility": {"iv_hv_ratio": 1.0, "iv_overvalued_label": None},
        "spread_candidates": {
            "bull_put_spread": {
                "best_candidate": {
                    "net_credit": 1.50,
                    "spread_yield": 0.01,
                    "credit_to_width_ratio": 0.20,
                    "ev_score": 0.80,
                },
            },
        },
    }


def _bcs_context(symbol="AAPL") -> dict:
    """Minimal context that passes all BearCallSpread hard checks."""
    return {
        "symbol": symbol,
        "confirmed_market_regime": "BEAR",
        "iv_rank": 50,
        "fundamentals": {"days_to_earnings": 35, "days_to_ex_dividend": 50},
        "volatility": {"iv_overvalued_label": "OVERVALUED"},
        "spread_candidates": {
            "bear_call_spread": {
                "best_candidate": {
                    "net_credit": 1.20,
                    "spread_yield": 0.01,
                    "credit_to_width_ratio": 0.20,
                    "ev_score": 0.75,
                },
            },
        },
    }


def _ic_context(symbol="SPY") -> dict:
    """Minimal context that passes all IronCondor hard checks."""
    return {
        "symbol": symbol,
        "confirmed_market_regime": "NEUTRAL",
        "iv_rank": 55,
        "iv_environment": "HIGH",
        "macro": {"vix": 22},
        "fundamentals": {"days_to_earnings": 40},
        "volatility": {"iv_overvalued_label": "OVERVALUED", "contango_label": "CONTANGO"},
        "spread_candidates": {
            "iron_condor": {
                "iron_condor_legs": {
                    "total_credit": 2.00,
                    "total_ev_score": 1.20,
                },
            },
        },
    }


def _lcv_context(symbol="AAPL") -> dict:
    """Minimal context that passes all LongCallVertical hard checks."""
    return {
        "symbol": symbol,
        "confirmed_market_regime": "BULL",
        "iv_environment": "LOW",
        "iv_rank": 20,
        "technicals": {"above_sma_50": True, "current_price": 200.0},
        "fundamentals": {"days_to_earnings": 70},
        "volatility": {"iv_hv_ratio": 1.0, "iv_overvalued_label": None},
        "support_bounce_signal": {"cahold_detected": True},
        "spread_candidates": {
            "long_call_vertical": {
                "best_candidate": {
                    "net_debit": 1.20,
                    "max_gain": 3.80,
                    "ev_score": 2.50,
                },
            },
        },
    }


# ── Strategy factories ─────────────────────────────────────────────────────────

def _make_bps(liq_mult=1.0, liq_tier="B", liq_conf="disabled",
              wr_mult=1.0, wr_tier="neutral", wr_conf="disabled",
              liq_repo=True, wr_repo=True):
    mock_broker = MagicMock()
    liq = MagicMock() if liq_repo else None
    wr = MagicMock() if wr_repo else None
    if liq:
        liq.get_multiplier.return_value = (liq_mult, liq_tier, liq_conf)
    if wr:
        wr.get_winrate_multiplier.return_value = (wr_mult, wr_tier, wr_conf)
    with patch.object(BullPutSpreadStrategy, "_load_state"):
        strat = BullPutSpreadStrategy(mock_broker, liquidity_repo=liq,
                                      backtest_stats_repo=wr)
    return strat


def _make_bcs(liq_mult=1.0, liq_tier="B", liq_conf="disabled",
              wr_mult=1.0, wr_tier="neutral", wr_conf="disabled",
              liq_repo=True, wr_repo=True):
    mock_broker = MagicMock()
    liq = MagicMock() if liq_repo else None
    wr = MagicMock() if wr_repo else None
    if liq:
        liq.get_multiplier.return_value = (liq_mult, liq_tier, liq_conf)
    if wr:
        wr.get_winrate_multiplier.return_value = (wr_mult, wr_tier, wr_conf)
    with patch.object(BearCallSpreadStrategy, "_load_state"):
        strat = BearCallSpreadStrategy(mock_broker, liquidity_repo=liq,
                                       backtest_stats_repo=wr)
    return strat


def _make_ic(liq_mult=1.0, liq_tier="B", liq_conf="disabled",
             wr_mult=1.0, wr_tier="neutral", wr_conf="disabled",
             liq_repo=True, wr_repo=True):
    mock_broker = MagicMock()
    liq = MagicMock() if liq_repo else None
    wr = MagicMock() if wr_repo else None
    if liq:
        liq.get_multiplier.return_value = (liq_mult, liq_tier, liq_conf)
    if wr:
        wr.get_winrate_multiplier.return_value = (wr_mult, wr_tier, wr_conf)
    with patch.object(IronCondorStrategy, "_load_state"):
        strat = IronCondorStrategy(mock_broker, liquidity_repo=liq,
                                   backtest_stats_repo=wr)
    return strat


def _make_lcv(liq_mult=1.0, liq_tier="B", liq_conf="disabled",
              wr_mult=1.0, wr_tier="neutral", wr_conf="disabled",
              liq_repo=True, wr_repo=True):
    mock_broker = MagicMock()
    liq = MagicMock() if liq_repo else None
    wr = MagicMock() if wr_repo else None
    if liq:
        liq.get_multiplier.return_value = (liq_mult, liq_tier, liq_conf)
    if wr:
        wr.get_winrate_multiplier.return_value = (wr_mult, wr_tier, wr_conf)
    with patch.object(LongCallVerticalStrategy, "_load_state"):
        strat = LongCallVerticalStrategy(mock_broker, liquidity_repo=liq,
                                         backtest_stats_repo=wr)
    return strat


def _make_wheel(liq_mult=1.0, liq_tier="B", liq_conf="disabled",
                wr_mult=1.0, wr_tier="neutral", wr_conf="disabled",
                liq_repo=True, wr_repo=True):
    mock_broker = MagicMock()
    liq = MagicMock() if liq_repo else None
    wr = MagicMock() if wr_repo else None
    if liq:
        liq.get_multiplier.return_value = (liq_mult, liq_tier, liq_conf)
    if wr:
        wr.get_winrate_multiplier.return_value = (wr_mult, wr_tier, wr_conf)
    with patch.object(WheelStrategy, "_load_state"):
        strat = WheelStrategy(mock_broker, liquidity_repo=liq,
                              backtest_stats_repo=wr)
    return strat


# ── BullPutSpread tests ────────────────────────────────────────────────────────

class TestBullPutSpreadWinRate:

    def test_both_repos_none_raw_score_unchanged(self):
        strat = _make_bps(liq_repo=False, wr_repo=False)
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(0.80)  # ev_score unchanged
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"

    def test_liq_tier_a_wr_strong_stacked(self):
        strat = _make_bps(liq_mult=1.2, liq_tier="A", liq_conf="high",
                          wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(0.80 * 1.2 * 1.3)

    def test_wr_reject_returns_below_winrate_floor(self):
        strat = _make_bps(liq_mult=1.2, liq_tier="A", liq_conf="high",
                          wr_mult=0.0, wr_tier="reject", wr_conf="high")
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_winrate_floor"
        assert score == pytest.approx(0.0)

    def test_liq_tier_d_checked_before_winrate(self):
        strat = _make_bps(liq_mult=0.0, liq_tier="D", liq_conf="high",
                          wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_liquidity_floor"
        assert score == pytest.approx(0.0)

    def test_liq_neutral_wr_low_confidence_combined_one(self):
        strat = _make_bps(liq_mult=1.0, liq_tier="B", liq_conf="high",
                          wr_mult=1.0, wr_tier="neutral", wr_conf="low")
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(0.80 * 1.0)

    def test_wr_kill_switch_off_liq_active(self, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", False)
        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.return_value = (1.0, "neutral", "disabled")
        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.2, "A", "high")
        mock_broker = MagicMock()
        with patch.object(BullPutSpreadStrategy, "_load_state"):
            strat = BullPutSpreadStrategy(mock_broker, liquidity_repo=mock_liq,
                                          backtest_stats_repo=mock_wr)
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(0.80 * 1.2)
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"

    def test_both_kill_switches_off(self, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", False)
        monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", False)
        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.return_value = (1.0, "neutral", "disabled")
        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.0, "B", "disabled")
        mock_broker = MagicMock()
        with patch.object(BullPutSpreadStrategy, "_load_state"):
            strat = BullPutSpreadStrategy(mock_broker, liquidity_repo=mock_liq,
                                          backtest_stats_repo=mock_wr)
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(0.80)
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"

    def test_wr_repo_raises_falls_back_neutral(self):
        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.0, "B", "high")
        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.side_effect = RuntimeError("db error")
        mock_broker = MagicMock()
        with patch.object(BullPutSpreadStrategy, "_load_state"):
            strat = BullPutSpreadStrategy(mock_broker, liquidity_repo=mock_liq,
                                          backtest_stats_repo=mock_wr)
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        # Fallback to (1.0, 'neutral', 'none') — no skip, liquidity still applied
        assert skip is None
        assert ctx["_research"]["winrate"]["confidence"] == "none"

    def test_wr_good_multiplier(self):
        strat = _make_bps(liq_mult=1.2, liq_tier="A", liq_conf="high",
                          wr_mult=1.15, wr_tier="good", wr_conf="high")
        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(0.80 * 1.2 * 1.15)


# ── BearCallSpread tests ───────────────────────────────────────────────────────

class TestBearCallSpreadWinRate:

    def test_both_repos_none_raw_score_unchanged(self):
        strat = _make_bcs(liq_repo=False, wr_repo=False)
        ctx = _bcs_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        # ev_score=0.75, OVERVALUED bonus * 1.15 = 0.8625
        assert score == pytest.approx(0.75 * 1.15)
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"

    def test_liq_tier_a_wr_strong_stacked(self):
        strat = _make_bcs(liq_mult=1.2, liq_tier="A", liq_conf="high",
                          wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _bcs_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(0.75 * 1.15 * 1.2 * 1.3)

    def test_wr_reject_returns_below_winrate_floor(self):
        strat = _make_bcs(liq_mult=1.0, liq_tier="B", liq_conf="high",
                          wr_mult=0.0, wr_tier="reject", wr_conf="high")
        ctx = _bcs_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_winrate_floor"
        assert score == pytest.approx(0.0)

    def test_liq_tier_d_checked_before_winrate(self):
        strat = _make_bcs(liq_mult=0.0, liq_tier="D", liq_conf="high",
                          wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _bcs_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_liquidity_floor"
        assert score == pytest.approx(0.0)


# ── IronCondor tests ───────────────────────────────────────────────────────────

class TestIronCondorWinRate:

    def test_both_repos_none_raw_score_unchanged(self):
        strat = _make_ic(liq_repo=False, wr_repo=False)
        ctx = _ic_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        # total_ev_score=1.20 * OVERVALUED bonus 1.20 = 1.44
        assert score == pytest.approx(1.20 * 1.20)
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"

    def test_liq_tier_a_wr_strong_stacked(self):
        strat = _make_ic(liq_mult=1.2, liq_tier="A", liq_conf="high",
                         wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _ic_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(1.20 * 1.20 * 1.2 * 1.3)

    def test_wr_reject_returns_below_winrate_floor(self):
        strat = _make_ic(liq_mult=1.0, liq_tier="B", liq_conf="high",
                         wr_mult=0.0, wr_tier="reject", wr_conf="high")
        ctx = _ic_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_winrate_floor"
        assert score == pytest.approx(0.0)

    def test_liq_tier_d_checked_before_winrate(self):
        strat = _make_ic(liq_mult=0.0, liq_tier="D", liq_conf="high",
                         wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _ic_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_liquidity_floor"
        assert score == pytest.approx(0.0)


# ── LongCallVertical tests ─────────────────────────────────────────────────────

class TestLongCallVerticalWinRate:

    def test_both_repos_none_raw_score_unchanged(self):
        strat = _make_lcv(liq_repo=False, wr_repo=False)
        ctx = _lcv_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(2.50)  # ev_score, no IV bonus (None label)
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"

    def test_liq_tier_a_wr_strong_stacked(self):
        strat = _make_lcv(liq_mult=1.2, liq_tier="A", liq_conf="high",
                          wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _lcv_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip is None
        assert score == pytest.approx(2.50 * 1.2 * 1.3)

    def test_wr_reject_returns_below_winrate_floor(self):
        strat = _make_lcv(liq_mult=1.0, liq_tier="B", liq_conf="high",
                          wr_mult=0.0, wr_tier="reject", wr_conf="high")
        ctx = _lcv_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_winrate_floor"
        assert score == pytest.approx(0.0)

    def test_liq_tier_d_checked_before_winrate(self):
        strat = _make_lcv(liq_mult=0.0, liq_tier="D", liq_conf="high",
                          wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = _lcv_context()
        skip, score = strat.pre_check_entry(ctx)
        assert skip == "below_liquidity_floor"
        assert score == pytest.approx(0.0)


# ── Wheel strategy tests ───────────────────────────────────────────────────────

class TestWheelWinRate:

    def test_both_repos_none_no_skip_disabled_confidence(self):
        strat = _make_wheel(liq_repo=False, wr_repo=False)
        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)
        assert result is None
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"

    def test_liq_tier_a_wr_strong_combined_multiplier(self):
        strat = _make_wheel(liq_mult=1.2, liq_tier="A", liq_conf="high",
                            wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)
        assert result is None
        assert ctx["_research"]["combined_multiplier"] == pytest.approx(1.2 * 1.3)

    def test_wr_reject_returns_skip_dict(self):
        strat = _make_wheel(liq_mult=1.0, liq_tier="B", liq_conf="high",
                            wr_mult=0.0, wr_tier="reject", wr_conf="high")
        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)
        assert result is not None
        assert result["action"] == "SKIP"
        assert result["skip_reason"] == "below_winrate_floor"

    def test_liq_tier_d_checked_before_winrate(self):
        strat = _make_wheel(liq_mult=0.0, liq_tier="D", liq_conf="high",
                            wr_mult=1.3, wr_tier="strong", wr_conf="high")
        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)
        assert result is not None
        assert result["skip_reason"] == "below_liquidity_floor"

    def test_management_state_skips_winrate_check(self):
        strat = _make_wheel(liq_mult=0.0, wr_mult=0.0)
        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.SHORT_PUT)
        assert result is None  # management state — no gating

    def test_wr_repo_raises_falls_back_neutral(self):
        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.side_effect = RuntimeError("db error")
        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.0, "B", "high")
        mock_broker = MagicMock()
        with patch.object(WheelStrategy, "_load_state"):
            strat = WheelStrategy(mock_broker, liquidity_repo=mock_liq,
                                  backtest_stats_repo=mock_wr)
        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)
        assert result is None  # fallback, no skip
        assert ctx["_research"]["winrate"]["confidence"] == "none"


# ── H5: Kill switch verification (NON-NEGOTIABLE) ─────────────────────────────

class TestKillSwitches:

    def test_winrate_kill_switch_off_suppresses_reject(self, monkeypatch):
        """Kill switch off → multiplier=1.0, no reject even for win_rate=0.25."""
        from config import settings
        monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", False)

        mock_wr = MagicMock()
        # Repository returns (1.0, 'neutral', 'disabled') when kill switch off
        mock_wr.get_winrate_multiplier.return_value = (1.0, "neutral", "disabled")
        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.0, "B", "high")
        mock_broker = MagicMock()

        with patch.object(BullPutSpreadStrategy, "_load_state"):
            strat = BullPutSpreadStrategy(mock_broker, liquidity_repo=mock_liq,
                                          backtest_stats_repo=mock_wr)

        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)

        # Must NOT skip
        assert skip is None
        assert skip != "below_winrate_floor"
        # Win-rate multiplier must be 1.0
        assert ctx["_research"]["winrate"]["multiplier"] == pytest.approx(1.0)
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"
        # Score is raw * 1.0 (liquidity neutral) * 1.0 (winrate disabled)
        assert score == pytest.approx(0.80)

    def test_winrate_kill_switch_off_wheel_no_reject(self, monkeypatch):
        """Wheel: kill switch off → no below_winrate_floor skip even for reject tier."""
        from config import settings
        monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", False)

        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.return_value = (1.0, "neutral", "disabled")
        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.0, "B", "high")
        mock_broker = MagicMock()

        with patch.object(WheelStrategy, "_load_state"):
            strat = WheelStrategy(mock_broker, liquidity_repo=mock_liq,
                                  backtest_stats_repo=mock_wr)

        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)

        assert result is None  # no skip
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"
        assert ctx["_research"]["winrate"]["multiplier"] == pytest.approx(1.0)

    def test_liquidity_kill_switch_off_suppresses_tier_d(self, monkeypatch):
        """Liquidity kill switch off → no below_liquidity_floor skip."""
        from config import settings
        monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", False)

        mock_liq = MagicMock()
        # get_multiplier returns (1.0, 'B', 'disabled') when kill switch off
        mock_liq.get_multiplier.return_value = (1.0, "B", "disabled")
        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.return_value = (1.0, "neutral", "disabled")
        mock_broker = MagicMock()

        with patch.object(BullPutSpreadStrategy, "_load_state"):
            strat = BullPutSpreadStrategy(mock_broker, liquidity_repo=mock_liq,
                                          backtest_stats_repo=mock_wr)

        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)

        # Must NOT skip
        assert skip is None
        assert skip != "below_liquidity_floor"
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"

    def test_liquidity_kill_switch_off_wheel(self, monkeypatch):
        """Wheel: liquidity kill switch off → no below_liquidity_floor skip."""
        from config import settings
        monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", False)

        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.0, "B", "disabled")
        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.return_value = (1.0, "neutral", "disabled")
        mock_broker = MagicMock()

        with patch.object(WheelStrategy, "_load_state"):
            strat = WheelStrategy(mock_broker, liquidity_repo=mock_liq,
                                  backtest_stats_repo=mock_wr)

        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)

        assert result is None
        assert ctx["_research"]["liquidity"]["confidence"] == "disabled"

    def test_both_kill_switches_independent_bps(self, monkeypatch):
        """Toggling one kill switch does not affect the other."""
        from config import settings
        # Only winrate disabled
        monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", False)
        monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", True)

        mock_liq = MagicMock()
        mock_liq.get_multiplier.return_value = (1.2, "A", "high")
        mock_wr = MagicMock()
        mock_wr.get_winrate_multiplier.return_value = (1.0, "neutral", "disabled")
        mock_broker = MagicMock()

        with patch.object(BullPutSpreadStrategy, "_load_state"):
            strat = BullPutSpreadStrategy(mock_broker, liquidity_repo=mock_liq,
                                          backtest_stats_repo=mock_wr)

        ctx = _bps_context()
        skip, score = strat.pre_check_entry(ctx)

        # Liquidity still active (1.2x), winrate disabled (1.0x)
        assert skip is None
        assert score == pytest.approx(0.80 * 1.2)
        assert ctx["_research"]["liquidity"]["tier"] == "A"
        assert ctx["_research"]["winrate"]["confidence"] == "disabled"
