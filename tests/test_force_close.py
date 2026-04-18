"""Tests for strategies/force_close.py and its integration in position_check."""

import pytest
from datetime import datetime, timedelta
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call


# ── Unit tests: _compute_close_limit_buy_to_close ─────────────────────────────

def test_compute_close_limit_buy_to_close_normal_case():
    """mid + 40% of spread gives the expected aggressive limit."""
    from strategies.force_close import _compute_close_limit_buy_to_close
    # bid=1.00, ask=1.20 → mid=1.10, spread=0.20, limit = 1.10 + 0.08 = 1.18
    result = _compute_close_limit_buy_to_close(1.00, 1.20)
    assert result == 1.18


def test_compute_close_limit_buy_to_close_capped_at_ask():
    """Result is never above the ask price."""
    from strategies.force_close import _compute_close_limit_buy_to_close
    # Use a very narrow spread where the result rounds to exactly the ask.
    # bid=1.00, ask=1.01 → mid=1.005, spread=0.01 → limit=1.009 → rounds to 1.01
    result = _compute_close_limit_buy_to_close(1.00, 1.01)
    assert result is not None
    assert result <= 1.01


def test_compute_close_limit_buy_to_close_missing_quote_returns_none():
    """Returns None when bid or ask is missing."""
    from strategies.force_close import _compute_close_limit_buy_to_close
    assert _compute_close_limit_buy_to_close(None, 1.20) is None
    assert _compute_close_limit_buy_to_close(1.00, None) is None
    assert _compute_close_limit_buy_to_close(None, None) is None


# ── Unit tests: check_wheel_short_put ─────────────────────────────────────────

def test_check_wheel_short_put_triggers_on_deep_itm_near_expiry():
    """delta=-0.75, dte=2 → trigger fires."""
    from strategies.force_close import check_wheel_short_put
    result = check_wheel_short_put(
        {"symbol": "AAPL260420P00150000"},
        {"delta": -0.75, "dte": 2, "bid": 15.00, "ask": 15.40},
    )
    assert result is not None
    assert result.rule_code == "WHEEL_SHORT_PUT_DEEP_ITM_EXPIRING"
    assert "0.75" in result.reason
    assert result.suggested_limit_price is not None


def test_check_wheel_short_put_no_trigger_above_delta_threshold():
    """delta=-0.65 (below 0.70 abs) → no trigger."""
    from strategies.force_close import check_wheel_short_put
    result = check_wheel_short_put(
        {},
        {"delta": -0.65, "dte": 2, "bid": 5.00, "ask": 5.40},
    )
    assert result is None


def test_check_wheel_short_put_no_trigger_below_dte_threshold():
    """delta=-0.75 but dte=4 (above 3) → no trigger."""
    from strategies.force_close import check_wheel_short_put
    result = check_wheel_short_put(
        {},
        {"delta": -0.75, "dte": 4, "bid": 5.00, "ask": 5.40},
    )
    assert result is None


def test_check_wheel_short_put_boundary_exactly_at_thresholds():
    """delta=-0.70 (exactly), dte=3 (exactly) → trigger fires (inclusive bounds)."""
    from strategies.force_close import check_wheel_short_put
    result = check_wheel_short_put(
        {},
        {"delta": -0.70, "dte": 3, "bid": 5.00, "ask": 5.40},
    )
    assert result is not None
    assert result.rule_code == "WHEEL_SHORT_PUT_DEEP_ITM_EXPIRING"


def test_check_wheel_short_put_kill_switch_off(monkeypatch):
    """FORCE_CLOSE_ENABLED=false → returns None even when conditions are met."""
    import strategies.force_close as fc_mod
    monkeypatch.setattr(fc_mod.settings, "FORCE_CLOSE_ENABLED", False)
    result = fc_mod.check_wheel_short_put(
        {},
        {"delta": -0.75, "dte": 2, "bid": 5.00, "ask": 5.40},
    )
    assert result is None


def test_check_wheel_short_put_missing_fields_returns_none():
    """delta=None or dte=None → None, no exception raised."""
    from strategies.force_close import check_wheel_short_put
    assert check_wheel_short_put({}, {"delta": None, "dte": 2}) is None
    assert check_wheel_short_put({}, {"delta": -0.75, "dte": None}) is None
    assert check_wheel_short_put({}, {}) is None


# ── Unit tests: check_credit_spread_short_leg ─────────────────────────────────

@pytest.mark.parametrize("strategy_type,expected_code", [
    ("bull_put_spread", "BPS_SHORT_LEG_DEEP_ITM_EXPIRING"),
    ("bear_call_spread", "BCS_SHORT_LEG_DEEP_ITM_EXPIRING"),
    ("iron_condor", "IC_SHORT_LEG_DEEP_ITM_EXPIRING"),
])
def test_check_credit_spread_triggers_per_strategy_type(strategy_type, expected_code):
    """Trigger fires for each supported credit spread type."""
    from strategies.force_close import check_credit_spread_short_leg
    result = check_credit_spread_short_leg(
        strategy_type,
        {"delta": -0.75, "dte": 2},
    )
    assert result is not None
    assert result.rule_code == expected_code
    assert result.suggested_limit_price is None  # spread pricing handled by caller


def test_check_credit_spread_unknown_strategy_returns_none():
    """An unrecognised strategy_type → None."""
    from strategies.force_close import check_credit_spread_short_leg
    result = check_credit_spread_short_leg("foo", {"delta": -0.75, "dte": 2})
    assert result is None


