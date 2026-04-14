"""Tests for the Long Call Vertical strategy, state machine, and guardrails."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from strategies.guardrails import Guardrails
from strategies.long_call_vertical_strategy import (
    LongCallVerticalState,
    LongCallVerticalStrategy,
)


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    ctx = {
        "symbol": "AAPL",
        "confirmed_market_regime": "BULL",
        "iv_environment": "LOW",
        "iv_rank": 22,
        "macro": {"vix": 14},
        "fundamentals": {"days_to_earnings": 80},
        "technicals": {
            "current_price": 185.0,
            "above_sma_50": True,
            "above_sma_200": True,
            "rsi_14": 55,
        },
        "support_bounce_signal": {
            "cahold_detected": True,
            "low_day_date": "2026-04-05",
            "low_day_high": 182.50,
            "current_close": 185.0,
            "above_50sma": True,
        },
        "spread_candidates": {
            "long_call_vertical": {
                "best_candidate": {
                    "expiration": "2026-06-15",
                    "dte": 45,
                    "short_leg": {
                        "symbol": "AAPL260615C00190000",
                        "strike": 190,
                        "delta": 0.35,
                        "mid": 2.50,
                        "open_interest": 800,
                    },
                    "long_leg": {
                        "symbol": "AAPL260615C00185000",
                        "strike": 185,
                        "delta": 0.52,
                        "mid": 4.00,
                        "open_interest": 1000,
                    },
                    "net_debit": 1.50,
                    "max_loss": 150.0,
                    "max_gain": 350.0,
                    "break_even": 186.50,
                    "credit_to_width_ratio": 0.30,
                    "liquidity_ok": True,
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


def _entry_decision(**overrides):
    d = {
        "action": "OPEN",
        "long_call_symbol": "AAPL260615C00185000",
        "short_call_symbol": "AAPL260615C00190000",
        "expiration": "2026-06-15",
        "dte": 45,
        "long_call_strike": 185.0,
        "short_call_strike": 190.0,
        "net_debit": 1.50,
        "max_gain": 350.0,
        "break_even": 186.50,
        "limit_price": 1.50,  # POSITIVE for debit
        "price_target": 192.0,
        "reasoning": "CAHOLD bounce, low IV, BULL regime",
        "skip_reason": None,
    }
    d.update(overrides)
    return d


@pytest.fixture
def strategy(tmp_path):
    with patch.object(LongCallVerticalStrategy, "__init__", lambda self, *a, **k: None):
        s = LongCallVerticalStrategy.__new__(LongCallVerticalStrategy)
        s.broker = MagicMock()
        s.state_writer = MagicMock()
        s.spread_tracker = MagicMock()
        s.spread_tracker.get_open_spreads.return_value = []
        s.spread_tracker.get_active_spreads.return_value = []
        s.state = LongCallVerticalState.IDLE
        s.open_spread_id = None
        s.pending_order_id = None
        s.cb_status_at_entry = None
        s._state_path = tmp_path / "lcv_state.json"
        advisor = MagicMock()
        advisor.ask_spread.return_value = {"action": "SKIP", "reasoning": "default mock"}
        s._advisor = advisor
    return s


# ================================================================
# IDLE state -- entry
# ================================================================


class TestIdleEntry:
    def test_open_when_all_conditions_met(self, strategy):
        strategy._advisor.ask_spread.return_value = _entry_decision()

        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "OPEN"

    def test_skip_when_regime_is_neutral(self, strategy):
        result = strategy.run_cycle(_base_context(confirmed_market_regime="NEUTRAL"), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "NEUTRAL" in result["reasoning"]

    def test_skip_when_regime_is_bear(self, strategy):
        result = strategy.run_cycle(_base_context(confirmed_market_regime="BEAR"), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "BEAR" in result["reasoning"]

    def test_skip_when_iv_is_moderate(self, strategy):
        result = strategy.run_cycle(_base_context(iv_environment="MODERATE"), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "MODERATE" in result["reasoning"]

    def test_skip_when_iv_is_high(self, strategy):
        result = strategy.run_cycle(_base_context(iv_environment="HIGH"), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "HIGH" in result["reasoning"]

    def test_skip_when_ivr_too_high(self, strategy):
        result = strategy.run_cycle(_base_context(iv_rank=35), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "IV rank" in result["reasoning"]

    def test_skip_when_below_50sma(self, strategy):
        result = strategy.run_cycle(_base_context(
            technicals={"current_price": 185.0, "above_sma_50": False},
        ), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "50-day SMA" in result["reasoning"]

    def test_skip_when_no_cahold_signal(self, strategy):
        result = strategy.run_cycle(_base_context(
            support_bounce_signal={"cahold_detected": False},
        ), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "CAHOLD" in result["reasoning"]

    def test_skip_when_no_candidates(self, strategy):
        ctx = _base_context(
            spread_candidates={"long_call_vertical": {"best_candidate": None}},
        )
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "candidates" in result["reasoning"].lower()

    def test_skip_when_debit_too_high(self, strategy):
        ctx = _base_context()
        ctx["spread_candidates"]["long_call_vertical"]["best_candidate"]["net_debit"] = 2.50
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "debit" in result["reasoning"].lower()

    def test_skip_when_already_open(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [
            {"spread_id": "abc", "status": "open"},
        ]
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "open long call vertical" in result["reasoning"].lower()


# ================================================================
# OPEN state -- management
# ================================================================


class TestOpenManagement:
    def _setup_open(self, strategy, gain_pct=30, dte=35, debit=1.50, wing=5, original_dte=45):
        strategy.state = LongCallVerticalState.OPEN
        strategy.open_spread_id = "spread-lcv"

        max_gain_per_share = wing - debit
        current_value = debit + (max_gain_per_share * gain_pct / 100)
        future_exp = (datetime.now().date() + timedelta(days=dte)).isoformat()

        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-lcv",
            "strategy_type": "long_call_vertical",
            "underlying": "AAPL",
            "legs": [
                {"symbol": "AAPL260615C00185000", "side": "buy"},
                {"symbol": "AAPL260615C00190000", "side": "sell"},
            ],
            "entry_credit": debit,
            "expiration": future_exp,
            "max_gain": max_gain_per_share * 100,
            "original_dte": original_dte,
            "status": "open",
        }]

        strategy.broker.get_option_snapshots.return_value = {
            "AAPL260615C00185000": {"mid": current_value},
            "AAPL260615C00190000": {"mid": 0.0},
        }

    def test_close_at_75_pct_gain(self, strategy):
        self._setup_open(strategy, gain_pct=80)
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "CLOSE"
        assert "75%" in result["reasoning"]

    def test_close_when_dte_20(self, strategy):
        self._setup_open(strategy, gain_pct=20, dte=18)
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "CLOSE"
        assert "DTE" in result["reasoning"] or "theta" in result["reasoning"].lower()

    def test_close_on_stop_loss(self, strategy):
        """Spread value fallen 40% from debit = stop loss."""
        strategy.state = LongCallVerticalState.OPEN
        strategy.open_spread_id = "spread-lcv"

        future_exp = (datetime.now().date() + timedelta(days=35)).isoformat()
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-lcv",
            "strategy_type": "long_call_vertical",
            "underlying": "AAPL",
            "legs": [
                {"symbol": "AAPL260615C00185000", "side": "buy"},
                {"symbol": "AAPL260615C00190000", "side": "sell"},
            ],
            "entry_credit": 1.50,
            "expiration": future_exp,
            "max_gain": 350,
            "status": "open",
        }]

        # Current value = 0.80 < 1.50 * 0.60 = 0.90 --> stop loss
        strategy.broker.get_option_snapshots.return_value = {
            "AAPL260615C00185000": {"mid": 0.80},
            "AAPL260615C00190000": {"mid": 0.0},
        }

        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "CLOSE"
        assert "stop loss" in result["reasoning"].lower() or "fallen" in result["reasoning"].lower()

    def test_hold_when_position_ok(self, strategy):
        self._setup_open(strategy, gain_pct=40, dte=30)
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "HOLD"

    def test_resets_to_idle_if_spread_not_found(self, strategy):
        strategy.state = LongCallVerticalState.OPEN
        strategy.open_spread_id = "missing"
        strategy.spread_tracker.get_open_spreads.return_value = []
        s.spread_tracker.get_active_spreads.return_value = []

        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert strategy.state == LongCallVerticalState.IDLE


# ================================================================
# Guardrails
# ================================================================


class TestLongCallVerticalGuardrails:
    @pytest.fixture
    def guardrails(self):
        return Guardrails()

    def test_valid_entry_passes(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(), _base_context(), {"buying_power": "100000"},
        )
        assert ok is True

    def test_rejects_negative_limit_price(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(limit_price=-1.50), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "positive" in reason.lower()

    def test_rejects_zero_limit_price(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(limit_price=0), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False

    def test_rejects_debit_too_low(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(net_debit=0.10), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "$0.20" in reason

    def test_rejects_debit_too_high(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(net_debit=2.50), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "$2.00" in reason

    def test_rejects_max_risk_over_1pct(self, guardrails):
        # debit=1.50 * 100 = $150, 1% of $10,000 = $100
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(net_debit=1.50), _base_context(), {"buying_power": "10000"},
        )
        assert ok is False
        assert "1%" in reason

    def test_rejects_non_bull_regime(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(), _base_context(confirmed_market_regime="NEUTRAL"),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "BULL" in reason

    def test_rejects_non_low_iv(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(), _base_context(iv_environment="HIGH"),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "LOW" in reason

    def test_rejects_earnings_within_dte(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(dte=45),
            _base_context(fundamentals={"days_to_earnings": 40}),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "Earnings" in reason

    def test_rejects_dte_outside_range(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(dte=20), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "DTE" in reason

    def test_rejects_put_symbol(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(long_call_symbol="AAPL260615P00185000"),
            _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "occ call" in reason.lower()

    def test_rejects_duplicate(self, guardrails):
        ok, reason = guardrails.validate_long_call_vertical_entry(
            _entry_decision(), _base_context(), {"buying_power": "100000"},
            open_spreads=[{"underlying": "AAPL", "status": "open"}],
        )
        assert ok is False
        assert "open long call vertical" in reason.lower()


# ================================================================
# Execution
# ================================================================


class TestExecution:
    def test_execute_entry_places_2_leg_order(self, strategy):
        strategy.broker.place_mleg_order.return_value = {"id": "order-lcv"}
        strategy.spread_tracker.register_spread.return_value = "spread-new"

        result = strategy.execute_entry(_entry_decision(underlying="AAPL"))
        assert result is True
        assert strategy.state == LongCallVerticalState.PENDING_OPEN
        assert strategy.pending_order_id == "order-lcv"

        call_args = strategy.broker.place_mleg_order.call_args
        legs = call_args[1]["legs"] if "legs" in call_args[1] else call_args[0][0]
        assert len(legs) == 2
        # First leg is buy (long), second is sell (short)
        assert legs[0]["side"] == "buy"
        assert legs[0]["position_intent"] == "buy_to_open"
        assert legs[1]["side"] == "sell"

    def test_limit_price_is_positive(self, strategy):
        strategy.broker.place_mleg_order.return_value = {"id": "order-lcv"}
        strategy.spread_tracker.register_spread.return_value = "spread-new"

        strategy.execute_entry(_entry_decision(limit_price=1.50, underlying="AAPL"))

        call_args = strategy.broker.place_mleg_order.call_args
        assert call_args[1]["limit_price"] == 1.50  # positive for debit

    def test_execute_entry_failure(self, strategy):
        strategy.broker.place_mleg_order.side_effect = Exception("fail")
        result = strategy.execute_entry(_entry_decision(underlying="AAPL"))
        assert result is False
        assert strategy.state == LongCallVerticalState.IDLE

    def test_execute_exit(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-lcv",
            "legs": [
                {"symbol": "A", "side": "buy", "position_intent": "buy_to_open"},
                {"symbol": "B", "side": "sell", "position_intent": "sell_to_open"},
            ],
            "status": "open",
        }]
        strategy.broker.close_mleg_position.return_value = {"id": "close"}

        result = strategy.execute_exit("spread-lcv", limit_price=3.80)
        assert result is True
        assert strategy.state == LongCallVerticalState.IDLE


# ================================================================
# CAHOLD detection
# ================================================================


class TestDetectSupportBounce:
    def test_cahold_detected(self):
        """Test the detect_support_bounce static method with mock data."""
        import pandas as pd
        from data.context_builder import ContextBuilder

        # Create mock historical data
        dates = pd.date_range("2026-03-01", periods=60, freq="B")
        data = {
            "Open": [180 + i * 0.1 for i in range(60)],
            "High": [182 + i * 0.1 for i in range(60)],
            "Low": [178 + i * 0.1 for i in range(60)],
            "Close": [181 + i * 0.1 for i in range(60)],
            "Volume": [1000000] * 60,
        }
        hist = pd.DataFrame(data, index=dates)
        # Make one day a clear low
        hist.iloc[55, hist.columns.get_loc("Low")] = 170.0
        hist.iloc[55, hist.columns.get_loc("High")] = 178.0
        # Most recent close is above that day's high
        hist.iloc[-1, hist.columns.get_loc("Close")] = 187.0

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = hist
            mock_ticker_cls.return_value = mock_ticker

            result = ContextBuilder.detect_support_bounce("AAPL")

        assert result["cahold_detected"] is True
        assert result["current_close"] == 187.0
        assert result["low_day_high"] == 178.0

    def test_no_cahold_when_close_below_low_day_high(self):
        import pandas as pd
        from data.context_builder import ContextBuilder

        dates = pd.date_range("2026-03-01", periods=60, freq="B")
        data = {
            "Open": [180] * 60,
            "High": [185] * 60,
            "Low": [175] * 60,
            "Close": [180] * 60,
            "Volume": [1000000] * 60,
        }
        hist = pd.DataFrame(data, index=dates)
        # Low day high is 185, but current close is only 180
        hist.iloc[55, hist.columns.get_loc("Low")] = 170.0
        hist.iloc[55, hist.columns.get_loc("High")] = 185.0
        hist.iloc[-1, hist.columns.get_loc("Close")] = 180.0

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.history.return_value = hist
            mock_ticker_cls.return_value = mock_ticker

            result = ContextBuilder.detect_support_bounce("AAPL")

        assert result["cahold_detected"] is False


# ================================================================
# State persistence
# ================================================================


class TestStatePersistence:
    def test_roundtrip(self, strategy, tmp_path):
        strategy._state_path = tmp_path / "lcv_state.json"
        strategy.state = LongCallVerticalState.OPEN
        strategy.open_spread_id = "persist-lcv"
        strategy._save_state()

        data = json.loads(strategy._state_path.read_text())
        assert data["state"] == "OPEN"
        assert data["open_spread_id"] == "persist-lcv"


# ================================================================
# IV forecast checks — reversed logic for buyer strategy (Prompt 3)
# ================================================================


class TestIVForecastForBuyer:
    def test_overvalued_iv_blocks_entry(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "OVERVALUED", "iv_hv_ratio": 1.1}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "OVERVALUED" in reason
        assert score == 0.0

    def test_undervalued_iv_boosts_score(self, strategy):
        ctx_uv = _base_context()
        ctx_uv["volatility"] = {"iv_overvalued_label": "UNDERVALUED", "iv_hv_ratio": 0.8}
        ctx_base = _base_context()
        _, score_base = strategy.pre_check_entry(ctx_base)
        _, score_uv = strategy.pre_check_entry(ctx_uv)
        assert score_uv == pytest.approx(score_base * 1.20, rel=0.01)

    def test_fair_iv_passes_without_penalty(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "FAIR", "iv_hv_ratio": 1.0}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is None
