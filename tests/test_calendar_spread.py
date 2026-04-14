"""Tests for the Calendar Spread strategy, guardrails, and entry conditions."""

import json
from unittest.mock import MagicMock, patch

import pytest

from strategies.guardrails import Guardrails
from strategies.calendar_spread_strategy import CalendarSpreadState, CalendarSpreadStrategy


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    ctx = {
        "symbol": "AAPL",
        "confirmed_market_regime": "NEUTRAL",
        "iv_environment": "LOW",
        "iv_rank": 22,
        "macro": {"vix": 14},
        "fundamentals": {
            "days_to_earnings": 50,
            "next_earnings_date": "2026-06-15",
        },
        "technicals": {
            "current_price": 185.0,
            "above_sma_50": True,
            "above_sma_200": True,
            "atr_14": 3.50,
        },
        "volatility": {
            "iv_overvalued_label": "FAIR",
            "contango_label": "NORMAL",
        },
        "spread_candidates": {
            "calendar_spread": {
                "best_candidate": {
                    "short_expiration": "2026-05-15",
                    "long_expiration": "2026-07-17",
                    "strike": 185.0,
                    "short_dte": 31,
                    "long_dte": 93,
                    "short_symbol": "AAPL260515C00185000",
                    "long_symbol": "AAPL260717C00185000",
                    "short_leg": {
                        "symbol": "AAPL260515C00185000",
                        "strike": 185.0,
                        "delta": 0.52,
                        "mid": 3.50,
                        "open_interest": 500,
                    },
                    "long_leg": {
                        "symbol": "AAPL260717C00185000",
                        "strike": 185.0,
                        "delta": 0.50,
                        "mid": 5.20,
                        "open_interest": 300,
                    },
                    "net_debit": 1.70,
                    "liquidity_ok": True,
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def strategy(tmp_path):
    with patch("strategies.calendar_spread_strategy.settings") as mock_settings:
        mock_settings.SNAPSHOTS_DIR = tmp_path
        broker = MagicMock()
        tracker = MagicMock()
        tracker.get_open_spreads.return_value = []
        tracker.get_active_spreads.return_value = []
        strat = CalendarSpreadStrategy(broker=broker, spread_tracker=tracker)
        return strat


# ================================================================
# Pre-check entry conditions
# ================================================================


class TestPreCheckEntry:
    def test_neutral_low_iv_normal_contango_passes(self, strategy):
        reason, score = strategy.pre_check_entry(_base_context())
        assert reason is None
        assert score > 0

    def test_bull_regime_rejected(self, strategy):
        ctx = _base_context(confirmed_market_regime="BULL")
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "NEUTRAL" in reason
        assert score == 0.0

    def test_high_iv_environment_rejected(self, strategy):
        ctx = _base_context(iv_environment="HIGH")
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "HIGH" in reason
        assert score == 0.0

    def test_backwardation_contango_rejected(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "FAIR", "contango_label": "BACKWARDATION"}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "BACKWARDATION" in reason
        assert score == 0.0

    def test_flat_contango_rejected(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "FAIR", "contango_label": "FLAT"}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "FLAT" in reason or "marginal" in reason.lower()
        assert score == 0.0

    def test_overvalued_iv_rejected(self, strategy):
        """Calendar buys the long leg — overvalued IV makes it expensive."""
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "OVERVALUED", "contango_label": "NORMAL"}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "OVERVALUED" in reason
        assert score == 0.0

    def test_earnings_too_close_rejected(self, strategy):
        ctx = _base_context()
        ctx["fundamentals"]["days_to_earnings"] = 25
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "35" in reason or "earnings" in reason.lower()
        assert score == 0.0


# ================================================================
# Management — ATR breach and roll logic
# ================================================================


class TestManagement:
    def _open_strategy(self, strategy, entry_debit=1.70):
        strategy.state = CalendarSpreadState.OPEN
        strategy.open_spread_id = "cal-1"
        strategy._roll_count = 0
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "cal-1",
            "expiration": "2026-05-15",
            "entry_credit": entry_debit,
            "entry_debit": entry_debit,
            "strike": 185.0,
            "legs": [
                {"symbol": "AAPL260515C00185000", "side": "sell"},
                {"symbol": "AAPL260717C00185000", "side": "buy"},
            ],
        }]

    def test_atr_breach_triggers_close(self, strategy):
        self._open_strategy(strategy)
        # Underlying moved 5.0 > ATR of 3.50
        ctx = _base_context()
        ctx["technicals"]["current_price"] = 191.0  # 6 points from strike 185
        strategy.broker.get_option_snapshots.return_value = {
            "AAPL260515C00185000": {"mid": 5.0, "delta": 0.65},
            "AAPL260717C00185000": {"mid": 6.5, "delta": 0.60},
        }
        result = strategy._evaluate_management(ctx)
        assert result["action"] == "CLOSE"
        assert "ATR" in result["reasoning"] or "atr" in result["reasoning"].lower()

    def test_short_leg_dte_triggers_roll(self, strategy):
        self._open_strategy(strategy)
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "cal-1",
            "expiration": "2026-04-16",  # 2 days away
            "entry_credit": 1.70,
            "entry_debit": 1.70,
            "strike": 185.0,
            "legs": [
                {"symbol": "AAPL260416C00185000", "side": "sell"},
                {"symbol": "AAPL260717C00185000", "side": "buy"},
            ],
        }]
        strategy.broker.get_option_snapshots.return_value = {
            "AAPL260416C00185000": {"mid": 0.40, "delta": 0.35},
            "AAPL260717C00185000": {"mid": 2.20, "delta": 0.45},
        }
        result = strategy._evaluate_management(_base_context())
        assert result["action"] == "ROLL_SHORT"

    def test_max_rolls_reached_closes(self, strategy):
        self._open_strategy(strategy)
        strategy._roll_count = 2
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "cal-1",
            "expiration": "2026-04-16",  # 2 days away
            "entry_credit": 1.70,
            "entry_debit": 1.70,
            "strike": 185.0,
            "legs": [
                {"symbol": "AAPL260416C00185000", "side": "sell"},
                {"symbol": "AAPL260717C00185000", "side": "buy"},
            ],
        }]
        strategy.broker.get_option_snapshots.return_value = {
            "AAPL260416C00185000": {"mid": 0.40, "delta": 0.35},
            "AAPL260717C00185000": {"mid": 2.20, "delta": 0.45},
        }
        result = strategy._evaluate_management(_base_context())
        assert result["action"] == "CLOSE"
        assert "max" in result["reasoning"].lower() or "roll" in result["reasoning"].lower()