# ── Integration test: position_check bypasses advisor on force-close ──────────

def _make_occ_symbol(days_from_now: int = 2) -> str:
    """Build an OCC symbol whose expiry is `days_from_now` days from today."""
    expiry = datetime.now().date() + timedelta(days=days_from_now)
    return f"AAPL{expiry.strftime('%y%m%d')}P00150000"


def _fake_context(occ_symbol: str) -> dict:
    """Minimal context dict that will trigger the force-close for SHORT_PUT."""
    return {
        "option_chain": {
            occ_symbol: {
                "symbol": occ_symbol,
                "implied_volatility": 0.60,
                "greeks": {
                    "delta": -0.75,
                    "gamma": 0.01,
                    "theta": -0.05,
                    "vega": 0.10,
                    "rho": -0.01,
                },
                "latest_quote": {
                    "bid_price": 15.00,
                    "bid_size": 10,
                    "ask_price": 15.40,
                    "ask_size": 5,
                    "timestamp": "2026-04-18",
                },
            }
        },
        "technicals": {"current_price": 145.0, "above_50sma": False, "above_200sma": False, "rsi": 25.0},
        "macro": {"vix": 40.0, "fear_greed": 15},
        "iv_rank": 80.0,
        "confirmed_market_regime": "CRASH",
        "account": {"buying_power": "100000", "options_buying_power": "100000"},
        "_research": None,
    }


def test_position_check_force_close_bypasses_advisor(monkeypatch, tmp_path):
    """When SHORT_PUT triggers force-close, advisor.ask is NOT called and a CLOSE
    decision is recorded with prompt_version=None."""
    from strategies.circuit_breaker import CircuitBreakerStatus
    from strategies.wheel_strategy import WheelState

    occ_symbol = _make_occ_symbol(days_from_now=2)  # DTE=2 → triggers rule

    # ── Broker mock ──
    mock_broker = MagicMock()
    mock_broker.get_clock.return_value = {"is_open": True}
    mock_broker.get_account.return_value = {
        "buying_power": "100000",
        "options_buying_power": "100000",
    }
    mock_broker.get_positions.return_value = [
        {"symbol": occ_symbol, "order_id": "ord_test", "qty": "-1"},
    ]

    # ── Circuit breaker mock ──
    cb_status = CircuitBreakerStatus()  # GREEN, no halt
    mock_cb = MagicMock()
    mock_cb.is_halted.return_value = False
    mock_cb.update.return_value = cb_status
    mock_cb._status = cb_status
    mock_cb_cls = MagicMock(return_value=mock_cb)
    mock_cb_cls.calculate_portfolio_equity.return_value = 100_000.0

    # ── Advisor mock — ask() must NOT be called ──
    mock_advisor = MagicMock()
    mock_advisor.prompt_version = "abc123456def"
    mock_advisor_cls = MagicMock(return_value=mock_advisor)

    # ── Recorder mock ──
    mock_recorder = MagicMock()
    mock_recorder.record_decision.return_value = (42, 7)  # (decision_id, cycle_id)

    # ── execute_decision mock ──
    mock_execute = MagicMock(return_value={"id": "force_close_order_id"})

    # ── WheelStrategy mock ──
    mock_strategy = MagicMock()
    mock_strategy.get_current_state.return_value = WheelState.SHORT_PUT

    # ── ContextBuilder mock ──
    mock_ctx_builder = MagicMock()
    mock_ctx_builder.build.return_value = _fake_context(occ_symbol)
    mock_ctx_builder_cls = MagicMock(return_value=mock_ctx_builder)

    # Ensure DRY_RUN is off so execute_decision is called
    from config import settings
    monkeypatch.setattr(settings, "DRY_RUN", False)

    with patch("brokers.broker_factory.make_broker", return_value=mock_broker), \
         patch("brokers.broker_factory.get_broker", return_value=mock_broker), \
         patch("strategies.circuit_breaker.CircuitBreaker", mock_cb_cls), \
         patch("strategies.wheel_strategy.WheelStrategy", return_value=mock_strategy), \
         patch("data.context_builder.ContextBuilder", mock_ctx_builder_cls), \
         patch("ai.claude_advisor.ClaudeAdvisor", mock_advisor_cls), \
         patch("main.execute_decision", mock_execute), \
         patch("database.db.Database"), \
         patch("database.recorder.TradeRecorder", return_value=mock_recorder), \
         patch("database.repositories.ApiUsageRepository"), \
         patch("data.state_writer.StateWriter"), \
         patch("data.trade_journal.TradeJournal"), \
         patch("strategies.guardrails.Guardrails"), \
         patch("jobs._report.append_section"):

        from jobs.position_check import run
        run()

    # ── Assertions ──

    # Advisor must NOT have been asked (force-close fires before it)
    mock_advisor.ask.assert_not_called()

    # A CLOSE decision must have been recorded
    assert mock_recorder.record_decision.called, "recorder.record_decision was not called"
    kw = mock_recorder.record_decision.call_args.kwargs
    assert kw["action"] == "CLOSE"
    assert kw["prompt_version"] is None, "Force-close decisions must have prompt_version=None"
    assert "WHEEL_SHORT_PUT_DEEP_ITM_EXPIRING" in kw["reasoning"]

    # execute_decision must have been called (not a dry-run)
    mock_execute.assert_called_once()
    exec_decision_arg = mock_execute.call_args.args[1]  # second positional arg
    assert exec_decision_arg["action"] == "close"
    assert exec_decision_arg["symbol"] == occ_symbol
