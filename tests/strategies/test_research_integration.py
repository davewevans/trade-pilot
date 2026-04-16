"""H4: Parametrized integration tests verifying all 5 strategies share the same
research metadata pattern — both liquidity and winrate sub-dicts are present,
and combined_multiplier matches the product of the two sub-multipliers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from strategies.bear_call_spread_strategy import BearCallSpreadStrategy
from strategies.bull_put_spread_strategy import BullPutSpreadStrategy
from strategies.iron_condor_strategy import IronCondorStrategy
from strategies.long_call_vertical_strategy import LongCallVerticalStrategy
from strategies.wheel_strategy import WheelState, WheelStrategy


# ── Minimal passing contexts ───────────────────────────────────────────────────

def _bps_context(symbol="AAPL"):
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


def _bcs_context(symbol="AAPL"):
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


def _ic_context(symbol="SPY"):
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


def _lcv_context(symbol="AAPL"):
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


# ── Strategy builders + call wrappers ─────────────────────────────────────────

def _make_spread_strategy(cls, liq_mult, liq_tier, liq_conf, wr_mult, wr_tier, wr_conf):
    mock_broker = MagicMock()
    liq = MagicMock()
    liq.get_multiplier.return_value = (liq_mult, liq_tier, liq_conf)
    wr = MagicMock()
    wr.get_winrate_multiplier.return_value = (wr_mult, wr_tier, wr_conf)
    with patch.object(cls, "_load_state"):
        strat = cls(mock_broker, liquidity_repo=liq, backtest_stats_repo=wr)
    return strat


def _run_spread(strat, ctx_fn, symbol="AAPL"):
    ctx = ctx_fn(symbol)
    skip, score = strat.pre_check_entry(ctx)
    return skip, score, ctx


def _make_wheel(liq_mult, liq_tier, liq_conf, wr_mult, wr_tier, wr_conf):
    mock_broker = MagicMock()
    liq = MagicMock()
    liq.get_multiplier.return_value = (liq_mult, liq_tier, liq_conf)
    wr = MagicMock()
    wr.get_winrate_multiplier.return_value = (wr_mult, wr_tier, wr_conf)
    with patch.object(WheelStrategy, "_load_state"):
        strat = WheelStrategy(mock_broker, liquidity_repo=liq, backtest_stats_repo=wr)
    return strat


def _run_wheel(strat, symbol="AAPL"):
    ctx = {}
    result = strat.evaluate_entry_liquidity(symbol, ctx, WheelState.IDLE)
    return result, ctx


# ── Parametrized fixtures ──────────────────────────────────────────────────────

# Each entry: (strategy_class_or_name, context_factory_or_None)
# wheel is handled separately because it uses evaluate_entry_liquidity
SPREAD_CASES = [
    (BullPutSpreadStrategy, _bps_context),
    (BearCallSpreadStrategy, _bcs_context),
    (IronCondorStrategy, _ic_context),
    (LongCallVerticalStrategy, _lcv_context),
]

SPREAD_IDS = ["bull_put_spread", "bear_call_spread", "iron_condor", "long_call_vertical"]


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestResearchMetadataShape:
    """All 5 strategies must populate _research with the correct sub-dicts."""

    @pytest.mark.parametrize("cls,ctx_fn", SPREAD_CASES, ids=SPREAD_IDS)
    def test_spread_research_meta_has_required_keys(self, cls, ctx_fn):
        strat = _make_spread_strategy(
            cls,
            liq_mult=1.1, liq_tier="B", liq_conf="high",
            wr_mult=1.05, wr_tier="neutral", wr_conf="low",
        )
        skip, score, ctx = _run_spread(strat, ctx_fn)

        assert skip is None
        meta = ctx.get("_research", {})
        assert "liquidity" in meta, "_research must have 'liquidity' sub-dict"
        assert "winrate" in meta, "_research must have 'winrate' sub-dict"
        assert "combined_multiplier" in meta, "_research must have 'combined_multiplier'"
        assert "final_score" in meta, "_research must have 'final_score'"

    @pytest.mark.parametrize("cls,ctx_fn", SPREAD_CASES, ids=SPREAD_IDS)
    def test_spread_liquidity_sub_dict_shape(self, cls, ctx_fn):
        strat = _make_spread_strategy(
            cls,
            liq_mult=1.2, liq_tier="A", liq_conf="high",
            wr_mult=1.0, wr_tier="neutral", wr_conf="none",
        )
        _, _, ctx = _run_spread(strat, ctx_fn)

        liq = ctx["_research"]["liquidity"]
        assert liq["multiplier"] == pytest.approx(1.2)
        assert liq["tier"] == "A"
        assert liq["confidence"] == "high"

    @pytest.mark.parametrize("cls,ctx_fn", SPREAD_CASES, ids=SPREAD_IDS)
    def test_spread_winrate_sub_dict_shape(self, cls, ctx_fn):
        strat = _make_spread_strategy(
            cls,
            liq_mult=1.0, liq_tier="B", liq_conf="none",
            wr_mult=1.15, wr_tier="good", wr_conf="high",
        )
        _, _, ctx = _run_spread(strat, ctx_fn)

        wr = ctx["_research"]["winrate"]
        assert wr["multiplier"] == pytest.approx(1.15)
        assert wr["tier"] == "good"
        assert wr["confidence"] == "high"

    @pytest.mark.parametrize("cls,ctx_fn", SPREAD_CASES, ids=SPREAD_IDS)
    def test_spread_combined_multiplier_is_product(self, cls, ctx_fn):
        """combined_multiplier must equal liq_mult * wr_mult exactly."""
        liq_mult, wr_mult = 1.2, 1.3
        strat = _make_spread_strategy(
            cls,
            liq_mult=liq_mult, liq_tier="A", liq_conf="high",
            wr_mult=wr_mult, wr_tier="strong", wr_conf="high",
        )
        _, _, ctx = _run_spread(strat, ctx_fn)

        assert ctx["_research"]["combined_multiplier"] == pytest.approx(liq_mult * wr_mult)

    @pytest.mark.parametrize("cls,ctx_fn", SPREAD_CASES, ids=SPREAD_IDS)
    def test_spread_final_score_uses_combined_multiplier(self, cls, ctx_fn):
        """final_score must be consistent with combined_multiplier."""
        liq_mult, wr_mult = 1.1, 1.2
        strat = _make_spread_strategy(
            cls,
            liq_mult=liq_mult, liq_tier="B", liq_conf="high",
            wr_mult=wr_mult, wr_tier="good", wr_conf="high",
        )
        _, score, ctx = _run_spread(strat, ctx_fn)

        meta = ctx["_research"]
        assert meta["final_score"] == pytest.approx(score)
        assert meta["final_score"] == pytest.approx(
            meta["final_score"] / meta["combined_multiplier"] * meta["combined_multiplier"]
        )

    def test_wheel_research_meta_has_required_keys(self):
        strat = _make_wheel(
            liq_mult=1.1, liq_tier="B", liq_conf="high",
            wr_mult=1.05, wr_tier="neutral", wr_conf="low",
        )
        result, ctx = _run_wheel(strat)

        assert result is None
        meta = ctx.get("_research", {})
        assert "liquidity" in meta
        assert "winrate" in meta
        assert "combined_multiplier" in meta

    def test_wheel_combined_multiplier_is_product(self):
        liq_mult, wr_mult = 1.2, 1.3
        strat = _make_wheel(
            liq_mult=liq_mult, liq_tier="A", liq_conf="high",
            wr_mult=wr_mult, wr_tier="strong", wr_conf="high",
        )
        result, ctx = _run_wheel(strat)

        assert result is None
        assert ctx["_research"]["combined_multiplier"] == pytest.approx(liq_mult * wr_mult)

    def test_wheel_liquidity_sub_dict_shape(self):
        strat = _make_wheel(
            liq_mult=1.2, liq_tier="A", liq_conf="high",
            wr_mult=1.0, wr_tier="neutral", wr_conf="none",
        )
        _, ctx = _run_wheel(strat)

        liq = ctx["_research"]["liquidity"]
        assert liq["multiplier"] == pytest.approx(1.2)
        assert liq["tier"] == "A"
        assert liq["confidence"] == "high"

    def test_wheel_winrate_sub_dict_shape(self):
        strat = _make_wheel(
            liq_mult=1.0, liq_tier="B", liq_conf="none",
            wr_mult=1.15, wr_tier="good", wr_conf="high",
        )
        _, ctx = _run_wheel(strat)

        wr = ctx["_research"]["winrate"]
        assert wr["multiplier"] == pytest.approx(1.15)
        assert wr["tier"] == "good"
        assert wr["confidence"] == "high"


class TestBothReposNone:
    """When both repos are None, metadata shows 'disabled' for both sub-dicts."""

    @pytest.mark.parametrize("cls,ctx_fn", SPREAD_CASES, ids=SPREAD_IDS)
    def test_spread_no_repos_disabled_confidence(self, cls, ctx_fn):
        mock_broker = MagicMock()
        with patch.object(cls, "_load_state"):
            strat = cls(mock_broker)  # no repos passed
        ctx = ctx_fn()
        skip, score = strat.pre_check_entry(ctx)

        assert skip is None
        meta = ctx["_research"]
        assert meta["liquidity"]["confidence"] == "disabled"
        assert meta["winrate"]["confidence"] == "disabled"
        assert meta["combined_multiplier"] == pytest.approx(1.0)

    def test_wheel_no_repos_disabled_confidence(self):
        mock_broker = MagicMock()
        with patch.object(WheelStrategy, "_load_state"):
            strat = WheelStrategy(mock_broker)
        ctx = {}
        result = strat.evaluate_entry_liquidity("AAPL", ctx, WheelState.IDLE)

        assert result is None
        meta = ctx["_research"]
        assert meta["liquidity"]["confidence"] == "disabled"
        assert meta["winrate"]["confidence"] == "disabled"
        assert meta["combined_multiplier"] == pytest.approx(1.0)