# ================================================================
# Guardrail validation
# ================================================================


class TestGuardrailValidation:
    @pytest.fixture
    def g(self, tmp_path):
        tracker = MagicMock()
        tracker.get_open_spreads.return_value = []
        return Guardrails(spread_tracker=tracker)

    def test_valid_calendar_passes(self, g):
        decision = {
            "short_symbol": "AAPL260515C00185000",
            "long_symbol": "AAPL260717C00185000",
            "limit_price": 1.70,
            "net_debit": 1.70,
        }
        context = {"fundamentals": {"days_to_earnings": 50}}
        account = {"buying_power": 50000.0}
        ok, msg = g.validate_calendar_spread_entry(decision, context, account)
        assert ok, f"Expected valid, got: {msg}"

    def test_mismatched_types_rejected(self, g):
        """One call, one put — invalid."""
        decision = {
            "short_symbol": "AAPL260515C00185000",  # call
            "long_symbol": "AAPL260717P00185000",   # put
            "limit_price": 1.70,
            "net_debit": 1.70,
        }
        context = {"fundamentals": {"days_to_earnings": 50}}
        account = {"buying_power": 50000.0}
        ok, msg = g.validate_calendar_spread_entry(decision, context, account)
        assert not ok
        assert "type" in msg.lower()

    def test_short_after_long_expiry_rejected(self, g):
        """Short expiration must be BEFORE long expiration."""
        decision = {
            "short_symbol": "AAPL260717C00185000",  # later date
            "long_symbol": "AAPL260515C00185000",   # earlier date
            "limit_price": 1.70,
            "net_debit": 1.70,
        }
        context = {"fundamentals": {"days_to_earnings": 50}}
        account = {"buying_power": 50000.0}
        ok, msg = g.validate_calendar_spread_entry(decision, context, account)
        assert not ok
        assert "expiration" in msg.lower() or "before" in msg.lower()

    def test_negative_limit_price_rejected(self, g):
        decision = {
            "short_symbol": "AAPL260515C00185000",
            "long_symbol": "AAPL260717C00185000",
            "limit_price": -1.70,  # Wrong sign for debit
            "net_debit": 1.70,
        }
        context = {"fundamentals": {"days_to_earnings": 50}}
        account = {"buying_power": 50000.0}
        ok, msg = g.validate_calendar_spread_entry(decision, context, account)
        assert not ok
        assert "positive" in msg.lower()

    def test_earnings_hard_block(self, g):
        decision = {
            "short_symbol": "AAPL260515C00185000",
            "long_symbol": "AAPL260717C00185000",
            "limit_price": 1.70,
            "net_debit": 1.70,
        }
        context = {"fundamentals": {"days_to_earnings": 10}}
        account = {"buying_power": 50000.0}
        ok, msg = g.validate_calendar_spread_entry(decision, context, account)
        assert not ok
        assert "earnings" in msg.lower()

    def test_debit_out_of_range_rejected(self, g):
        decision = {
            "short_symbol": "AAPL260515C00185000",
            "long_symbol": "AAPL260717C00185000",
            "limit_price": 3.00,
            "net_debit": 3.00,  # > $2.50
        }
        context = {"fundamentals": {"days_to_earnings": 50}}
        account = {"buying_power": 50000.0}
        ok, msg = g.validate_calendar_spread_entry(decision, context, account)
        assert not ok
        assert "$2.50" in msg or "range" in msg.lower()
