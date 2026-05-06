"""Regression tests for compute_ev_score and the monies_rows upstream guard.

Covers Sentry event 73113a69 (2026-05-06): AttributeError 'str' object has no
attribute 'get' when a stale cache entry stored the raw API envelope dict instead
of a normalized list, causing dict-key iteration (strings) in compute_ev_score.
"""

from data.context_builder import compute_ev_score

_SAMPLE_CANDIDATE = {
    "short_leg": {"delta": -0.30},
    "max_gain": 150.0,
    "max_loss": 350.0,
    "dte": 21,
}

_SAMPLE_SUMMARY = {
    "atm_iv_m1": 0.25,
    "implied_move_pct": 0.04,
    "forecast_move_pct": 0.03,
}

_SAMPLE_MONIES = [
    {
        "ticker": "VZ",
        "expir_date": "2026-05-30",
        "vol5": 0.20, "vol10": 0.21, "vol15": 0.22, "vol20": 0.23,
        "vol25": 0.24, "vol30": 0.25, "vol35": 0.26, "vol40": 0.27,
        "vol45": 0.28, "vol50": 0.29, "vol55": 0.30, "vol60": 0.31,
        "vol65": 0.32, "vol70": 0.33, "vol75": 0.34, "vol80": 0.35,
        "vol85": 0.36, "vol90": 0.37, "vol95": 0.38, "vol100": 0.39,
    }
]


# ── Happy-path tests ──────────────────────────────────────────────────────────


def test_normal_monies_rows_returns_ev_score():
    result = compute_ev_score(_SAMPLE_CANDIDATE, "put", _SAMPLE_SUMMARY, _SAMPLE_MONIES)
    assert "ev_score" in result
    assert result["ev_score"] is not None
    assert isinstance(result["ev_score"], float)


def test_empty_monies_rows_returns_base_pop():
    result = compute_ev_score(_SAMPLE_CANDIDATE, "put", _SAMPLE_SUMMARY, [])
    assert "base_pop" in result
    assert result["base_pop"] == pytest.approx(0.70, abs=0.01)
    assert result["ev_score"] is not None


# ── Regression: non-dict rows ─────────────────────────────────────────────────


def test_string_entry_in_monies_rows_does_not_raise():
    """Stale cache: monies_rows=[str, dict] — the string row is skipped."""
    mixed = ["data", _SAMPLE_MONIES[0]]  # string entry (stale cache) + valid dict
    result = compute_ev_score(_SAMPLE_CANDIDATE, "put", _SAMPLE_SUMMARY, mixed)
    assert "ev_score" in result
    # Valid dict row was found, so ev_score is not None
    assert result["ev_score"] is not None


def test_all_string_monies_rows_does_not_raise():
    """All rows are strings (completely stale cache) — falls back to base_pop."""
    strings_only = ["data", "errors", "meta"]
    result = compute_ev_score(_SAMPLE_CANDIDATE, "put", _SAMPLE_SUMMARY, strings_only)
    assert "ev_score" in result
    # No valid row found, ev_score computed from base_pop only
    assert isinstance(result["ev_score"], float)


def test_empty_monies_rows_no_exception():
    result = compute_ev_score(_SAMPLE_CANDIDATE, "put", _SAMPLE_SUMMARY, [])
    assert result["base_pop"] is not None


def test_degenerate_candidate_returns_none_ev():
    bad = {"short_leg": {"delta": 0}, "max_gain": 0, "max_loss": 0, "dte": 30}
    result = compute_ev_score(bad, "put", _SAMPLE_SUMMARY, _SAMPLE_MONIES)
    assert result["ev_score"] is None


# ── import guard for pytest.approx ───────────────────────────────────────────
import pytest
