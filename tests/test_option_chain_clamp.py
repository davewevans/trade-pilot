"""Tests for clamp_strike_range helper in context_builder."""

import math

from data.option_chain_utils import clamp_strike_range


def test_clamp_strike_range_put_side():
    low, high = clamp_strike_range(710.0, "put")
    assert math.isclose(low, 710.0 * 0.75, rel_tol=1e-6)
    assert math.isclose(high, 710.0 * 1.05, rel_tol=1e-6)


def test_clamp_strike_range_call_side():
    low, high = clamp_strike_range(710.0, "call")
    assert math.isclose(low, 710.0 * 0.95, rel_tol=1e-6)
    assert math.isclose(high, 710.0 * 1.25, rel_tol=1e-6)


def test_clamp_strike_range_custom_pct():
    low, high = clamp_strike_range(100.0, "put", clamp_pct=0.10)
    assert math.isclose(low, 90.0, rel_tol=1e-6)
    assert math.isclose(high, 105.0, rel_tol=1e-6)


def test_clamp_strike_range_handles_none_spot():
    assert clamp_strike_range(None, "put") == (0.0, float("inf"))
    assert clamp_strike_range(0.0, "put") == (0.0, float("inf"))


def test_clamp_strike_range_handles_unknown_side():
    low, high = clamp_strike_range(100.0, "straddle")
    assert low == 0.0
    assert high == float("inf")


def test_clamp_strike_range_negative_spot():
    assert clamp_strike_range(-50.0, "put") == (0.0, float("inf"))
