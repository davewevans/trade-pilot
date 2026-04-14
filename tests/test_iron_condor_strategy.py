"""Tests for the Iron Condor strategy, state machine, and guardrails."""

import json
from unittest.mock import MagicMock, patch

import pytest

from strategies.guardrails import Guardrails
from strategies.iron_condor_strategy import IronCondorState, IronCondorStrategy


# ── Helpers ─────────────────────────────────────────────────


def _base_context(**overrides):
    """Return a valid context dict for iron condor entry."""
    ctx = {
        "confirmed_market_regime": "NEUTRAL",
        "iv_environment": "HIGH",
        "iv_rank": 55,
        "macro": {"vix": 22},
        "fundamentals": {"days_to_earnings": 60},
        "technicals": {"current_price": 540.0, "above_sma_50": True},
        "spread_candidates": {
            "iron_condor": {
                "iron_condor_legs": {
                    "put_spread": {
                        "short_leg": {"symbol": "SPY250502P00530000", "strike": 530, "delta": -0.20},
                        "long_leg": {"symbol": "SPY250502P00525000", "strike": 525},
                        "net_credit": 1.20,
                        "liquidity_ok": True,
                    },
                    "call_spread": {
                        "short_leg": {"symbol": "SPY250502C00550000", "strike": 550, "delta": 0.20},
                        "long_leg": {"symbol": "SPY250502C00555000", "strike": 555},
                        "net_credit": 1.00,
                        "liquidity_ok": True,
                    },
                    "total_credit": 2.20,
                    "total_max_loss": 280.0,
                },
            },
        },
    }
    ctx.update(overrides)
    return ctx


def _entry_decision(**overrides):
    """Return a valid iron condor entry decision dict."""
    d = {
        "action": "OPEN",
        "put_short_symbol": "SPY250502P00530000",
        "put_long_symbol": "SPY250502P00525000",
        "call_short_symbol": "SPY250502C00550000",
        "call_long_symbol": "SPY250502C00555000",
        "expiration": "2025-05-02",
        "dte": 30,
        "total_credit": 2.20,
        "max_loss": 280.0,
        "limit_price": -2.20,
        "reasoning": "Good setup",
        "skip_reason": None,
    }
    d.update(overrides)
    return d


@pytest.fixture
def strategy(tmp_path):
    """IronCondorStrategy with mocked broker, writer, and tracker."""
    with patch("strategies.iron_condor_strategy.settings") as mock_settings:
        mock_settings.SNAPSHOTS_DIR = tmp_path

        broker = MagicMock()
        sw = MagicMock()
        tracker = MagicMock()
        tracker.get_open_spreads.return_value = []
        tracker.get_active_spreads.return_value = []

        with patch.object(IronCondorStrategy, "__init__", lambda self, *a, **k: None):
            s = IronCondorStrategy.__new__(IronCondorStrategy)
            s.broker = broker
            s.state_writer = sw
            s.spread_tracker = tracker
            s.state = IronCondorState.IDLE
            s.open_spread_id = None
            s.pending_order_id = None
            s.cb_status_at_entry = None
            s._state_path = tmp_path / "iron_condor_state.json"
            # Mock advisor — strategies now REQUIRE an advisor in run_cycle.
            advisor = MagicMock()
            advisor.ask_spread.return_value = {"action": "SKIP", "reasoning": "default mock"}
            s._advisor = advisor

        return s


# ================================================================
# IDLE state — entry evaluation
# ================================================================


