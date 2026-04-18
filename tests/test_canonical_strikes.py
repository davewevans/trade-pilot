"""Tests for canonical /strikes cache keys (Phase 14).

Covers:
1. Two call sites with different narrow ranges for the same (symbol, side)
   make only ONE ORATS call (shared canonical cache entry).
2. Filtered results match what a narrow-range call would have returned.
3. filter_strikes() handles empty results and missing fields.
4. Canonical range is a strict superset of all known call-site ranges.
5. Put deltas (negative from ORATS) are filtered correctly by magnitude.
"""

import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strike(delta: float, dte: int, strike: float = 100.0) -> dict:
    """Return a minimal strike dict matching the shape returned by get_strikes_by_delta."""
    return {
        "strike": strike,
        "expiration_date": "2026-05-16",
        "dte": dte,
        "delta": delta,
        "theta": -0.01,
        "vega": 0.05,
        "gamma": 0.01,
        "smv_vol": 0.25,
        "bid_price": 1.0,
        "ask_price": 1.1,
        "mid_price": 1.05,
        "open_interest": 500,
        "opt_value": 1.0,
    }


def _canonical_put_rows() -> list[dict]:
    """A realistic canonical put strike set (delta 0.10–0.70, DTE 14–60)."""
    return [
        _make_strike(delta=-0.12, dte=14, strike=95.0),
        _make_strike(delta=-0.18, dte=21, strike=97.0),
        _make_strike(delta=-0.22, dte=21, strike=98.0),
        _make_strike(delta=-0.28, dte=28, strike=99.0),
        _make_strike(delta=-0.31, dte=35, strike=100.0),
        _make_strike(delta=-0.55, dte=50, strike=103.0),
        _make_strike(delta=-0.65, dte=60, strike=105.0),
    ]


# ---------------------------------------------------------------------------
# Test 1: Two different narrow ranges → only ONE ORATS call
# ---------------------------------------------------------------------------

