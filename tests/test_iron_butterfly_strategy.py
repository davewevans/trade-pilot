"""Tests for the Iron Butterfly strategy, state machine, and guardrails."""

import json
from unittest.mock import MagicMock, patch

import pytest

from strategies.iron_butterfly_strategy import IronButterflyState, IronButterflyStrategy


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    """Return a valid context dict for iron butterfly entry."""
    ctx = {
        "confirmed_market_regime": "NEUTRAL",
        "iv_environment": "HIGH",
        "iv_rank": 55,
        "macro": {"vix": 22},
        "fundamentals": {"days_to_earnings": 60},
        "technicals": {"current_price": 540.0, "above_sma_50": True},
        "spread_candidates": {
            "iron_butterfly": {
                "iron_butterfly_legs": {
                    "put_wing": {"symbol": "SPY250502P00535000", "strike": 535},
                    "put_short": {"symbol": "SPY250502P00540000", "strike": 540, "delta": -0.50},
                    "call_short": {"symbol": "SPY250502C00540000", "strike": 540, "delta": 0.50},
                    "call_wing": {"symbol": "SPY250502C00545000", "strike": 545},
                    "total_credit": 3.50,
                    "center_strike": 540.0,
                    "total_max_loss": 150.0,
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


def _entry_decision(**overrides):
    """Return a valid iron butterfly entry decision dict."""
    d = {
        "action": "OPEN",
        "put_long_symbol": "SPY250502P00535000",
        "put_short_symbol": "SPY250502P00540000",
        "call_short_symbol": "SPY250502C00540000",
        "call_long_symbol": "SPY250502C00545000",
        "expiration": "2025-05-02",
        "dte": 30,
        "total_credit": 3.50,
        "max_loss": 150.0,
        "limit_price": -3.50,
        "center_strike": 540.0,
        "reasoning": "Good ATM butterfly setup",
        "skip_reason": None,
    }
    d.update(overrides)
    return d


@pytest.fixture
def strategy(tmp_path):
    """IronButterflyStrategy with mocked broker, writer, and tracker."""
    with patch("strategies.iron_butterfly_strategy.settings") as mock_settings:
        mock_settings.SNAPSHOTS_DIR = tmp_path

        broker = MagicMock()
        sw = MagicMock()
        tracker = MagicMock()
        tracker.get_open_spreads.return_value = []
        tracker.get_active_spreads.return_value = []

        with patch.object(IronButterflyStrategy, "__init__", lambda self, *a, **k: None):
            s = IronButterflyStrategy.__new__(IronButterflyStrategy)
            s.broker = broker
            s.state_writer = sw
            s.spread_tracker = tracker
            s.state = IronButterflyState.IDLE
            s.open_spread_id = None
            s.pending_order_id = None
            s._state_path = tmp_path / "iron_butterfly_state.json"
            advisor = MagicMock()
            advisor.ask_spread.return_value = {"action": "SKIP", "reasoning": "default mock"}
            s._advisor = advisor

        return s


# ================================================================
# IDLE state — entry evaluation
# ================================================================


class TestIdleEntry:
    def test_open_when_all_conditions_met(self, strategy):
        strategy._advisor.ask_spread.return_value = _entry_decision()

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "OPEN"
        assert result["total_credit"] == 3.50

    def test_skip_when_ivr_too_low(self, strategy):
        ctx = _base_context(iv_rank=30)
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "IV rank" in result["reasoning"]

    def test_skip_when_earnings_too_close(self, strategy):
        ctx = _base_context(fundamentals={"days_to_earnings": 25})
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "Earnings" in result["reasoning"]

    def test_skip_when_regime_is_bear(self, strategy):
        ctx = _base_context(confirmed_market_regime="BEAR")
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "BEAR" in result["reasoning"]

    def test_skip_when_vix_too_low(self, strategy):
        ctx = _base_context(macro={"vix": 12})
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "VIX" in result["reasoning"]

    def test_skip_when_vix_too_high(self, strategy):
        ctx = _base_context(macro={"vix": 40})
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "VIX" in result["reasoning"]

    def test_skip_when_no_candidates(self, strategy):
        ctx = _base_context(
            spread_candidates={"iron_butterfly": {"iron_butterfly_legs": None}},
        )
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "candidates" in result["reasoning"].lower()

    def test_skip_when_already_open(self, strategy):
        strategy.spread_tracker.get_active_spreads.return_value = [
            {"spread_id": "abc", "status": "open"},
        ]
        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "iron butterfly" in result["reasoning"].lower()

    def test_skip_when_iv_undervalued(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "UNDERVALUED"}
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "UNDERVALUED" in result["reasoning"]

    def test_skip_when_backwardation(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"contango_label": "BACKWARDATION"}
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "BACKWARDATION" in result["reasoning"]


# ================================================================
# OPEN state — management evaluation
# ================================================================


class TestOpenManagement:
    def _setup_open(self, strategy, pnl_target_pct=30, dte=25,
                    underlying_price=540.0, entry_credit=3.50):
        """Put strategy in OPEN state with a mock spread."""
        from datetime import datetime, timedelta

        strategy.state = IronButterflyState.OPEN
        strategy.open_spread_id = "spread-123"

        # current_value represents what the position costs to close now
        # profit = original_credit - current_value
        current_value = entry_credit * (1 - pnl_target_pct / 100)
        future_exp = (datetime.now().date() + timedelta(days=dte)).isoformat()

        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-123",
            "strategy_type": "iron_butterfly",
            "underlying": "SPY",
            "legs": [
                {"symbol": "SPY250502P00535000", "side": "buy"},
                {"symbol": "SPY250502P00540000", "side": "sell"},
                {"symbol": "SPY250502C00540000", "side": "sell"},
                {"symbol": "SPY250502C00545000", "side": "buy"},
            ],
            "entry_credit": entry_credit,
            "expiration": future_exp,
            "status": "open",
        }]

        short_mid = current_value / 2
        long_mid = 0.0
        strategy.broker.get_option_snapshots.return_value = {
            "SPY250502P00535000": {"mid": long_mid},
            "SPY250502P00540000": {"mid": short_mid},
            "SPY250502C00540000": {"mid": short_mid},
            "SPY250502C00545000": {"mid": long_mid},
        }

    def test_close_when_50_pct_profit(self, strategy):
        self._setup_open(strategy, pnl_target_pct=55)

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "CLOSE"
        assert "50%" in result["reasoning"] or "captured" in result["reasoning"].lower()

    def test_close_when_dte_7(self, strategy):
        """DTE <= 7 triggers close regardless of P&L."""
        self._setup_open(strategy, pnl_target_pct=20)

        from datetime import datetime, timedelta
        exp = (datetime.now().date() + timedelta(days=5)).isoformat()
        strategy.spread_tracker.get_open_spreads.return_value[0]["expiration"] = exp

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "CLOSE"
        assert "DTE" in result["reasoning"] or "gamma" in result["reasoning"].lower()

    def test_close_on_stop_loss_200_pct(self, strategy):
        """Current value >= 200% of original credit triggers stop loss."""
        self._setup_open(strategy, pnl_target_pct=-150)  # big loss: current > 2× credit

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "CLOSE"
        assert "200%" in result["reasoning"] or "stop loss" in result["reasoning"].lower()

    def test_hold_when_position_ok(self, strategy):
        self._setup_open(strategy, pnl_target_pct=30)

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "HOLD"

    def test_resets_to_idle_if_spread_not_found(self, strategy):
        strategy.state = IronButterflyState.OPEN
        strategy.open_spread_id = "missing-spread"
        strategy.spread_tracker.get_open_spreads.return_value = []

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert strategy.state == IronButterflyState.IDLE
        assert result["action"] == "SKIP"


# ================================================================
# Execute entry/exit
# ================================================================


class TestExecution:
    def test_execute_entry_builds_4_leg_order(self, strategy):
        """Entry places a 4-leg mleg order with correct leg structure."""
        strategy.broker.place_mleg_order.return_value = {"id": "order-abc"}
        strategy.spread_tracker.register_spread.return_value = "spread-xyz"

        decision = _entry_decision(underlying="SPY")
        result = strategy.execute_entry(decision)

        assert result is True
        assert strategy.state == IronButterflyState.PENDING_OPEN
        assert strategy.pending_order_id == "order-abc"
        assert strategy.open_spread_id == "spread-xyz"

        call_args = strategy.broker.place_mleg_order.call_args
        legs = call_args[1]["legs"] if "legs" in call_args[1] else call_args[0][0]
        assert len(legs) == 4

    def test_execute_entry_short_legs_at_same_strike(self, strategy):
        """Both short OCC symbols must encode the same ATM strike."""
        strategy.broker.place_mleg_order.return_value = {"id": "order-abc"}
        strategy.spread_tracker.register_spread.return_value = "spread-xyz"

        decision = _entry_decision(underlying="SPY")
        strategy.execute_entry(decision)

        call_args = strategy.broker.place_mleg_order.call_args
        legs = call_args[1]["legs"] if "legs" in call_args[1] else call_args[0][0]

        sell_legs = [l for l in legs if l["side"] == "sell"]
        assert len(sell_legs) == 2

        # Both short legs have "00540000" in the symbol (strike 540)
        for leg in sell_legs:
            assert "00540000" in leg["symbol"], (
                f"Expected ATM strike 540 in short leg symbol: {leg['symbol']}"
            )

        # Put short and call short should have same strike
        put_short = next(l for l in sell_legs if "P" in l["symbol"])
        call_short = next(l for l in sell_legs if "C" in l["symbol"])
        put_strike = int(put_short["symbol"][-8:]) / 1000
        call_strike = int(call_short["symbol"][-8:]) / 1000
        assert put_strike == call_strike, (
            f"Short strikes must match for iron butterfly: "
            f"put={put_strike} vs call={call_strike}"
        )

    def test_execute_entry_failure_returns_false(self, strategy):
        strategy.broker.place_mleg_order.side_effect = Exception("API error")

        result = strategy.execute_entry(_entry_decision(underlying="SPY"))
        assert result is False
        assert strategy.state == IronButterflyState.IDLE

    def test_execute_exit_closes_spread(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-123",
            "legs": [
                {"symbol": "SPY250502P00535000", "side": "buy", "position_intent": "buy_to_open"},
                {"symbol": "SPY250502P00540000", "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": "SPY250502C00540000", "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": "SPY250502C00545000", "side": "buy", "position_intent": "buy_to_open"},
            ],
            "status": "open",
        }]
        strategy.broker.close_mleg_position.return_value = {"id": "close-abc"}

        result = strategy.execute_exit("spread-123", limit_price=1.00)

        assert result is True
        assert strategy.state == IronButterflyState.PENDING_CLOSE
        assert strategy.pending_order_id == "close-abc"

    def test_execute_exit_spread_not_found(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = []

        result = strategy.execute_exit("missing-spread")
        assert result is False


# ================================================================
# State machine transitions
# ================================================================


class TestStateMachine:
    def test_pending_open_returns_hold(self, strategy):
        strategy.state = IronButterflyState.PENDING_OPEN

        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)

        assert result["action"] == "HOLD"
        assert "PENDING_OPEN" in result["reasoning"]

    def test_pending_close_returns_hold(self, strategy):
        strategy.state = IronButterflyState.PENDING_CLOSE

        result = strategy.run_cycle(_base_context(), advisor=strategy._advisor)

        assert result["action"] == "HOLD"
        assert "PENDING_CLOSE" in result["reasoning"]


# ================================================================
# State persistence
# ================================================================


class TestStatePersistence:
    def test_state_roundtrips(self, strategy, tmp_path):
        strategy._state_path = tmp_path / "ib_state.json"
        strategy.state = IronButterflyState.OPEN
        strategy.open_spread_id = "test-id"
        strategy.pending_order_id = "order-id"
        strategy._save_state()

        data = json.loads(strategy._state_path.read_text())
        assert data["state"] == "OPEN"
        assert data["open_spread_id"] == "test-id"
        assert data["pending_order_id"] == "order-id"
