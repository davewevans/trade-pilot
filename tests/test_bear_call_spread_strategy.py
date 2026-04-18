"""Tests for the Bear Call Spread strategy, state machine, and guardrails."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from strategies.guardrails import Guardrails
from strategies.bear_call_spread_strategy import BearCallSpreadState, BearCallSpreadStrategy


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    ctx = {
        "symbol": "SPY",
        "confirmed_market_regime": "BEAR",
        "iv_environment": "HIGH",
        "iv_rank": 55,
        "macro": {"vix": 25},
        "fundamentals": {
            "days_to_earnings": 50,
            "days_to_ex_dividend": 60,
            "next_ex_dividend_date": None,
        },
        "technicals": {
            "current_price": 540.0,
            "above_sma_50": False,
            "above_sma_200": True,
            "rsi_14": 65,
        },
        "spread_candidates": {
            "bear_call_spread": {
                "best_candidate": {
                    "expiration": "2026-05-15",
                    "dte": 30,
                    "short_leg": {
                        "symbol": "SPY260515C00550000",
                        "strike": 550,
                        "delta": 0.25,
                        "bid": 2.80,
                        "ask": 3.00,
                        "mid": 2.90,
                        "open_interest": 600,
                        "bid_ask_spread_pct": 6.9,
                    },
                    "long_leg": {
                        "symbol": "SPY260515C00555000",
                        "strike": 555,
                        "delta": 0.18,
                        "bid": 1.80,
                        "ask": 2.00,
                        "mid": 1.90,
                        "open_interest": 500,
                    },
                    "net_credit": 1.00,
                    "max_loss": 400.0,
                    "max_gain": 100.0,
                    "break_even": 551.0,
                    "credit_to_width_ratio": 0.20,
                    "liquidity_ok": True,
                    "spread_yield": 0.00185,  # 1.00 / 540.0
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


def _entry_decision(**overrides):
    d = {
        "action": "OPEN",
        "short_call_symbol": "SPY260515C00550000",
        "long_call_symbol": "SPY260515C00555000",
        "expiration": "2026-05-15",
        "dte": 30,
        "short_call_strike": 550.0,
        "long_call_strike": 555.0,
        "net_credit": 1.00,
        "max_loss": 400.0,
        "limit_price": -1.00,
        "bearish_rationale": "Below 50-SMA, RSI overbought",
        "reasoning": "Good bearish setup",
        "skip_reason": None,
    }
    d.update(overrides)
    return d


@pytest.fixture
def strategy(tmp_path):
    with patch.object(BearCallSpreadStrategy, "__init__", lambda self, *a, **k: None):
        s = BearCallSpreadStrategy.__new__(BearCallSpreadStrategy)
        s.broker = MagicMock()
        s.state_writer = MagicMock()
        s.spread_tracker = MagicMock()
        s.spread_tracker.get_open_spreads.return_value = []
        s.spread_tracker.get_active_spreads.return_value = []
        s.state = BearCallSpreadState.IDLE
        s.open_spread_id = None
        s.pending_order_id = None
        s._state_path = tmp_path / "bcs_state.json"
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

    def test_skip_when_regime_is_bull(self, strategy):
        result = strategy.run_cycle(_base_context(confirmed_market_regime="BULL"), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "BULL" in result["reasoning"]

    def test_allows_neutral_regime_with_bearish_setup(self, strategy):
        strategy._advisor.ask_spread.return_value = _entry_decision()

        result = strategy.run_cycle(_base_context(confirmed_market_regime="NEUTRAL"), advisor=strategy._advisor)
        assert result["action"] == "OPEN"

    def test_skip_neutral_without_bearish_setup(self, strategy):
        """NEUTRAL regime + above 50-SMA + RSI < 60 = no bearish setup."""
        result = strategy.run_cycle(_base_context(
            confirmed_market_regime="NEUTRAL",
            technicals={
                "current_price": 540.0,
                "above_sma_50": True,
                "rsi_14": 50,
            },
        ), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "bearish" in result["reasoning"].lower()

    def test_skip_when_ivr_too_low(self, strategy):
        result = strategy.run_cycle(_base_context(iv_rank=30), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "IV rank" in result["reasoning"]

    def test_skip_when_earnings_too_close(self, strategy):
        result = strategy.run_cycle(_base_context(
            fundamentals={"days_to_earnings": 20, "days_to_ex_dividend": 60},
        ), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "Earnings" in result["reasoning"]

    def test_skip_when_ex_dividend_within_dte(self, strategy):
        result = strategy.run_cycle(_base_context(
            fundamentals={"days_to_earnings": 50, "days_to_ex_dividend": 25},
        ), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "Ex-dividend" in result["reasoning"]

    def test_skip_when_no_candidates(self, strategy):
        ctx = _base_context(spread_candidates={"bear_call_spread": {"best_candidate": None}})
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)
        assert result["action"] == "SKIP"

    def test_skip_when_credit_too_low(self, strategy):
        ctx = _base_context()
        ctx["spread_candidates"]["bear_call_spread"]["best_candidate"]["net_credit"] = 0.30
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)
        assert result["action"] == "SKIP"

    def test_skip_when_already_open(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [
            {"spread_id": "abc", "status": "open"},
        ]
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "SKIP"
        assert "open bear call spread" in result["reasoning"].lower()

    def test_bear_regime_skips_technical_check(self, strategy):
        """BEAR regime doesn't require bearish technicals."""
        strategy._advisor.ask_spread.return_value = _entry_decision()

        result = strategy.run_cycle(_base_context(
            confirmed_market_regime="BEAR",
            technicals={
                "current_price": 540.0,
                "above_sma_50": True,
                "rsi_14": 50,
            },
        ), advisor=strategy._advisor)
        assert result["action"] == "OPEN"


