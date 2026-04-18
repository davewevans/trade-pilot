"""Unit tests for compute_portfolio_greeks().

Sign convention documented in context_builder.compute_portfolio_greeks:
  - Alpaca returns buyer-perspective Greeks (put delta negative, call delta positive).
  - Signed qty: positive for long positions, negative for short positions.
  - Dollar delta contribution = delta * signed_qty * 100
  - Short put (side="short", qty=1, delta=-0.25):
      signed_qty = -1  →  contribution = -0.25 * -1 * 100 = +25

Tests:
  1. Empty portfolio (no snapshot file) → all zeros, no divide-by-zero.
  2. Sign convention: short put with delta -0.25 → +25 net_delta.
  3. DTE bucketing at the exact boundary values (7, 21, 45, 46 days).
  4. Fallback counting: position with None Greeks → 0 contribution,
     contracts_from_fallback_source incremented.
  5. max_skew_seconds computed from snapshot timestamp age.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure repo root is on sys.path so imports resolve
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.context_builder import compute_portfolio_greeks, _dte_bucket


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_snapshot(tmp_path: Path, positions: list[dict], open_spreads: list[dict] | None = None, ts: str | None = None) -> Path:
    """Write a minimal portfolio.json snapshot to tmp_path."""
    snap = {
        "timestamp": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "greeks_fetched_at": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": {"total_equity": 100000.0, "buying_power": 50000.0},
        "positions": positions,
        "wheel_states": {},
        "open_spreads": open_spreads or [],
    }
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    return path


def _call_with_snapshot(snap_dir: Path, account_id=None) -> dict:
    """Call compute_portfolio_greeks with SNAPSHOTS_DIR pointed at snap_dir."""
    class _FakeSettings:
        SNAPSHOTS_DIR = snap_dir

    with patch("data.context_builder.settings", _FakeSettings()):
        return compute_portfolio_greeks(account_id=account_id)


# ── Test 1: Empty portfolio ───────────────────────────────────────────────────

class TestEmptyPortfolio:
    def test_missing_snapshot_returns_zeros(self, tmp_path):
        """No portfolio.json → all aggregates are 0, no exception."""
        # Patch settings to point at an empty directory
        class _FakeSettings:
            SNAPSHOTS_DIR = tmp_path

        with patch("data.context_builder.settings", _FakeSettings()):
            result = compute_portfolio_greeks()

        assert result["net_delta"] == 0.0
        assert result["net_theta"] == 0.0
        assert result["net_vega"] == 0.0
        assert result["net_gamma"] == 0.0
        assert result["total_defined_risk_usd"] == 0.0
        assert result["position_count"] == 0
        assert result["freshness"]["contracts_from_fallback_source"] == 0
        # computed_at must be a parseable ISO timestamp
        datetime.fromisoformat(result["freshness"]["computed_at"])

    def test_empty_positions_list_returns_zeros(self, tmp_path):
        """portfolio.json exists but has no positions → all zeros."""
        _write_snapshot(tmp_path, positions=[])
        result = _call_with_snapshot(tmp_path)

        assert result["net_delta"] == 0.0
        assert result["position_count"] == 0
        assert result["by_dte_bucket"]["0_7"]["position_count"] == 0


# ── Test 2: Sign convention ───────────────────────────────────────────────────

class TestSignConvention:
    def test_short_put_contributes_positive_delta(self, tmp_path):
        """
        A short cash-secured put:
          delta = -0.25 (buyer's perspective: put loses 25 cents per $1 rise)
          qty   = 1     (Alpaca reports positive quantity)
          side  = "short"
        Expected net_delta contribution = -0.25 * -1 * 100 = +25.0
        """
        positions = [
            {
                "symbol":        "SPY240119P00580000",
                "underlying":    "SPY",
                "strategy_type": "wheel",
                "expiration":    "2024-01-19",
                "dte":           25,
                "quantity":      1,
                "side":          "short",
                "delta":         -0.25,
                "theta":         -0.05,
                "vega":           0.10,
                "gamma":          0.005,
            }
        ]
        _write_snapshot(tmp_path, positions)
        result = _call_with_snapshot(tmp_path)

        assert result["net_delta"] == pytest.approx(25.0, abs=0.01)
        # theta: -0.05 * -1 * 100 = +5.0 (seller earns $5/day from decay)
        assert result["net_theta"] == pytest.approx(5.0, abs=0.01)
        # vega: 0.10 * -1 * 100 = -10.0 (seller loses $10 per 1% IV rise)
        assert result["net_vega"] == pytest.approx(-10.0, abs=0.01)
        assert result["position_count"] == 1

    def test_long_stock_equity_contributes_shares_as_delta(self, tmp_path):
        """
        LONG_STOCK (100 shares, no expiration):
          signed_qty = +100 (long)
          delta contribution = 100 (dollars per $1 move)
        """
        positions = [
            {
                "symbol":        "SPY",
                "underlying":    "SPY",
                "strategy_type": "wheel",
                "expiration":    None,  # equity — no expiration
                "dte":           None,
                "quantity":      100,
                "side":          "long",
                "delta":         None,  # Greeks not meaningful for equity
                "theta":         None,
                "vega":          None,
                "gamma":         None,
            }
        ]
        _write_snapshot(tmp_path, positions)
        result = _call_with_snapshot(tmp_path)

        assert result["net_delta"] == pytest.approx(100.0, abs=0.01)
        assert result["net_theta"] == 0.0
        assert result["net_vega"] == 0.0
        # Equity does NOT go into DTE buckets
        total_bucket_count = sum(
            b["position_count"] for b in result["by_dte_bucket"].values()
        )
        assert total_bucket_count == 0
        # Equity with None Greeks does NOT count as fallback
        assert result["freshness"]["contracts_from_fallback_source"] == 0


# ── Test 3: DTE bucketing at boundaries ──────────────────────────────────────

class TestDteBucketing:
    @pytest.mark.parametrize("dte,expected_bucket", [
        (0,   "0_7"),
        (7,   "0_7"),    # boundary: inclusive upper edge of 0_7
        (8,   "8_21"),   # boundary: first day of 8_21
        (21,  "8_21"),   # boundary: inclusive upper edge of 8_21
        (22,  "22_45"),  # boundary: first day of 22_45
        (45,  "22_45"),  # boundary: inclusive upper edge of 22_45
        (46,  "46_plus"), # boundary: first day of 46_plus
        (100, "46_plus"),
        (None, "46_plus"),  # None → fallback to 46_plus
    ])
    def test_dte_bucket_assignment(self, dte, expected_bucket):
        assert _dte_bucket(dte) == expected_bucket

    def test_bucket_counts_with_positions(self, tmp_path):
        """Four positions in different DTE buckets land in the right buckets."""
        positions = [
            {"symbol": "A1", "underlying": "SPY", "strategy_type": "wheel",
             "expiration": "2024-01-10", "dte": 7, "quantity": 1, "side": "short",
             "delta": -0.20, "theta": -0.03, "vega": 0.08, "gamma": 0.003},
            {"symbol": "A2", "underlying": "SPY", "strategy_type": "wheel",
             "expiration": "2024-01-24", "dte": 21, "quantity": 1, "side": "short",
             "delta": -0.20, "theta": -0.03, "vega": 0.08, "gamma": 0.003},
            {"symbol": "A3", "underlying": "SPY", "strategy_type": "wheel",
             "expiration": "2024-02-07", "dte": 45, "quantity": 1, "side": "short",
             "delta": -0.20, "theta": -0.03, "vega": 0.08, "gamma": 0.003},
            {"symbol": "A4", "underlying": "SPY", "strategy_type": "wheel",
             "expiration": "2024-02-22", "dte": 46, "quantity": 1, "side": "short",
             "delta": -0.20, "theta": -0.03, "vega": 0.08, "gamma": 0.003},
        ]
        _write_snapshot(tmp_path, positions)
        result = _call_with_snapshot(tmp_path)

        assert result["by_dte_bucket"]["0_7"]["position_count"] == 1
        assert result["by_dte_bucket"]["8_21"]["position_count"] == 1
        assert result["by_dte_bucket"]["22_45"]["position_count"] == 1
        assert result["by_dte_bucket"]["46_plus"]["position_count"] == 1


# ── Test 4: Fallback counting ─────────────────────────────────────────────────

class TestFallbackCounting:
    def test_position_with_no_greeks_contributes_zero_and_increments_counter(self, tmp_path):
        """
        A position where all Greeks are None (Alpaca snapshot wasn't available):
        - Net delta/theta/vega/gamma contribution = 0
        - contracts_from_fallback_source += 1
        """
        positions = [
            # This position has Greeks → normal
            {"symbol": "SPY240119P00580000", "underlying": "SPY", "strategy_type": "wheel",
             "expiration": "2024-01-19", "dte": 25, "quantity": 1, "side": "short",
             "delta": -0.25, "theta": -0.05, "vega": 0.10, "gamma": 0.005},
            # This position has no Greeks (snapshot missed) → fallback
            {"symbol": "QQQ240119P00450000", "underlying": "QQQ", "strategy_type": "wheel",
             "expiration": "2024-01-19", "dte": 25, "quantity": 1, "side": "short",
             "delta": None, "theta": None, "vega": None, "gamma": None},
        ]
        _write_snapshot(tmp_path, positions)
        result = _call_with_snapshot(tmp_path)

        # Only the first position contributes Greeks
        assert result["net_delta"] == pytest.approx(25.0, abs=0.01)
        assert result["net_theta"] == pytest.approx(5.0, abs=0.01)
        assert result["position_count"] == 2
        # Exactly one fallback
        assert result["freshness"]["contracts_from_fallback_source"] == 1

    def test_all_missing_greeks_returns_zeros_not_error(self, tmp_path):
        """If ALL positions have no Greeks → zeros, fallback count = N."""
        positions = [
            {"symbol": "A", "underlying": "SPY", "strategy_type": "wheel",
             "expiration": "2024-01-19", "dte": 25, "quantity": 1, "side": "short",
             "delta": None, "theta": None, "vega": None, "gamma": None},
            {"symbol": "B", "underlying": "QQQ", "strategy_type": "wheel",
             "expiration": "2024-01-19", "dte": 25, "quantity": 1, "side": "short",
             "delta": None, "theta": None, "vega": None, "gamma": None},
        ]
        _write_snapshot(tmp_path, positions)
        result = _call_with_snapshot(tmp_path)

        assert result["net_delta"] == 0.0
        assert result["net_theta"] == 0.0
        assert result["freshness"]["contracts_from_fallback_source"] == 2


# ── Test 5: max_skew_seconds / freshness ─────────────────────────────────────

class TestFreshness:
    def test_age_computed_from_greeks_fetched_at(self, tmp_path):
        """
        greeks_fetched_at 120 seconds in the past → age ≈ 120 seconds.
        """
        ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(timespec="seconds")
        _write_snapshot(tmp_path, positions=[], ts=ts)
        result = _call_with_snapshot(tmp_path)

        age = result["freshness"]["oldest_contract_age_seconds"]
        # Allow ±5s for test execution time
        assert 115 <= age <= 125, f"Expected age ~120s, got {age}"
        assert result["freshness"]["newest_contract_age_seconds"] == age

    def test_max_skew_is_zero_for_single_snapshot(self, tmp_path):
        """
        All data from one snapshot → max_skew_seconds == 0 (no inter-leg age
        difference when all Greeks come from the same portfolio_refresh run).
        """
        _write_snapshot(tmp_path, positions=[])
        result = _call_with_snapshot(tmp_path)
        assert result["freshness"]["max_skew_seconds"] == 0