def test_two_callers_same_symbol_side_make_one_orats_call():
    """
    Wheel (put 0.20–0.30 / DTE 21–35) and bull_put_spread (put 0.15–0.30 / DTE 20–50)
    for the same symbol should result in exactly one ORATS /strikes HTTP call,
    with the second call served entirely from cache.

    We patch the module-level _cache singleton directly so we control cache hits/misses
    without touching the real SQLite database.
    """
    import data.orats_client as mod
    from data.orats_client import ORATSClient

    canonical_rows = _canonical_put_rows()
    # Build the raw ORATS response rows
    raw_rows = [
        {
            "strike": r["strike"],
            "expirDate": r["expiration_date"],
            "dte": r["dte"],
            "delta": r["delta"],
            "theta": r["theta"],
            "vega": r["vega"],
            "gamma": r["gamma"],
            "smvVol": r["smv_vol"],
            "putBidPrice": r["bid_price"],
            "putAskPrice": r["ask_price"],
            "putOpenInterest": r["open_interest"],
            "putValue": r["opt_value"],
            "callBidPrice": None,
            "callAskPrice": None,
            "callOpenInterest": None,
            "callValue": None,
        }
        for r in canonical_rows
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": raw_rows}
    mock_resp.raise_for_status = MagicMock()

    http_call_count = 0

    def _mock_get(url, params=None, timeout=None):
        nonlocal http_call_count
        if "/strikes" in url:
            http_call_count += 1
        return mock_resp

    # In-memory cache: first call misses → stores canonical; second call hits.
    _mem_cache: dict[tuple, object] = {}

    def _cache_get(endpoint, cache_key, ttl):
        return _mem_cache.get((endpoint, cache_key))

    def _cache_set(endpoint, cache_key, data, ttl):
        _mem_cache[(endpoint, cache_key)] = data

    mock_cache = MagicMock()
    mock_cache.get.side_effect = _cache_get
    mock_cache.set.side_effect = _cache_set

    ledger = MagicMock()

    with (
        patch.object(mod, "_cache", mock_cache),
        patch("data.orats_client.requests.get", side_effect=_mock_get),
        patch("data.api_ledger.get_ledger", return_value=ledger),
    ):
        client = ORATSClient(api_key="test_key")

        # First caller: wheel puts (narrow range)
        result1 = client.get_strikes_by_delta("AAPL", "put", 0.20, 0.30, 21, 35)
        # Second caller: bull_put_spread puts (different narrow range, same symbol/side)
        result2 = client.get_strikes_by_delta("AAPL", "put", 0.15, 0.30, 20, 50)

    assert http_call_count == 1, (
        f"Expected exactly 1 ORATS HTTP call; got {http_call_count}. "
        "Second caller should have hit the canonical cache."
    )

    # Results should be appropriately filtered to each caller's range
    for row in result1:
        assert 0.20 <= abs(row["delta"]) <= 0.30, f"delta {row['delta']} out of wheel range"
        assert 21 <= row["dte"] <= 35, f"dte {row['dte']} out of wheel range"

    for row in result2:
        assert 0.15 <= abs(row["delta"]) <= 0.30, f"delta {row['delta']} out of spread range"
        assert 20 <= row["dte"] <= 50, f"dte {row['dte']} out of spread range"


# ---------------------------------------------------------------------------
# Test 2: Filtered results match narrow-range call expectations
# ---------------------------------------------------------------------------

def test_filter_strips_out_of_range_strikes():
    """filter_strikes returns only rows within the requested delta and DTE range."""
    from data.orats_client import filter_strikes

    strikes = _canonical_put_rows()  # delta 0.12–0.65, dte 14–60

    # Request narrow wheel range: put 0.20–0.30, dte 21–35
    filtered = filter_strikes(strikes, 0.20, 0.30, 21, 35)

    for row in filtered:
        assert 0.20 <= abs(row["delta"]) <= 0.30, f"delta {row['delta']} out of range"
        assert 21 <= row["dte"] <= 35, f"dte {row['dte']} out of range"

    # 0.22/21, 0.28/28, 0.31/35 — 0.31 is just over 0.30 so excluded
    assert len(filtered) == 2  # delta=-0.22/dte=21, delta=-0.28/dte=28


# ---------------------------------------------------------------------------
# Test 3: filter_strikes handles edge cases
# ---------------------------------------------------------------------------

def test_filter_strikes_empty_input():
    """filter_strikes returns an empty list for empty input."""
    from data.orats_client import filter_strikes
    assert filter_strikes([], 0.10, 0.70, 14, 60) == []


def test_filter_strikes_missing_delta_field():
    """Rows with missing delta are included (no data = pass-through)."""
    from data.orats_client import filter_strikes

    rows = [
        {"strike": 100.0, "dte": 21, "delta": None},
        {"strike": 101.0, "dte": 21},  # no delta key at all
    ]
    result = filter_strikes(rows, 0.20, 0.30, 14, 60)
    assert len(result) == 2


def test_filter_strikes_missing_dte_field():
    """Rows with missing dte are included (no data = pass-through)."""
    from data.orats_client import filter_strikes

    rows = [
        {"strike": 100.0, "delta": -0.25, "dte": None},
        {"strike": 101.0, "delta": -0.25},  # no dte key at all
    ]
    result = filter_strikes(rows, 0.20, 0.30, 14, 60)
    assert len(result) == 2


def test_filter_strikes_positive_call_deltas():
    """Positive call deltas are filtered correctly by magnitude."""
    from data.orats_client import filter_strikes

    rows = [
        _make_strike(delta=0.15, dte=21),   # just below min → excluded
        _make_strike(delta=0.20, dte=21),   # on boundary → included
        _make_strike(delta=0.35, dte=30),   # within range → included
        _make_strike(delta=0.55, dte=40),   # above max → excluded
    ]
    result = filter_strikes(rows, 0.20, 0.40, 14, 60)
    assert len(result) == 2
    deltas = [r["delta"] for r in result]
    assert 0.20 in deltas
    assert 0.35 in deltas


# ---------------------------------------------------------------------------
# Test 4: Canonical range is strict superset of all known call-site ranges
# ---------------------------------------------------------------------------

def test_canonical_range_is_superset_of_all_call_sites():
    """The canonical parameters must cover every known call-site range."""
    from data.orats_client import (
        _CANONICAL_STRIKES_DELTA_MIN,
        _CANONICAL_STRIKES_DELTA_MAX,
        _CANONICAL_STRIKES_DTE_MIN,
        _CANONICAL_STRIKES_DTE_MAX,
    )

    # All known call sites (from grep on context_builder.py)
    call_sites = [
        # (delta_min, delta_max, dte_min, dte_max, description)
        (0.15, 0.65, 21, 45, "spread chain option enrich"),
        (0.15, 0.30, 20, 50, "bull_put_spread / bear_call_spread build"),
        (0.15, 0.20, 30, 50, "iron_condor build"),
        (0.20, 0.30, 21, 35, "wheel put (CSP)"),
        (0.20, 0.35, 21, 35, "wheel call (CC)"),
    ]

    for delta_min, delta_max, dte_min, dte_max, desc in call_sites:
        assert _CANONICAL_STRIKES_DELTA_MIN <= delta_min, (
            f"Canonical delta_min ({_CANONICAL_STRIKES_DELTA_MIN}) > call-site min "
            f"({delta_min}) for {desc}"
        )
        assert _CANONICAL_STRIKES_DELTA_MAX >= delta_max, (
            f"Canonical delta_max ({_CANONICAL_STRIKES_DELTA_MAX}) < call-site max "
            f"({delta_max}) for {desc}"
        )
        assert _CANONICAL_STRIKES_DTE_MIN <= dte_min, (
            f"Canonical dte_min ({_CANONICAL_STRIKES_DTE_MIN}) > call-site min "
            f"({dte_min}) for {desc}"
        )
        assert _CANONICAL_STRIKES_DTE_MAX >= dte_max, (
            f"Canonical dte_max ({_CANONICAL_STRIKES_DTE_MAX}) < call-site max "
            f"({dte_max}) for {desc}"
        )


# ---------------------------------------------------------------------------
# Test 5: Put deltas (negative from ORATS) filtered by magnitude
# ---------------------------------------------------------------------------

def test_negative_put_deltas_filtered_by_magnitude():
    """filter_strikes applies abs() to delta so put rows (negative) filter correctly."""
    from data.orats_client import filter_strikes

    # Simulate what ORATS returns for puts: negative deltas
    rows = [
        _make_strike(delta=-0.25, dte=28),  # magnitude 0.25 → in range 0.20–0.30
        _make_strike(delta=-0.10, dte=28),  # magnitude 0.10 → below min 0.20 → excluded
        _make_strike(delta=-0.45, dte=28),  # magnitude 0.45 → above max 0.30 → excluded
    ]

    result = filter_strikes(rows, 0.20, 0.30, 14, 60)
    assert len(result) == 1
    assert result[0]["delta"] == -0.25
