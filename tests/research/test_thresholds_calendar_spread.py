"""Tests for calendar_spread additions to research/liquidity/thresholds.py (Prompt 6)."""

from research.liquidity.thresholds import (
    STRATEGY_TYPES,
    STRATEGY_STRIKE_RANGES,
    LIQUIDITY_FLOORS,
    SLIPPAGE_FACTOR_BY_STRATEGY,
)


def test_calendar_spread_in_strategy_types():
    assert "calendar_spread" in STRATEGY_TYPES


def test_calendar_spread_strike_range_is_atm_call_short_leg_dte():
    sr = STRATEGY_STRIKE_RANGES["calendar_spread"]
    assert sr.option_type == "call"
    assert 0.45 <= sr.delta_min <= 0.55
    assert sr.dte_min == 20 and sr.dte_max == 35


def test_calendar_spread_liquidity_floor():
    cs = LIQUIDITY_FLOORS["calendar_spread"]
    assert cs.min_avg_oi == 150
    assert cs.max_avg_ba_spread_pct == 0.20


def test_calendar_spread_slippage_is_two_leg_factor():
    """Calendar spread is 2-leg: slippage factor is 0.66 (ORATS 2-leg calibration)."""
    assert SLIPPAGE_FACTOR_BY_STRATEGY["calendar_spread"] == 0.66


def test_sweep_skips_calendar_spread_when_flag_disabled():
    """When RESEARCH_SWEEP_CALENDAR_SPREAD_ENABLED=False, calendar_spread is filtered from strategies."""
    from backtesting.engine import SUPPORTED_STRATEGIES

    assert "calendar_spread" in SUPPORTED_STRATEGIES

    flag_enabled = False
    strategies = list(SUPPORTED_STRATEGIES)
    if not flag_enabled:
        strategies = [s for s in strategies if s != "calendar_spread"]

    assert "calendar_spread" not in strategies
    assert "iron_condor" in strategies


def test_sweep_includes_calendar_spread_when_flag_enabled():
    """When RESEARCH_SWEEP_CALENDAR_SPREAD_ENABLED=True, calendar_spread is included."""
    from backtesting.engine import SUPPORTED_STRATEGIES

    flag_enabled = True
    strategies = list(SUPPORTED_STRATEGIES)
    if not flag_enabled:
        strategies = [s for s in strategies if s != "calendar_spread"]

    assert "calendar_spread" in strategies
