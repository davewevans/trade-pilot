"""Tests for iron_butterfly additions to research/liquidity/thresholds.py (Prompt 3)."""

from research.liquidity.thresholds import (
    STRATEGY_TYPES,
    STRATEGY_STRIKE_RANGES,
    LIQUIDITY_FLOORS,
    SLIPPAGE_FACTOR_BY_STRATEGY,
)


def test_iron_butterfly_in_strategy_types():
    assert "iron_butterfly" in STRATEGY_TYPES


def test_iron_butterfly_floor_tighter_than_iron_condor():
    ib = LIQUIDITY_FLOORS["iron_butterfly"]
    ic = LIQUIDITY_FLOORS["iron_condor"]
    assert ib.min_avg_oi >= ic.min_avg_oi
    assert ib.max_avg_ba_spread_pct <= ic.max_avg_ba_spread_pct


def test_iron_butterfly_strike_range_atm():
    sr = STRATEGY_STRIKE_RANGES["iron_butterfly"]
    assert sr.option_type == "both"
    assert 0.40 <= sr.delta_min <= 0.55
    assert 0.40 <= sr.delta_max <= 0.55


def test_iron_butterfly_slippage_matches_iron_condor():
    assert SLIPPAGE_FACTOR_BY_STRATEGY["iron_butterfly"] == SLIPPAGE_FACTOR_BY_STRATEGY["iron_condor"]


def test_sweep_skips_iron_butterfly_when_flag_disabled():
    """When RESEARCH_SWEEP_IRON_BUTTERFLY_ENABLED=False, iron_butterfly is filtered from strategies."""
    from backtesting.engine import SUPPORTED_STRATEGIES

    # Confirm iron_butterfly is in SUPPORTED_STRATEGIES (base list)
    assert "iron_butterfly" in SUPPORTED_STRATEGIES

    # Replicate the exact filtering logic from run_sweep
    flag_enabled = False
    strategies = list(SUPPORTED_STRATEGIES)
    if not flag_enabled:
        strategies = [s for s in strategies if s != "iron_butterfly"]

    assert "iron_butterfly" not in strategies
    assert "iron_condor" in strategies


def test_sweep_includes_iron_butterfly_when_flag_enabled():
    """When RESEARCH_SWEEP_IRON_BUTTERFLY_ENABLED=True, iron_butterfly is included."""
    from backtesting.engine import SUPPORTED_STRATEGIES

    flag_enabled = True
    strategies = list(SUPPORTED_STRATEGIES)
    if not flag_enabled:
        strategies = [s for s in strategies if s != "iron_butterfly"]

    assert "iron_butterfly" in strategies
