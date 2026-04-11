"""Tests for shared-account capital guard."""

from unittest.mock import MagicMock

from strategies.guardrails import (
    SHARED_ACCOUNT_STRATEGIES,
    check_shared_account_buying_power,
    get_committed_capital_on_shared_account,
)


def _make_tracker(open_spreads: list) -> MagicMock:
    tracker = MagicMock()
    tracker.get_open_spreads.return_value = open_spreads
    return tracker


def test_no_open_spreads_allows_trade():
    tracker = _make_tracker([])
    ok, reason = check_shared_account_buying_power(
        new_trade_max_loss=500,
        account_buying_power=100_000,
        spread_tracker=tracker,
    )
    assert ok is True
    assert reason == ""


def test_trade_exceeds_individual_2pct_limit():
    tracker = _make_tracker([])
    ok, reason = check_shared_account_buying_power(
        new_trade_max_loss=2_100,
        account_buying_power=100_000,
        spread_tracker=tracker,
    )
    assert ok is False
    assert "2%" in reason


def test_combined_exceeds_10pct_portfolio_limit():
    existing = [
        {"strategy_type": "bull_put_spread", "max_loss": 4_500},
        {"strategy_type": "bear_call_spread", "max_loss": 4_500},
    ]
    tracker = _make_tracker(existing)
    ok, reason = check_shared_account_buying_power(
        new_trade_max_loss=1_500,
        account_buying_power=100_000,
        spread_tracker=tracker,
    )
    assert ok is False
    assert "shared account" in reason.lower()


def test_wheel_spreads_not_counted_in_shared_capital():
    existing = [
        {"strategy_type": "wheel", "max_loss": 50_000},
        {"strategy_type": "iron_condor", "max_loss": 5_000},
    ]
    tracker = _make_tracker(existing)
    committed = get_committed_capital_on_shared_account(tracker)
    assert committed == 0.0


def test_shared_account_strategies_set_is_correct():
    assert SHARED_ACCOUNT_STRATEGIES == {
        "bull_put_spread",
        "bear_call_spread",
        "long_call_vertical",
    }


def test_zero_buying_power_rejected():
    tracker = _make_tracker([])
    ok, reason = check_shared_account_buying_power(
        new_trade_max_loss=100,
        account_buying_power=0,
        spread_tracker=tracker,
    )
    assert ok is False
    assert "zero" in reason.lower()


def test_within_limits_passes():
    existing = [
        {"strategy_type": "bull_put_spread", "max_loss": 2_000},
    ]
    tracker = _make_tracker(existing)
    ok, reason = check_shared_account_buying_power(
        new_trade_max_loss=1_500,
        account_buying_power=100_000,
        spread_tracker=tracker,
    )
    assert ok is True