class TestIdleEntry:
    def test_open_when_all_conditions_met(self, strategy):
        """Claude returns OPEN and conditions pass."""
        strategy._advisor.ask_spread.return_value = _entry_decision()

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "OPEN"
        assert result["total_credit"] == 2.20

    def test_skip_when_ivr_too_low(self, strategy):
        ctx = _base_context(iv_rank=30)
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "IV rank" in result["reasoning"]

    def test_skip_when_earnings_too_close(self, strategy):
        ctx = _base_context(fundamentals={"days_to_earnings": 20})
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
            spread_candidates={"iron_condor": {"iron_condor_legs": None}},
        )
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "candidates" in result["reasoning"].lower()

    def test_skip_when_already_open(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [
            {"spread_id": "abc", "status": "open"},
        ]
        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "SKIP"
        assert "open iron condor" in result["reasoning"].lower()


# ================================================================
# OPEN state — management evaluation
# ================================================================


class TestOpenManagement:
    def _setup_open(self, strategy, pnl_target_pct=30, dte=25,
                    underlying_price=540.0, entry_credit=2.20):
        """Put strategy in OPEN state with a mock spread."""
        from datetime import datetime, timedelta

        strategy.state = IronCondorState.OPEN
        strategy.open_spread_id = "spread-123"

        current_value = entry_credit * (1 - pnl_target_pct / 100)
        future_exp = (datetime.now().date() + timedelta(days=dte)).isoformat()

        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-123",
            "strategy_type": "iron_condor",
            "underlying": "SPY",
            "legs": [
                {"symbol": "SPY250502P00530000", "side": "sell"},
                {"symbol": "SPY250502P00525000", "side": "buy"},
                {"symbol": "SPY250502C00550000", "side": "sell"},
                {"symbol": "SPY250502C00555000", "side": "buy"},
            ],
            "entry_credit": entry_credit,
            "expiration": future_exp,
            "status": "open",
        }]

        # Mock snapshots: short legs have value, long legs less
        short_mid = current_value / 2
        long_mid = 0.0
        strategy.broker.get_option_snapshots.return_value = {
            "SPY250502P00530000": {"mid": short_mid},
            "SPY250502P00525000": {"mid": long_mid},
            "SPY250502C00550000": {"mid": short_mid},
            "SPY250502C00555000": {"mid": long_mid},
        }

    def test_close_when_50_pct_profit(self, strategy):
        self._setup_open(strategy, pnl_target_pct=55)

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "CLOSE"
        assert "50%" in result["reasoning"] or "captured" in result["reasoning"].lower()

    def test_close_when_dte_10(self, strategy):
        """DTE <= 10 triggers close regardless of P&L."""
        self._setup_open(strategy, pnl_target_pct=20)

        # Override expiration to be 8 days away
        from datetime import datetime, timedelta
        exp = (datetime.now().date() + timedelta(days=8)).isoformat()
        strategy.spread_tracker.get_open_spreads.return_value[0]["expiration"] = exp

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "CLOSE"
        assert "DTE" in result["reasoning"] or "gamma" in result["reasoning"].lower()

    def test_hold_when_breached_but_dte_gt_20(self, strategy):
        """Short strike breached with DTE > 20 → hold, let theta work."""
        self._setup_open(strategy, pnl_target_pct=10)

        from datetime import datetime, timedelta
        exp = (datetime.now().date() + timedelta(days=30)).isoformat()
        strategy.spread_tracker.get_open_spreads.return_value[0]["expiration"] = exp

        # Underlying below short put strike (breach)
        ctx = _base_context(technicals={"current_price": 525.0})
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "HOLD"
        assert "theta" in result["reasoning"].lower()

    def test_hold_when_position_ok(self, strategy):
        self._setup_open(strategy, pnl_target_pct=30)

        ctx = _base_context()
        result = strategy.run_cycle(ctx, advisor=strategy._advisor)

        assert result["action"] == "HOLD"


# ================================================================
# Guardrails
# ================================================================


class TestIronCondorGuardrails:
    @pytest.fixture
    def guardrails(self):
        return Guardrails()

    def test_valid_entry_passes(self, guardrails):
        decision = _entry_decision()
        ctx = _base_context()
        account = {"buying_power": "100000"}

        ok, reason = guardrails.validate_iron_condor_entry(decision, ctx, account)
        assert ok is True
        assert reason == ""

    def test_rejects_positive_limit_price(self, guardrails):
        decision = _entry_decision(limit_price=2.20)  # wrong sign
        ctx = _base_context()
        account = {"buying_power": "100000"}

        ok, reason = guardrails.validate_iron_condor_entry(decision, ctx, account)
        assert ok is False
        assert "negative" in reason.lower()

    def test_rejects_max_loss_exceeds_5pct(self, guardrails):
        decision = _entry_decision(max_loss=6000)  # > 5% of 100k
        ctx = _base_context()
        account = {"buying_power": "100000"}

        ok, reason = guardrails.validate_iron_condor_entry(decision, ctx, account)
        assert ok is False
        assert "5%" in reason

    def test_rejects_low_total_credit(self, guardrails):
        decision = _entry_decision(total_credit=0.80)
        ctx = _base_context()
        account = {"buying_power": "100000"}

        ok, reason = guardrails.validate_iron_condor_entry(decision, ctx, account)
        assert ok is False
        assert "$1.00" in reason

    def test_rejects_dte_outside_range(self, guardrails):
        decision = _entry_decision(dte=15)
        ctx = _base_context()
        account = {"buying_power": "100000"}

        ok, reason = guardrails.validate_iron_condor_entry(decision, ctx, account)
        assert ok is False
        assert "DTE" in reason

    def test_rejects_invalid_occ_symbol(self, guardrails):
        decision = _entry_decision(put_short_symbol="INVALID")
        ctx = _base_context()
        account = {"buying_power": "100000"}

        ok, reason = guardrails.validate_iron_condor_entry(decision, ctx, account)
        assert ok is False
        assert "OCC" in reason

    def test_rejects_earnings_within_30_days(self, guardrails):
        decision = _entry_decision()
        ctx = _base_context(fundamentals={"days_to_earnings": 25})
        account = {"buying_power": "100000"}

        ok, reason = guardrails.validate_iron_condor_entry(decision, ctx, account)
        assert ok is False
        assert "Earnings" in reason

    def test_rejects_duplicate_condor(self, guardrails):
        decision = _entry_decision()
        ctx = _base_context()
        account = {"buying_power": "100000"}
        open_condors = [{"underlying": "SPY", "status": "open"}]

        ok, reason = guardrails.validate_iron_condor_entry(
            decision, ctx, account, open_condors=open_condors,
        )
        assert ok is False
        assert "open iron condor" in reason.lower()