# ================================================================
# OPEN state -- management
# ================================================================


class TestOpenManagement:
    def _setup_open(self, strategy, pnl_target_pct=30, dte=25, entry_credit=1.00):
        strategy.state = BearCallSpreadState.OPEN
        strategy.open_spread_id = "spread-bcs"

        current_value = entry_credit * (1 - pnl_target_pct / 100)
        future_exp = (datetime.now().date() + timedelta(days=dte)).isoformat()

        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-bcs",
            "strategy_type": "bear_call_spread",
            "underlying": "SPY",
            "legs": [
                {"symbol": "SPY260515C00550000", "side": "sell"},
                {"symbol": "SPY260515C00555000", "side": "buy"},
            ],
            "entry_credit": entry_credit,
            "expiration": future_exp,
            "status": "open",
        }]

        strategy.broker.get_option_snapshots.return_value = {
            "SPY260515C00550000": {"mid": current_value},
            "SPY260515C00555000": {"mid": 0.0},
        }

    def test_close_at_50_pct_profit(self, strategy):
        self._setup_open(strategy, pnl_target_pct=55)
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "CLOSE"
        assert "50%" in result["reasoning"]

    def test_close_when_dte_10(self, strategy):
        self._setup_open(strategy, pnl_target_pct=20, dte=8)
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "CLOSE"
        assert "DTE" in result["reasoning"]

    def test_close_when_breached_and_dte_lt_15(self, strategy):
        self._setup_open(strategy, pnl_target_pct=10, dte=12)
        ctx = _base_context(technicals={
            "current_price": 555.0,
            "above_sma_50": False,
            "rsi_14": 65,
        })
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)
        assert result["action"] == "CLOSE"
        assert "breached" in result["reasoning"].lower()

    def test_close_immediately_on_ex_div_risk(self, strategy):
        """ITM short call + ex-dividend approaching = immediate close."""
        self._setup_open(strategy, pnl_target_pct=10, dte=25)
        ctx = _base_context(
            technicals={"current_price": 555.0, "above_sma_50": False, "rsi_14": 65},
            fundamentals={
                "days_to_earnings": 50,
                "days_to_ex_dividend": 10,  # ex-div within DTE
            },
        )
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)
        assert result["action"] == "CLOSE"
        assert "ex-dividend" in result["reasoning"].lower() or "assignment" in result["reasoning"].lower()

    def test_hold_when_position_ok(self, strategy):
        self._setup_open(strategy, pnl_target_pct=30, dte=25)
        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert result["action"] == "HOLD"

    def test_resets_to_idle_if_spread_not_found(self, strategy):
        strategy.state = BearCallSpreadState.OPEN
        strategy.open_spread_id = "missing"
        strategy.spread_tracker.get_open_spreads.return_value = []
        s.spread_tracker.get_active_spreads.return_value = []

        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)
        assert strategy.state == BearCallSpreadState.IDLE


# ================================================================
# Guardrails
# ================================================================


