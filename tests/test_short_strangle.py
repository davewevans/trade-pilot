"""Tests for the Short Strangle strategy, guardrails, and entry conditions."""

import json
from unittest.mock import MagicMock, patch

import pytest

from strategies.guardrails import Guardrails
from strategies.short_strangle_strategy import ShortStrangleState, ShortStrangleStrategy


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    ctx = {
        "symbol": "SPY",
        "confirmed_market_regime": "NEUTRAL",
        "iv_environment": "HIGH",
        "iv_rank": 60,
        "macro": {"vix": 22},
        "fundamentals": {"days_to_earnings": 50},
        "technicals": {
            "current_price": 540.0,
            "above_sma_50": True,
            "above_sma_200": True,
        },
        "volatility": {
            "iv_overvalued_label": "OVERVALUED",
            "contango_label": "NORMAL",
        },
        "spread_candidates": {
            "short_strangle": {
                "best_candidate": {
                    "expiration": "2026-05-16",
                    "dte": 32,
                    "put_leg": {
                        "symbol": "SPY260516P00520000",
                        "strike": 520,
                        "delta": -0.17,
                        "mid": 1.80,
                        "open_interest": 600,
                        "bid_ask_spread_pct": 8.0,
                    },
                    "call_leg": {
                        "symbol": "SPY260516C00560000",
                        "strike": 560,
                        "delta": 0.17,
                        "mid": 1.60,
                        "open_interest": 500,
                        "bid_ask_spread_pct": 9.0,
                    },
                    "put_credit": 1.80,
                    "call_credit": 1.60,
                    "total_credit": 3.40,
                    "spread_yield": 0.0063,  # 3.40 / 540
                    "liquidity_ok": True,
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def strategy(tmp_path):
    with patch("strategies.short_strangle_strategy.settings") as mock_settings:
        mock_settings.SNAPSHOTS_DIR = tmp_path
        broker = MagicMock()
        tracker = MagicMock()
        tracker.get_open_spreads.return_value = []
        tracker.get_active_spreads.return_value = []
        strat = ShortStrangleStrategy(broker=broker, spread_tracker=tracker)
        return strat


# ================================================================
# Pre-check entry conditions
# ================================================================


class TestPreCheckEntry:
    def test_neutral_high_iv_overvalued_passes(self, strategy):
        ctx = _base_context()
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is None
        assert score > 0

    def test_bull_regime_rejected(self, strategy):
        ctx = _base_context(confirmed_market_regime="BULL")
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "NEUTRAL" in reason
        assert score == 0.0

    def test_bear_regime_rejected(self, strategy):
        ctx = _base_context(confirmed_market_regime="BEAR")
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert score == 0.0

    def test_low_iv_rank_rejected(self, strategy):
        ctx = _base_context(iv_rank=40)
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "50" in reason
        assert score == 0.0

    def test_undervalued_iv_rejected(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "UNDERVALUED"}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "UNDERVALUED" in reason
        assert score == 0.0

    def test_backwardation_rejected(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {
            "iv_overvalued_label": "OVERVALUED",
            "contango_label": "BACKWARDATION",
        }
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "BACKWARDATION" in reason
        assert score == 0.0

    def test_earnings_within_35_days_rejected(self, strategy):
        ctx = _base_context()
        ctx["fundamentals"] = {"days_to_earnings": 30}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "35" in reason
        assert score == 0.0

    def test_low_spread_yield_rejected(self, strategy):
        ctx = _base_context()
        ctx["spread_candidates"]["short_strangle"]["best_candidate"]["spread_yield"] = 0.002
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "0.003" in reason
        assert score == 0.0

    def test_overvalued_iv_boosts_score(self, strategy):
        ctx_ov = _base_context()
        ctx_ov["volatility"] = {"iv_overvalued_label": "OVERVALUED", "contango_label": "NORMAL"}
        ctx_fair = _base_context()
        ctx_fair["volatility"] = {"iv_overvalued_label": "FAIR", "contango_label": "NORMAL"}
        _, score_ov = strategy.pre_check_entry(ctx_ov)
        _, score_fair = strategy.pre_check_entry(ctx_fair)
        assert score_ov > score_fair


# ================================================================
# Management — delta breach trigger
# ================================================================


class TestManagementDeltaBreach:
    def test_delta_breach_triggers_close(self, strategy):
        strategy.state = ShortStrangleState.OPEN
        strategy.open_spread_id = "strangle-1"

        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "strangle-1",
            "expiration": "2026-06-20",
            "entry_credit": 3.40,
            "legs": [
                {"symbol": "SPY260516P00520000", "side": "sell"},
                {"symbol": "SPY260516C00560000", "side": "sell"},
            ],
        }]
        # Short put delta has breached 0.40
        strategy.broker.get_option_snapshots.return_value = {
            "SPY260516P00520000": {"mid": 2.50, "delta": -0.45},
            "SPY260516C00560000": {"mid": 1.60, "delta": 0.17},
        }

        result = strategy._evaluate_management(_base_context())
        assert result["action"] == "CLOSE"
        assert "delta" in result["reasoning"].lower() or "0.40" in result["reasoning"]


# ================================================================
# Guardrail validation
# ================================================================


class TestGuardrailValidation:
    @pytest.fixture
    def g(self, tmp_path):
        tracker = MagicMock()
        tracker.get_open_spreads.return_value = []
        tracker.get_active_spreads.return_value = []
        return Guardrails(spread_tracker=tracker)

    def test_valid_strangle_passes(self, g):
        decision = {
            "put_symbol": "SPY260516P00520000",
            "call_symbol": "SPY260516C00560000",
            "limit_price": -3.40,
            "qty": 1,
            "total_credit": 3.40,
        }
        context = {
            "technicals": {"current_price": 540.0},
            "fundamentals": {"days_to_earnings": 50},
        }
        account = {"buying_power": 50000.0}
        ok, msg = g.validate_short_strangle_entry(decision, context, account)
        assert ok, f"Expected valid, got: {msg}"

    def test_margin_too_large_rejected(self, g):
        """Very small account where 20% margin > 25% of buying power."""
        decision = {
            "put_symbol": "SPY260516P00520000",
            "call_symbol": "SPY260516C00560000",
            "limit_price": -3.40,
            "qty": 1,
        }
        context = {
            "technicals": {"current_price": 540.0},
            "fundamentals": {"days_to_earnings": 50},
        }
        # buying_power = 5000. margin = 540*100*0.20 = 10800 > 5000*0.25 = 1250
        account = {"buying_power": 5000.0}
        ok, msg = g.validate_short_strangle_entry(decision, context, account)
        assert not ok
        assert "25%" in msg or "margin" in msg.lower()

    def test_earnings_hard_block(self, g):
        decision = {
            "put_symbol": "SPY260516P00520000",
            "call_symbol": "SPY260516C00560000",
            "limit_price": -3.40,
        }
        context = {
            "technicals": {"current_price": 540.0},
            "fundamentals": {"days_to_earnings": 10},
        }
        account = {"buying_power": 100000.0}
        ok, msg = g.validate_short_strangle_entry(decision, context, account)
        assert not ok
        assert "earnings" in msg.lower()
