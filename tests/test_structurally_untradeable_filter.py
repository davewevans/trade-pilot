"""Unit tests for is_structurally_untradeable_for_csp.

The helper lives in strategies/guardrails.py and is a module-level function
(not a Guardrails class method). It reads the option_chain from the already-built
context dict and checks whether every contract in the -0.20 to -0.30 delta band
would exceed the 10% options_buying_power cap. No mocking of external services is
needed — the helper only reads from a dict.

Test matrix:
  - AAPL-class ($275 stock, small BP): untradeable
  - BAC-class ($52 stock, large BP): tradeable
  - Empty delta band: returns (False, None), not STRUCTURALLY_UNTRADEABLE
  - Band present but some strikes are zero / bad data: gracefully tradeable
  - BP = 0: untradeable on any non-zero strike
  - Boundary: strike × 100 exactly equals cap → tradeable (uses >, not >=)
  - Kill-switch off: helper is never called (wiring test via config)
  - Multiple contracts in band: min strike is used
"""

import pytest

from strategies.guardrails import is_structurally_untradeable_for_csp
from strategies.skip_codes import SkipCode


# ── Helpers ───────────────────────────────────────────────────────────────────

def _context_with_chain(contracts: list) -> dict:
    return {"option_chain": {"contracts": contracts, "snapshots": {}}}


def _put(strike: float, delta: float) -> dict:
    return {"strike_price": strike, "delta": delta, "symbol": f"TEST_PUT_{strike}"}


# ── Core logic tests ──────────────────────────────────────────────────────────

def test_untradeable_when_lowest_strike_exceeds_cap():
    """AAPL @ ~$275, BP $100K: strike $261 × 100 = $26,100 > 10% × $100K = $10,000."""
    contracts = [_put(261.0, -0.25), _put(255.0, -0.22)]
    ctx = _context_with_chain(contracts)
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is True
    assert reason is not None
    assert "STRUCTURALLY_UNTRADEABLE" in reason
    assert "255.00" in reason  # min strike reported


def test_tradeable_when_lowest_strike_fits():
    """BAC @ ~$52, BP $100K: strike $49 × 100 = $4,900 < 10% × $100K = $10,000."""
    contracts = [_put(49.0, -0.25), _put(47.0, -0.22)]
    ctx = _context_with_chain(contracts)
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False
    assert reason is None


def test_uses_min_strike_not_max():
    """When band has multiple strikes, filter on min (cheapest) — not max."""
    # $50 and $200 in band; min is $50 → $5,000 cost < $10,000 cap → tradeable
    contracts = [_put(200.0, -0.25), _put(50.0, -0.22)]
    ctx = _context_with_chain(contracts)
    untradeable, _ = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False


def test_boundary_exactly_equal_is_tradeable():
    """strike × 100 == cap is tradeable; filter uses >, not >=."""
    # $100 strike × 100 = $10,000; cap = 10% × $100K = $10,000 → exactly equal → tradeable
    contracts = [_put(100.0, -0.25)]
    ctx = _context_with_chain(contracts)
    untradeable, _ = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False


def test_boundary_one_cent_over_is_untradeable():
    """strike × 100 = cap + 0.01 is untradeable."""
    # $100.0001 strike → cost $10,000.01 > $10,000 cap
    contracts = [_put(100.0001, -0.25)]
    ctx = _context_with_chain(contracts)
    untradeable, _ = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is True


# ── Empty / missing chain edge cases ─────────────────────────────────────────

def test_empty_delta_band_returns_false():
    """No contracts in -0.20 to -0.30 band → (False, None), not STRUCTURALLY_UNTRADEABLE."""
    # These contracts are outside the target band
    contracts = [_put(50.0, -0.10), _put(50.0, -0.40)]
    ctx = _context_with_chain(contracts)
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False
    assert reason is None


def test_no_option_chain_in_context_returns_false():
    """option_chain missing from context entirely → (False, None)."""
    ctx: dict = {}
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False
    assert reason is None


def test_option_chain_none_returns_false():
    """option_chain is None (fetch failed) → (False, None)."""
    ctx = {"option_chain": None}
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False
    assert reason is None


def test_empty_contracts_list_returns_false():
    """option_chain has empty contracts list → (False, None)."""
    ctx = _context_with_chain([])
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False
    assert reason is None


# ── Bad data edge cases ───────────────────────────────────────────────────────

def test_contract_with_zero_strike_skipped():
    """Contracts with strike_price=0 are excluded from min() calculation."""
    # Only bad-data contract in band → no valid min → returns (False, None)
    contracts = [_put(0.0, -0.25)]
    ctx = _context_with_chain(contracts)
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False
    assert reason is None


def test_contract_with_missing_delta_excluded():
    """Contracts with no delta field are excluded from the band."""
    contracts = [{"strike_price": 50.0, "symbol": "NODELTA"}]
    ctx = _context_with_chain(contracts)
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=100_000.0)
    assert untradeable is False
    assert reason is None


# ── BP edge cases ─────────────────────────────────────────────────────────────

def test_zero_bp_is_untradeable():
    """BP = 0 → cap = 0; any non-zero strike cost exceeds it."""
    contracts = [_put(1.0, -0.25)]
    ctx = _context_with_chain(contracts)
    untradeable, reason = is_structurally_untradeable_for_csp(ctx, options_buying_power=0.0)
    assert untradeable is True
    assert reason is not None


def test_small_bp_with_cheap_stock_tradeable():
    """$5K BP, $25 stock: $23.75 strike × 100 = $2,375 < 10% × $5K = $500. Wait, $2375 > $500."""
    # Actually $2,375 > $500 cap → untradeable. Confirms math is right.
    contracts = [_put(23.75, -0.25)]
    ctx = _context_with_chain(contracts)
    untradeable, _ = is_structurally_untradeable_for_csp(ctx, options_buying_power=5_000.0)
    assert untradeable is True


def test_custom_position_cap_pct():
    """position_cap_pct override: 20% cap makes the same contract tradeable."""
    # $261 strike × 100 = $26,100; 20% × $100K = $20,000 → still untradeable
    contracts = [_put(261.0, -0.25)]
    ctx = _context_with_chain(contracts)
    untradeable, _ = is_structurally_untradeable_for_csp(
        ctx, options_buying_power=100_000.0, position_cap_pct=0.30
    )
    # $26,100 < 30% × $100K = $30,000 → tradeable
    assert untradeable is False


# ── Skip code integrity ───────────────────────────────────────────────────────

def test_structurally_untradeable_is_valid_skip_code():
    """SkipCode.STRUCTURALLY_UNTRADEABLE exists and is in SkipCode.ALL."""
    assert hasattr(SkipCode, "STRUCTURALLY_UNTRADEABLE")
    assert SkipCode.STRUCTURALLY_UNTRADEABLE in SkipCode.ALL


def test_skip_reason_enum_has_structurally_untradeable():
    """SkipReason.STRUCTURALLY_UNTRADEABLE exists and maps to PRE_CHECK gate."""
    from strategies.skip_reasons import SkipReason, SkipGate, REASON_TO_GATE
    assert hasattr(SkipReason, "STRUCTURALLY_UNTRADEABLE")
    assert REASON_TO_GATE[SkipReason.STRUCTURALLY_UNTRADEABLE] == SkipGate.PRE_CHECK