class TestBearCallSpreadGuardrails:
    @pytest.fixture
    def guardrails(self):
        return Guardrails()

    def test_valid_entry_passes(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(), _base_context(), {"buying_power": "100000"},
        )
        assert ok is True

    def test_rejects_positive_limit_price(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(limit_price=1.00), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "negative" in reason.lower()

    def test_rejects_low_credit(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(net_credit=0.20), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False

    def test_rejects_max_loss_over_2pct(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(max_loss=2500), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "2%" in reason

    def test_rejects_dte_outside_range(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(dte=15), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False

    def test_rejects_put_symbol(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(short_call_symbol="SPY260515P00550000"),
            _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "occ call" in reason.lower()

    def test_rejects_earnings_within_21d(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(),
            _base_context(fundamentals={"days_to_earnings": 18, "days_to_ex_dividend": 60}),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "Earnings" in reason

    def test_rejects_ex_div_within_dte(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(dte=30),
            _base_context(fundamentals={"days_to_earnings": 50, "days_to_ex_dividend": 20}),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "ex-dividend" in reason.lower()

    def test_rejects_duplicate(self, guardrails):
        ok, reason = guardrails.validate_bear_call_spread_entry(
            _entry_decision(), _base_context(), {"buying_power": "100000"},
            open_spreads=[{"underlying": "SPY", "status": "open"}],
        )
        assert ok is False
        assert "open bear call spread" in reason.lower()


# ================================================================
# Execution
# ================================================================


class TestExecution:
    def test_execute_entry_places_2_leg_order(self, strategy):
        strategy.broker.place_mleg_order.return_value = {"id": "order-bcs"}
        strategy.spread_tracker.register_spread.return_value = "spread-new"

        result = strategy.execute_entry(_entry_decision(underlying="SPY"))
        assert result is True
        assert strategy.state == BearCallSpreadState.OPEN

        call_args = strategy.broker.place_mleg_order.call_args
        legs = call_args[1]["legs"] if "legs" in call_args[1] else call_args[0][0]
        assert len(legs) == 2
        assert legs[0]["side"] == "sell"
        assert legs[0]["position_intent"] == "sell_to_open"
        assert legs[1]["side"] == "buy"

    def test_execute_entry_failure(self, strategy):
        strategy.broker.place_mleg_order.side_effect = Exception("fail")
        result = strategy.execute_entry(_entry_decision(underlying="SPY"))
        assert result is False
        assert strategy.state == BearCallSpreadState.IDLE

    def test_execute_exit(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-bcs",
            "legs": [
                {"symbol": "A", "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": "B", "side": "buy", "position_intent": "buy_to_open"},
            ],
            "status": "open",
        }]
        strategy.broker.close_mleg_position.return_value = {"id": "close"}

        result = strategy.execute_exit("spread-bcs", limit_price=0.25)
        assert result is True
        assert strategy.state == BearCallSpreadState.IDLE


# ================================================================
# State persistence
# ================================================================


class TestStatePersistence:
    def test_roundtrip(self, strategy, tmp_path):
        strategy._state_path = tmp_path / "bcs_state.json"
        strategy.state = BearCallSpreadState.OPEN
        strategy.open_spread_id = "persist-bcs"
        strategy._save_state()

        data = json.loads(strategy._state_path.read_text())
        assert data["state"] == "OPEN"
        assert data["open_spread_id"] == "persist-bcs"


# ================================================================
# IV forecast and spread yield checks (Prompt 3)
# ================================================================


class TestIVForecastAndSpreadYield:
    def test_undervalued_iv_blocks_entry(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "UNDERVALUED", "iv_hv_ratio": 1.1}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "UNDERVALUED" in reason
        assert score == 0.0

    def test_overvalued_iv_boosts_score(self, strategy):
        ctx_ov = _base_context()
        ctx_ov["volatility"] = {"iv_overvalued_label": "OVERVALUED", "iv_hv_ratio": 1.1}
        ctx_base = _base_context()
        _, score_base = strategy.pre_check_entry(ctx_base)
        _, score_ov = strategy.pre_check_entry(ctx_ov)
        assert score_ov == pytest.approx(score_base * 1.15, rel=0.01)

    def test_low_spread_yield_blocks_entry(self, strategy):
        ctx = _base_context()
        ctx["spread_candidates"]["bear_call_spread"]["best_candidate"]["spread_yield"] = 0.0004
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert score == 0.0
