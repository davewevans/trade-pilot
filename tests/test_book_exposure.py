"""Unit tests for data/book_exposure.py."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from data.book_exposure import check_anti_crowding, compute_cross_account_book_exposure


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_wheel_state(tmp_path: Path, symbol: str, state: str) -> Path:
    p = tmp_path / "wheel_state.json"
    p.write_text(json.dumps({
        "symbol": symbol,
        "state": state,
        "open_position": None,
        "cost_basis": None,
        "total_premium_collected": 0.0,
        "roll_count": 0,
    }), encoding="utf-8")
    return p


def _make_tw_state(tmp_path: Path, symbol: str, state: str) -> Path:
    p = tmp_path / "turnover_wheel_state.json"
    p.write_text(json.dumps({
        "symbol": symbol,
        "state": state,
        "open_position": None,
        "cost_basis": None,
        "total_premium_collected": 0.0,
        "roll_count": 0,
    }), encoding="utf-8")
    return p


def _fake_tracker(spreads: list[dict]):
    """Return a SpreadTracker mock whose get_active_spreads() returns spreads."""
    tracker = MagicMock()
    tracker.get_active_spreads.return_value = spreads
    return tracker


def _patch_settings(tmp_path: Path):
    """Return a patch for config.settings with SNAPSHOTS_DIR = tmp_path."""
    from config import settings as real_settings
    mock = MagicMock(wraps=real_settings)
    mock.SNAPSHOTS_DIR = tmp_path
    mock.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED = True
    mock.DIRECTIONAL_FAMILY_MAP = real_settings.DIRECTIONAL_FAMILY_MAP
    return mock


# ── compute_cross_account_book_exposure ──────────────────────────────────────

class TestComputeBookExposure:

    def test_empty_state_no_files(self, tmp_path):
        """All state files absent → empty book, no exception."""
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            result = compute_cross_account_book_exposure()
        assert result["by_underlying"] == {}
        assert "computed_at" in result
        assert result["sources"]["wheel_state"] is None
        assert result["sources"]["turnover_wheel_state"] is None

    def test_wheel_short_put_only(self, tmp_path):
        """Wheel SHORT_PUT on AAPL → short_put family populated."""
        _make_wheel_state(tmp_path, "AAPL", "SHORT_PUT")
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            result = compute_cross_account_book_exposure()
        assert "AAPL" in result["by_underlying"]
        aapl = result["by_underlying"]["AAPL"]
        assert "wheel" in aapl["families"]["short_put"]
        assert aapl["families"]["short_call"] == []

    def test_iron_condor_both_families(self, tmp_path):
        """Iron condor → both short_put AND short_call populated."""
        spreads = [{"strategy_type": "iron_condor", "underlying": "SPY", "status": "open"}]
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker(spreads)):
            result = compute_cross_account_book_exposure()
        spy = result["by_underlying"]["SPY"]
        assert "iron_condor" in spy["families"]["short_put"]
        assert "iron_condor" in spy["families"]["short_call"]

    def test_wheel_and_turnover_wheel_both_short_put(self, tmp_path):
        """Wheel + Turnover Wheel SHORT_PUT on same symbol → both appear in short_put."""
        _make_wheel_state(tmp_path, "AAPL", "SHORT_PUT")
        _make_tw_state(tmp_path, "AAPL", "SHORT_PUT")
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            result = compute_cross_account_book_exposure()
        aapl = result["by_underlying"]["AAPL"]
        assert "wheel" in aapl["families"]["short_put"]
        assert "turnover_wheel" in aapl["families"]["short_put"]

    def test_long_stock_excluded_from_short_put_family(self, tmp_path):
        """Wheel LONG_STOCK → NOT in short_put family (families list is empty)."""
        _make_wheel_state(tmp_path, "AAPL", "LONG_STOCK")
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            result = compute_cross_account_book_exposure()
        aapl = result["by_underlying"]["AAPL"]
        # Position is visible but contributes to no family
        assert len(aapl["positions"]) == 1
        assert aapl["positions"][0]["families"] == []
        assert aapl["families"]["short_put"] == []

    def test_long_stock_excluded_from_short_call_family(self, tmp_path):
        """Wheel LONG_STOCK → NOT in short_call family either."""
        _make_wheel_state(tmp_path, "AAPL", "LONG_STOCK")
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            result = compute_cross_account_book_exposure()
        aapl = result["by_underlying"]["AAPL"]
        assert aapl["families"]["short_call"] == []

    def test_spread_pending_open_counted(self, tmp_path):
        """PENDING_OPEN status on a spread is included in active spreads."""
        spreads = [{"strategy_type": "bull_put_spread", "underlying": "MSFT",
                    "status": "pending_open"}]
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker(spreads)):
            result = compute_cross_account_book_exposure()
        assert "MSFT" in result["by_underlying"]
        assert "bull_put_spread" in result["by_underlying"]["MSFT"]["families"]["short_put"]

    def test_file_not_found_no_exception(self, tmp_path):
        """All state files absent → returns empty book without raising."""
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            result = compute_cross_account_book_exposure()
        assert result["by_underlying"] == {}

    def test_unknown_strategy_type_in_spreads_skipped(self, tmp_path):
        """Unknown strategy_type in open_spreads → logged warning, no exception."""
        spreads = [{"strategy_type": "future_strategy_xyz", "underlying": "AAPL",
                    "status": "open"}]
        with patch("data.book_exposure.settings", _patch_settings(tmp_path)), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker(spreads)):
            result = compute_cross_account_book_exposure()
        # AAPL not added because strategy_type is unknown
        assert "AAPL" not in result["by_underlying"]


# ── check_anti_crowding ───────────────────────────────────────────────────────

class TestCheckAntiCrowding:

    def _book_with_wheel_short_put(self) -> dict:
        return {
            "by_underlying": {
                "AAPL": {
                    "positions": [
                        {"strategy_type": "wheel", "state": "SHORT_PUT",
                         "families": ["short_put"]}
                    ],
                    "families": {"short_put": ["wheel"], "short_call": [],
                                 "long_directional": []},
                }
            }
        }

    def test_peer_exemption_wheel_and_turnover_wheel(self):
        """Wheel + Turnover Wheel on same underlying → allowed (peer exception)."""
        book = self._book_with_wheel_short_put()
        allowed, reason = check_anti_crowding("AAPL", "turnover_wheel", book=book)
        assert allowed is True
        assert reason == ""

    def test_wheel_blocked_by_bull_put_spread(self):
        """Wheel CSP on AAPL when bull_put_spread OPEN → blocked."""
        book = {
            "by_underlying": {
                "AAPL": {
                    "positions": [{"strategy_type": "bull_put_spread",
                                   "state": "OPEN", "families": ["short_put"]}],
                    "families": {"short_put": ["bull_put_spread"], "short_call": [],
                                 "long_directional": []},
                }
            }
        }
        allowed, reason = check_anti_crowding("AAPL", "wheel", book=book)
        assert allowed is False
        assert "bull_put_spread" in reason
        assert "AAPL" in reason
        assert "short_put" in reason

    def test_kill_switch_off_always_allowed(self):
        """CROSS_ACCOUNT_ANTI_CROWDING_ENABLED=False → always allowed (ANTI_CROWDING_CROSS_ACCOUNT gate bypassed)."""
        book = {
            "by_underlying": {
                "AAPL": {
                    "positions": [{"strategy_type": "iron_condor",
                                   "state": "OPEN", "families": ["short_put", "short_call"]}],
                    "families": {"short_put": ["iron_condor"], "short_call": ["iron_condor"],
                                 "long_directional": []},
                }
            }
        }
        from config import settings as real_settings
        mock = MagicMock(wraps=real_settings)
        mock.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED = False
        mock.DIRECTIONAL_FAMILY_MAP = real_settings.DIRECTIONAL_FAMILY_MAP
        with patch("data.book_exposure.settings", mock):
            allowed, reason = check_anti_crowding("AAPL", "wheel", book=book)
        assert allowed is True
        assert reason == ""

    def test_unknown_strategy_raises_value_error(self):
        """Unknown strategy_type raises ValueError (strict guard)."""
        with pytest.raises(ValueError, match="adaptive_spreads"):
            check_anti_crowding("AAPL", "adaptive_spreads")

    def test_unknown_strategy_raises_value_error_arbitrary(self):
        """Any string not in DIRECTIONAL_FAMILY_MAP raises ValueError."""
        with pytest.raises(ValueError):
            check_anti_crowding("AAPL", "not_a_real_strategy_type")

    def test_calendar_spread_always_allowed(self):
        """calendar_spread has empty families → never blocked, never blocks."""
        book = {
            "by_underlying": {
                "AAPL": {
                    "positions": [{"strategy_type": "iron_condor",
                                   "state": "OPEN", "families": ["short_put", "short_call"]}],
                    "families": {"short_put": ["iron_condor"], "short_call": ["iron_condor"],
                                 "long_directional": []},
                }
            }
        }
        allowed, reason = check_anti_crowding("AAPL", "calendar_spread", book=book)
        assert allowed is True
        assert reason == ""

    def test_long_stock_does_not_block_bull_put_spread(self, tmp_path):
        """Wheel LONG_STOCK on AAPL → bull_put_spread is not blocked (no option family)."""
        _make_wheel_state(tmp_path, "AAPL", "LONG_STOCK")
        mock_settings = _patch_settings(tmp_path)
        with patch("data.book_exposure.settings", mock_settings), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            book = compute_cross_account_book_exposure()

        with patch("data.book_exposure.settings", mock_settings):
            allowed, reason = check_anti_crowding("AAPL", "bull_put_spread", book=book)
        assert allowed is True
        assert reason == ""

    def test_long_stock_does_not_block_bear_call_spread(self, tmp_path):
        """Wheel LONG_STOCK on AAPL → bear_call_spread is not blocked."""
        _make_wheel_state(tmp_path, "AAPL", "LONG_STOCK")
        mock_settings = _patch_settings(tmp_path)
        with patch("data.book_exposure.settings", mock_settings), \
             patch("data.spread_tracker.SpreadTracker", return_value=_fake_tracker([])):
            book = compute_cross_account_book_exposure()

        with patch("data.book_exposure.settings", mock_settings):
            allowed, reason = check_anti_crowding("AAPL", "bear_call_spread", book=book)
        assert allowed is True
        assert reason == ""

    def test_no_position_on_underlying(self):
        """No existing position on this underlying → always allowed."""
        book = {"by_underlying": {}}
        allowed, reason = check_anti_crowding("TSLA", "wheel", book=book)
        assert allowed is True
        assert reason == ""

    def test_wheel_short_put_and_bull_put_spread_blocks_new_wheel(self):
        """Existing wheel SHORT_PUT + bull_put_spread on AAPL blocks a second wheel entry."""
        book = {
            "by_underlying": {
                "AAPL": {
                    "families": {
                        "short_put": ["wheel", "bull_put_spread"],
                        "short_call": [],
                        "long_directional": [],
                    }
                }
            }
        }
        # A new bull_put_spread entry on AAPL would be blocked by existing wheel
        allowed, reason = check_anti_crowding("AAPL", "bull_put_spread", book=book)
        assert allowed is False
        assert "wheel" in reason
