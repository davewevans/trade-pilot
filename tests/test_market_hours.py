"""Tests for utils.market_hours."""

from datetime import datetime, time as dt_time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from utils.market_hours import is_options_order_allowed, parse_dte_from_occ

_ET = ZoneInfo("America/New_York")


def _mock_now(hour, minute=0):
    """Return a patcher that fixes datetime.now to a given ET time."""
    dt = datetime(2026, 4, 13, hour, minute, tzinfo=_ET)  # Monday
    return patch("utils.market_hours.datetime", wraps=datetime,
                 **{"now.return_value": dt})


class TestIsOptionsOrderAllowed:
    def test_allowed_during_market_hours(self):
        with _mock_now(10, 30):
            ok, reason = is_options_order_allowed("SPY")
        assert ok is True
        assert reason == ""

    def test_blocked_before_open(self):
        with _mock_now(9, 0):
            ok, reason = is_options_order_allowed("SPY")
        assert ok is False
        assert "not open" in reason.lower()

    def test_blocked_after_close(self):
        with _mock_now(16, 5):
            ok, reason = is_options_order_allowed("SPY")
        assert ok is False
        assert "closed" in reason.lower()

    def test_blocked_after_options_cutoff(self):
        with _mock_now(15, 20):
            ok, reason = is_options_order_allowed("SPY")
        assert ok is False
        assert "cutoff" in reason.lower()

    def test_blocked_exactly_at_cutoff(self):
        with _mock_now(15, 15):
            ok, reason = is_options_order_allowed("SPY")
        assert ok is False

    def test_allowed_just_before_cutoff(self):
        with _mock_now(15, 14):
            ok, reason = is_options_order_allowed("SPY")
        assert ok is True

    def test_blocked_on_expiration_day(self):
        with _mock_now(10, 0):
            ok, reason = is_options_order_allowed("AAPL", dte=0)
        assert ok is False
        assert "expiration day" in reason.lower()

    def test_allowed_with_positive_dte(self):
        with _mock_now(10, 0):
            ok, reason = is_options_order_allowed("SPY", dte=21)
        assert ok is True


class TestParseDteFromOcc:
    def test_valid_occ_symbol(self):
        # SPY250502P00530000 -> 2025-05-02
        with patch("utils.market_hours.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = datetime(2025, 4, 11, tzinfo=_ET)
            dte = parse_dte_from_occ("SPY250502P00530000")
        assert dte == 21  # May 2 - Apr 11

    def test_invalid_symbol_returns_none(self):
        assert parse_dte_from_occ("INVALID") is None

    def test_short_symbol_returns_none(self):
        assert parse_dte_from_occ("AB") is None
