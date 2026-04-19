"""Unit tests for data.macro_calendar.

# TODO: operator must populate data/macro_events.json with FOMC + CPI dates
# through end of year before deploying. Tests below use mocked/tmp data files
# and do not rely on real operator-populated dates.
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data.macro_calendar import (
    all_tier_1_events,
    fomc_coverage_days,
    generate_nfp_dates,
    is_blocked,
    load_events,
    next_event,
)

ET = ZoneInfo("America/New_York")


def et(year, month, day, hour=10, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


def make_schedule(*dates: date) -> pd.DataFrame:
    """Build a minimal mcal-style schedule DataFrame from a list of dates."""
    return pd.DataFrame(index=pd.DatetimeIndex([pd.Timestamp(d) for d in dates]))


# ── generate_nfp_dates ────────────────────────────────────────────────────────


class TestGenerateNfpDates:
    def test_returns_12_entries(self):
        nfp = generate_nfp_dates(2026)
        assert len(nfp) == 12

    def test_all_are_fridays(self):
        for entry in generate_nfp_dates(2026):
            d = date.fromisoformat(entry["date"])
            assert d.weekday() == 4, f"{entry['date']} is not a Friday"

    def test_each_is_first_friday_of_month(self):
        for entry in generate_nfp_dates(2026):
            d = date.fromisoformat(entry["date"])
            assert d.day <= 7, f"{entry['date']} is not in the first 7 days of the month"

    def test_all_have_required_schema_fields(self):
        for entry in generate_nfp_dates(2026):
            assert entry["type"] == "NFP"
            assert entry["time_et"] == "08:30"
            assert "date" in entry
            assert "description" in entry

    def test_correct_first_fridays_spot_check(self):
        nfp = {e["date"]: e for e in generate_nfp_dates(2026)}
        # Jan 2026: first Friday is Jan 2
        assert "2026-01-02" in nfp
        # Feb 2026: first Friday is Feb 6
        assert "2026-02-06" in nfp


# ── load_events ───────────────────────────────────────────────────────────────


class TestLoadEvents:
    def test_returns_empty_when_file_missing(self, tmp_path):
        with patch("data.macro_calendar._DATA_FILE", tmp_path / "missing.json"):
            result = load_events()
        assert result == []

    def test_logs_warning_when_file_missing(self, tmp_path, caplog):
        with patch("data.macro_calendar._DATA_FILE", tmp_path / "missing.json"):
            with caplog.at_level(logging.WARNING, logger="data.macro_calendar"):
                load_events()
        assert any("macro_events.json not found" in r.message for r in caplog.records)

    def test_returns_empty_when_file_malformed(self, tmp_path, caplog):
        bad = tmp_path / "macro_events.json"
        bad.write_text("NOT VALID JSON {{{", encoding="utf-8")
        with patch("data.macro_calendar._DATA_FILE", bad):
            with caplog.at_level(logging.WARNING, logger="data.macro_calendar"):
                result = load_events()
        assert result == []
        assert any("Failed to parse" in r.message for r in caplog.records)

    def test_returns_events_from_valid_file(self, tmp_path):
        data = {"events": [
            {"type": "FOMC", "date": "2026-05-07", "time_et": "14:00", "description": "test"},
        ]}
        f = tmp_path / "macro_events.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        with patch("data.macro_calendar._DATA_FILE", f):
            result = load_events()
        assert len(result) == 1
        assert result[0]["type"] == "FOMC"


# ── all_tier_1_events ─────────────────────────────────────────────────────────


class TestAllTier1Events:
    def test_filters_out_past_events(self, tmp_path):
        data = {"events": [
            {"type": "FOMC", "date": "2020-01-01", "time_et": "14:00", "description": "old"},
            {"type": "FOMC", "date": "2099-12-31", "time_et": "14:00", "description": "future"},
        ]}
        f = tmp_path / "macro_events.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        with patch("data.macro_calendar._DATA_FILE", f):
            events = all_tier_1_events(et(2026, 4, 19))
        dates = [e["date"] for e in events]
        assert "2020-01-01" not in dates
        assert "2099-12-31" in dates

    def test_includes_nfp_for_current_and_next_year(self, tmp_path):
        f = tmp_path / "macro_events.json"
        f.write_text('{"events": []}', encoding="utf-8")
        with patch("data.macro_calendar._DATA_FILE", f):
            events = all_tier_1_events(et(2026, 1, 1))
        types = [e["type"] for e in events]
        assert "NFP" in types
        # Should include NFP for both 2026 and 2027
        years = {e["date"][:4] for e in events if e["type"] == "NFP"}
        assert "2026" in years
        assert "2027" in years

    def test_sorted_ascending(self, tmp_path):
        data = {"events": [
            {"type": "FOMC", "date": "2026-06-15", "time_et": "14:00", "description": "later"},
            {"type": "CPI",  "date": "2026-05-10", "time_et": "08:30", "description": "earlier"},
        ]}
        f = tmp_path / "macro_events.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        with patch("data.macro_calendar._DATA_FILE", f):
            events = all_tier_1_events(et(2026, 4, 19))
        filtered = [e for e in events if e["type"] in ("FOMC", "CPI")]
        dates = [e["date"] for e in filtered]
        assert dates == sorted(dates)

    def test_raises_for_naive_datetime(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            all_tier_1_events(datetime(2026, 4, 19, 10, 0))


# ── next_event ────────────────────────────────────────────────────────────────


class TestNextEvent:
    def _patch_events(self, events):
        return patch("data.macro_calendar.all_tier_1_events", return_value=events)

    def _patch_sessions(self, current, nxt):
        return patch("data.macro_calendar._get_session_pair", return_value=(current, nxt))

    def test_returns_nearest_future_event(self):
        now = et(2026, 4, 19, 10, 0)
        events = [
            {"type": "NFP", "date": "2026-05-01", "time_et": "08:30", "description": "May NFP"},
            {"type": "FOMC", "date": "2026-06-15", "time_et": "14:00", "description": "June FOMC"},
        ]
        with self._patch_events(events), self._patch_sessions(
            date(2026, 4, 19), date(2026, 4, 20)
        ):
            result = next_event(now)
        assert result is not None
        assert result["date"] == "2026-05-01"
        assert result["type"] == "NFP"
        assert result["hours_until"] > 0
        assert isinstance(result["is_today"], bool)
        assert isinstance(result["is_next_trading_day"], bool)

    def test_returns_none_when_no_events_within_30_days(self):
        now = et(2026, 4, 19, 10, 0)
        far_future = [
            {"type": "FOMC", "date": "2026-06-30", "time_et": "14:00", "description": "far"},
        ]
        with self._patch_events(far_future), self._patch_sessions(
            date(2026, 4, 19), date(2026, 4, 20)
        ):
            result = next_event(now)
        assert result is None

    def test_is_today_flag(self):
        now = et(2026, 4, 19, 10, 0)
        events = [{"type": "NFP", "date": "2026-04-19", "time_et": "08:30", "description": "today"}]
        with self._patch_events(events), self._patch_sessions(
            date(2026, 4, 19), date(2026, 4, 20)
        ):
            result = next_event(now)
        # Event at 8:30 is before now (10:00), so it won't be returned
        assert result is None

    def test_is_next_trading_day_flag(self):
        now = et(2026, 4, 19, 10, 0)
        events = [{"type": "NFP", "date": "2026-04-20", "time_et": "08:30", "description": "tmrw"}]
        with self._patch_events(events), self._patch_sessions(
            date(2026, 4, 19), date(2026, 4, 20)
        ):
            result = next_event(now)
        assert result is not None
        assert result["is_next_trading_day"] is True

    def test_raises_for_naive_datetime(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            next_event(datetime(2026, 4, 19, 10, 0))


# ── is_blocked ────────────────────────────────────────────────────────────────


class TestIsBlocked:
    def _patch_sessions(self, current, nxt):
        return patch("data.macro_calendar._get_session_pair", return_value=(current, nxt))

    def _patch_events(self, events):
        return patch("data.macro_calendar.all_tier_1_events", return_value=events)

    def _fomc(self, date_str) -> dict:
        return {"type": "FOMC", "date": date_str, "time_et": "14:00", "description": "FOMC"}

    def test_event_is_today_market_open_blocked(self):
        now = et(2026, 4, 21, 10, 0)
        with self._patch_sessions(date(2026, 4, 21), date(2026, 4, 22)), \
             self._patch_events([self._fomc("2026-04-21")]):
            blocked, reason = is_blocked(now)
        assert blocked is True
        assert "FOMC" in reason
        assert "2026-04-21" in reason

    def test_event_is_tomorrow_trading_day_blocked(self):
        now = et(2026, 4, 21, 10, 0)
        with self._patch_sessions(date(2026, 4, 21), date(2026, 4, 22)), \
             self._patch_events([self._fomc("2026-04-22")]):
            blocked, reason = is_blocked(now)
        assert blocked is True
        assert "2026-04-22" in reason

    def test_event_monday_today_friday_blocked(self):
        # Friday April 18 → next trading day is Monday April 21
        now = et(2026, 4, 18, 10, 0)
        with self._patch_sessions(date(2026, 4, 18), date(2026, 4, 21)), \
             self._patch_events([self._fomc("2026-04-21")]):
            blocked, reason = is_blocked(now)
        assert blocked is True

    def test_event_monday_today_thursday_not_blocked(self):
        # Thursday April 17 → next trading day is Friday April 18
        now = et(2026, 4, 17, 10, 0)
        with self._patch_sessions(date(2026, 4, 17), date(2026, 4, 18)), \
             self._patch_events([self._fomc("2026-04-21")]):
            blocked, reason = is_blocked(now)
        assert blocked is False
        assert reason == ""

    def test_event_on_holiday_not_blocked(self):
        # Event is on tomorrow (holiday). NYSE skips it; next session is day-after-tomorrow.
        # event_date is NOT in (current_session, next_session) → not blocked.
        now = et(2026, 4, 24, 10, 0)
        with self._patch_sessions(date(2026, 4, 24), date(2026, 4, 27)), \
             self._patch_events([self._fomc("2026-04-25")]):
            # April 25 is the holiday; not in (Apr 24, Apr 27) → not blocked
            blocked, reason = is_blocked(now)
        assert blocked is False

    def test_event_two_trading_days_out_not_blocked(self):
        now = et(2026, 4, 19, 10, 0)
        with self._patch_sessions(date(2026, 4, 19), date(2026, 4, 20)), \
             self._patch_events([self._fomc("2026-04-21")]):
            blocked, reason = is_blocked(now)
        assert blocked is False
        assert reason == ""

    def test_reason_format(self):
        now = et(2026, 4, 21, 10, 0)
        with self._patch_sessions(date(2026, 4, 21), date(2026, 4, 22)), \
             self._patch_events([self._fomc("2026-04-21")]):
            blocked, reason = is_blocked(now)
        assert reason.startswith("MACRO_EVENT_PROXIMITY: FOMC on 2026-04-21 at 14:00 ET")

    def test_raises_for_naive_datetime(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            is_blocked(datetime(2026, 4, 19, 10, 0))

    def test_returns_false_when_no_events(self, tmp_path):
        f = tmp_path / "macro_events.json"
        f.write_text('{"events": []}', encoding="utf-8")
        # Use a date with no nearby NFP — deep in the middle of a month
        now = et(2026, 4, 10, 10, 0)
        # Patch session pair to real values; patch events to return only far-future items
        with patch("data.macro_calendar._DATA_FILE", f), \
             self._patch_sessions(date(2026, 4, 10), date(2026, 4, 11)), \
             patch("data.macro_calendar.all_tier_1_events", return_value=[]):
            blocked, reason = is_blocked(now)
        assert blocked is False
        assert reason == ""