# ================================================================
# Execute entry/exit
# ================================================================


class TestExecution:
    def test_execute_entry_places_4_leg_order(self, strategy):
        strategy.broker.place_mleg_order.return_value = {"id": "order-abc"}
        strategy.spread_tracker.register_spread.return_value = "spread-xyz"

        decision = _entry_decision(underlying="SPY")
        result = strategy.execute_entry(decision)

        assert result is True
        assert strategy.state == IronCondorState.OPEN
        assert strategy.open_spread_id == "spread-xyz"

        call_args = strategy.broker.place_mleg_order.call_args
        legs = call_args[1]["legs"] if "legs" in call_args[1] else call_args[0][0]
        assert len(legs) == 4

    def test_execute_entry_failure(self, strategy):
        strategy.broker.place_mleg_order.side_effect = Exception("API error")

        result = strategy.execute_entry(_entry_decision(underlying="SPY"))
        assert result is False
        assert strategy.state == IronCondorState.IDLE

    def test_execute_exit_closes_spread(self, strategy):
        strategy.spread_tracker.get_open_spreads.return_value = [{
            "spread_id": "spread-123",
            "legs": [
                {"symbol": "A", "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": "B", "side": "buy", "position_intent": "buy_to_open"},
                {"symbol": "C", "side": "sell", "position_intent": "sell_to_open"},
                {"symbol": "D", "side": "buy", "position_intent": "buy_to_open"},
            ],
            "status": "open",
        }]
        strategy.broker.close_mleg_position.return_value = {"id": "close-abc"}

        result = strategy.execute_exit("spread-123", limit_price=0.50)

        assert result is True
        assert strategy.state == IronCondorState.IDLE
        strategy.spread_tracker.close_spread.assert_called_once_with("spread-123", exit_credit=0.50)


# ================================================================
# State persistence
# ================================================================


class TestStatePersistence:
    def test_state_roundtrips(self, strategy, tmp_path):
        strategy._state_path = tmp_path / "ic_state.json"
        strategy.state = IronCondorState.OPEN
        strategy.open_spread_id = "test-id"
        strategy._save_state()

        # Read it back
        data = json.loads(strategy._state_path.read_text())
        assert data["state"] == "OPEN"
        assert data["open_spread_id"] == "test-id"


# ================================================================
# IV forecast and contango checks (Prompt 3)
# ================================================================


class TestIVForecastAndContango:
    def test_undervalued_iv_blocks_entry(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"iv_overvalued_label": "UNDERVALUED"}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "UNDERVALUED" in reason
        assert score == 0.0

    def test_backwardation_blocks_entry(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {"contango_label": "BACKWARDATION"}
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is not None
        assert "BACKWARDATION" in reason
        assert score == 0.0

    def test_overvalued_iv_boosts_score(self, strategy):
        ctx_ov = _base_context()
        ctx_ov["volatility"] = {"iv_overvalued_label": "OVERVALUED"}
        ctx_base = _base_context()
        _, score_base = strategy.pre_check_entry(ctx_base)
        _, score_ov = strategy.pre_check_entry(ctx_ov)
        assert score_ov == pytest.approx(score_base * 1.20, rel=0.01)

    def test_normal_contango_and_fair_iv_passes(self, strategy):
        ctx = _base_context()
        ctx["volatility"] = {
            "iv_overvalued_label": "FAIR",
            "contango_label": "NORMAL",
        }
        reason, score = strategy.pre_check_entry(ctx)
        assert reason is None
        assert score > 0
