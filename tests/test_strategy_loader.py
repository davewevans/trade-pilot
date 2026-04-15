"""Tests for the strategy definition loader."""

import pytest

from strategies.strategy_loader import (
    get_strategy_entry_params,
    get_strategy_guardrail_params,
    get_strategy_management_params,
    invalidate_cache,
    list_available_strategies,
    load_all_strategies,
    load_strategy,
)

# Clear the LRU cache before each test to avoid cross-test contamination.
@pytest.fixture(autouse=True)
def clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def test_load_strategy_wheel_returns_required_fields():
    defn = load_strategy("wheel")
    assert defn["name"] == "wheel"
    assert defn["display_name"] == "Wheel"
    assert defn["type"] == "wheel"


def test_load_strategy_nonexistent_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_strategy("nonexistent_strategy_xyz")


def test_load_all_strategies_returns_all_six():
    all_strats = load_all_strategies()
    expected = {
        "wheel",
        "bull_put_spread",
        "bear_call_spread",
        "iron_condor",
        "long_call_vertical",
        "adaptive_spreads",
    }
    assert expected.issubset(set(all_strats.keys()))
    assert len(all_strats) >= 6


def test_list_available_strategies_returns_expected_names():
    names = list_available_strategies()
    assert "wheel" in names
    assert "bull_put_spread" in names
    assert "bear_call_spread" in names
    assert "iron_condor" in names
    assert "long_call_vertical" in names
    assert "adaptive_spreads" in names


def test_get_strategy_entry_params_bull_put_spread():
    params = get_strategy_entry_params("bull_put_spread")
    assert "short_delta_min" in params
    assert "dte_min" in params
    assert "min_net_credit" in params


def test_composite_strategy_has_sub_strategies():
    defn = load_strategy("adaptive_spreads")
    assert defn.get("composite") is True
    sub = defn.get("sub_strategies", [])
    assert isinstance(sub, list)
    assert "bull_put_spread" in sub
    assert "bear_call_spread" in sub
    assert "long_call_vertical" in sub


def test_invalidate_cache_clears_lru():
    # Load once to populate cache
    defn1 = load_strategy("wheel")
    invalidate_cache()
    # Should load fresh from disk (no error)
    defn2 = load_strategy("wheel")
    assert defn1 == defn2


def test_get_strategy_guardrail_params_returns_guardrails_block():
    params = get_strategy_guardrail_params("bull_put_spread")
    assert "dte_hard_min" in params
    assert "min_net_credit_hard" in params


def test_get_strategy_management_params_returns_management_block():
    params = get_strategy_management_params("wheel")
    assert "profit_target_pct" in params
    assert "close_dte_threshold" in params
