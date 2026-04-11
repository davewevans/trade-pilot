"""Tests for the Bull Put Spread strategy, state machine, and guardrails."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from strategies.guardrails import Guardrails
from strategies.bull_put_spread_strategy import BullPutSpreadState, BullPutSpreadStrategy


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    ctx = {
        "symbol": "SPY",
        "confirmed_market_regime": "NEUTRAL",
        "iv_environment": "MODERATE",
        "iv_rank": 45,
        "macro": {"vix": 20},
        "fundamentals": {"days_to_earnings": 50},
        "technicals": {"current_price": 540.0, "above_sma_50": True},
        "spread_candidates": {
            "bull_put_spread": {
                "best_candidate": {
                    "expiration": "2026-05-15",
                    "dte": 30,
                    "short_leg": {
                        "symbol": "SPY260515P00530000",
                        "strike": 530,
                        "delta": -0.25,
                        "bid": 2.90,
                        "ask": 3.10,
                        "mid": 3.00,
                        "open_interest": 500,
                        "bid_ask_spread_pct": 6.67,
                    },
                    "long_leg": {
                        "symbol": "SPY260515P00525000",
                        "strike": 525,
                        "delta": -0.18,
                        "bid": 1.90,
                        "ask": 2.10,
                        "mid": 2.00,
                        "open_interest": 400,
                    },
                    "net_credit": 1.00,
                    "max_loss": 400.0,
                    "max_gain": 100.0,
                    "break_even": 529.0,
                    "credit_to_width_ratio": 0.20,
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
        "short_put_symbol": "SPY260515P00530000",
        "long_put_symbol": "SPY260515P00525000",
        "expiration": "2026-05-15",
        "dte": 30,
        "short_put_strike": 530.0,
        "long_put_strike": 525.0,
        "net_credit": 1.00,
        "max_loss": 400.0,
        "limit_price": -1.00,
        "reasoning": "Good setup",
        "skip_reason": None,
    }
    d.update(overrides)
    return d


@pytest.fixture
def strategy(tmp_path):
    with patch.object(BullPutSpreadStrategy, "__init__", lambda self, *a, **k: None):
        s = BullPutSpreadStrategy.__new__(BullPutSpreadStrategy)
        s.broker = MagicMock()
        s.state_writer = MagicMock()
        s.spread_tracker = MagicMock()
        s.spread_tracker.get_open_spreads.return_value = []
        s.state = BullPutSpreadState.IDLE
        s.open_spread_id = None
        s._client = MagicMock()
        s._model = "test"
        s._system_prompt = "system"
        s._idle_prompt = "idle"
        s._open_prompt = "open"
        s._state_path = tmp_path / "bps_state.json"
    return s


# ================================================================
# IDLE state -- entry
# ================================================================


class TestIdleEntry:
    def test_open_when_all_conditions_met(self, strategy):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=json.dumps(_entry_decision()))]
        strategy._client.messages.create.return_value = mock_resp

        result = strategy.run_cycle(_base_context())
        assert result["action"] == "OPEN"

    def test_skip_when_regime_is_bear(self, strategy):
        result = strategy.run_cycle(_base_context(confirmed_market_regime="BEAR"))
        assert result["action"] == "SKIP"
        assert "BEAR" in result["reasoning"]

    def test_skip_when_regime_is_crash(self, strategy):
        result = strategy.run_cycle(_base_context(confirmed_market_regime="CRASH"))
        assert result["action"] == "SKIP"

    def test_allows_bull_regime(self, strategy):
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=json.dumps(_entry_decision()))]
        strategy._client.messages.create.return_value = mock_resp

        result = strategy.run_cycle(_base_context(confirmed_market_regime="BULL"))
        assert result["action"] == "OPEN"

    def test_skip_when_ivr_too_low(self, strategy):
        result = strategy.run_cycle(_base_context(iv_rank=25))
        assert result["action"] == "SKIP"
        assert "IV rank" in result["reasoning"]

    def test_skip_when_below_50sma(self, strategy):
        result = strategy.run_cycle(_base_context(
            technicals={"current_price": 540.0, "above_sma_50": False},
        ))
        assert result["action"] == "SKIP"
        assert "50-day SMA" in result["reasoning"]

    def test_skip_when_earnings_too_close(self, strategy):
        result = strategy.run_cycle(_base_context(
            fundamentals={"days_to_earnings": 20},
        ))
        assert result["action"] == "SKIP"
        assert "Earnings" in result["reasoning"]

    def test_skip_when_no_candidates(self, strategy):
        ctx = _base_context(spread_candidates={"bull_put_spread": {"best_candidate": None}})
        result = strategy.run_cycle(ctx)
        assert result["action"] == "SKIP"
        assert "candidates" in result["reasoning"].lower()

    def test_skip_when_credit_too_low(self, strategy):
        ctx = _base_context()
        ctx["spread_candidates"]["bull_put_spread"]["best_candidate"]["net_credit"] = 0.30
        result = strategy.run_cycle(ctx)
        assert result["action"] == "SKIP"
        assert "credit" in result["reasoning"].lower()

    def test_skip_when_ratio_too_low(self, strategy):
        ctx = _base_context()
        ctx["spread_candidates"]["bull_put_spread"]["best_candidate"]["credit_to_width_ratio"] = 0.10
        result = strategy.run_cycle(ctx)
        assert result["action"] == "SKIP"
        assert "ratio" in result["reasoning"].lower()

    def test_skip_when_already_open_on_underlying(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [
            {"spread_id": "abc", "status": "open"},
        ]
        result = strategy.run_cycle(_base_context())
        assert result["action"] == "SKIP"
        assert "open bull put spread" in result["reasoning"].lower()


# ================================================================
# OPEN state -- management
# ================================================================


class TestOpenManagement:
    def _setup_open(self, strategy, pnl_target_pct=30, dte=25, entry_credit=1.00):
        strategy.state = BullPutSpreadState.OPEN
        strategy.open_spread_id = "spread-456"

        current_value = entry_credit * (1 - pnl_target_pct / 100)
        future_exp = (datetime.now().date() + timedelta(days=dte)).isoformat()

        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-456",
            "strategy_type": "bull_put_spread",
            "underlying": "SPY",
            "legs": [
                {"symbol": "SPY260515P00530000", "side": "sell"},
                {"symbol": "SPY260515P00525000", "side": "buy"},
            ],
            "entry_credit": entry_credit,
            "expiration": future_exp,
            "status": "open",
        }]

        strategy.broker.get_option_snapshots.return_value = {
            "SPY260515P00530000": {"mid": current_value},
            "SPY260515P00525000": {"mid": 0.0},
        }

    def test_close_at_50_pct_profit(self, strategy):
        self._setup_open(strategy, pnl_target_pct=55)
        result = strategy.run_cycle(_base_context())
        assert result["action"] == "CLOSE"
        assert "50%" in result["reasoning"]

    def test_close_when_dte_10(self, strategy):
        self._setup_open(strategy, pnl_target_pct=20, dte=8)
        result = strategy.run_cycle(_base_context())
        assert result["action"] == "CLOSE"
        assert "DTE" in result["reasoning"]

    def test_close_when_breached_and_dte_lt_15(self, strategy):
        self._setup_open(strategy, pnl_target_pct=10, dte=12)
        # Underlying below short put strike
        ctx = _base_context(technicals={"current_price": 525.0, "above_sma_50": True})
        result = strategy.run_cycle(ctx)
        assert result["action"] == "CLOSE"
        assert "breached" in result["reasoning"].lower()

    def test_hold_when_position_ok(self, strategy):
        self._setup_open(strategy, pnl_target_pct=30, dte=25)
        result = strategy.run_cycle(_base_context())
        assert result["action"] == "HOLD"

    def test_hold_when_breached_but_dte_gte_15(self, strategy):
        self._setup_open(strategy, pnl_target_pct=10, dte=20)
        ctx = _base_context(technicals={"current_price": 525.0, "above_sma_50": True})
        result = strategy.run_cycle(ctx)
        assert result["action"] == "HOLD"

    def test_resets_to_idle_if_spread_not_found(self, strategy):
        strategy.state = BullPutSpreadState.OPEN
        strategy.open_spread_id = "missing-id"
        strategy.spread_tracker.get_open_spreads.return_value = []

        result = strategy.run_cycle(_base_context())
        assert strategy.state == BullPutSpreadState.IDLE
        assert strategy.open_spread_id is None


# ================================================================
# Guardrails
# ================================================================


class TestBullPutSpreadGuardrails:
    @pytest.fixture
    def guardrails(self):
        return Guardrails()

    def test_valid_entry_passes(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(), _base_context(), {"buying_power": "100000"},
        )
        assert ok is True

    def test_rejects_positive_limit_price(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(limit_price=1.00), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "negative" in reason.lower()

    def test_rejects_low_net_credit(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(net_credit=0.20), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "$0.25" in reason

    def test_rejects_max_loss_over_2pct(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(max_loss=2500), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "2%" in reason

    def test_rejects_dte_outside_range(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(dte=15), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "DTE" in reason

    def test_rejects_invalid_occ_symbol(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(short_put_symbol="BAD"), _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "OCC" in reason

    def test_rejects_call_symbol_for_put_spread(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(short_put_symbol="SPY260515C00530000"),
            _base_context(), {"buying_power": "100000"},
        )
        assert ok is False
        assert "occ put" in reason.lower()

    def test_rejects_earnings_within_21d(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(),
            _base_context(fundamentals={"days_to_earnings": 18}),
            {"buying_power": "100000"},
        )
        assert ok is False
        assert "Earnings" in reason

    def test_rejects_duplicate_spread(self, guardrails):
        ok, reason = guardrails.validate_bull_put_spread_entry(
            _entry_decision(), _base_context(), {"buying_power": "100000"},
            open_spreads=[{"underlying": "SPY", "status": "open"}],
        )
        assert ok is False
        assert "open bull put spread" in reason.lower()


# ================================================================
# Execution
# ================================================================


class TestExecution:
    def test_execute_entry_places_2_leg_order(self, strategy):
        strategy.broker.place_mleg_order.return_value = {"id": "order-789"}
        strategy.spread_tracker.register_spread.return_value = "spread-new"

        result = strategy.execute_entry(_entry_decision(underlying="SPY"))

        assert result is True
        assert strategy.state == BullPutSpreadState.OPEN
        assert strategy.open_spread_id == "spread-new"

        call_args = strategy.broker.place_mleg_order.call_args
        legs = call_args[1]["legs"] if "legs" in call_args[1] else call_args[0][0]
        assert len(legs) == 2
        assert legs[0]["side"] == "sell"
        assert legs[1]["side"] == "buy"

    def test_execute_entry_failure_stays_idle(self, strategy):
        strategy.broker.place_mleg_order.side_effect = Exception("API error")

        result = strategy.execute_entry(_entry_decision(underlying="SPY"))
        assert result is False
        assert strategy.state == BullPutSpreadState.IDLE

    def test_execute_exit_closes_spread(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-456",
            "legs": [
                {"symbol": "A", "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": "B", "side": "buy", "position_intent": "buy_to_open"},
            ],
            "status": "open",
        }]
        strategy.broker.close_mleg_position.return_value = {"id": "close-xyz"}

        result = strategy.execute_exit("spread-456", limit_price=0.25)

        assert result is True
        assert strategy.state == BullPutSpreadState.IDLE
        strategy.spread_tracker.close_spread.assert_called_once_with(
            "spread-456", exit_credit=0.25,
        )


# ================================================================
# State persistence
# ================================================================


class TestStatePersistence:
    def test_roundtrip(self, strategy, tmp_path):
        strategy._state_path = tmp_path / "bps_state.json"
        strategy.state = BullPutSpreadState.OPEN
        strategy.open_spread_id = "persist-id"
        strategy._save_state()

        data = json.loads(strategy._state_path.read_text())
        assert data["state"] == "OPEN"
        assert data["open_spread_id"] == "persist-id"
