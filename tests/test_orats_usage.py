"""Tests for ORATS call counter and usage tracker."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ── ORATSClient counter tests ─────────────────────────────────────────────────


class TestORATSClientCounter:
    def _make_client(self):
        """Create an ORATSClient with a fake API key."""
        with patch("data.orats_client.settings") as mock_settings:
            mock_settings.ORATS_API_KEY = "fake-key"
            from data.orats_client import ORATSClient
            client = ORATSClient(api_key="fake-key")
        return client

    def test_call_counter_increments(self):
        """_track_call increments total and per-endpoint counters."""
        from data.orats_client import ORATSClient
        client = ORATSClient.__new__(ORATSClient)
        client._call_count = 0
        client._calls_by_endpoint = {}
        client._session_start = datetime.utcnow()

        client._track_call("summaries")
        client._track_call("summaries")
        client._track_call("strikes")

        assert client._call_count == 3
        assert client._calls_by_endpoint["summaries"] == 2
        assert client._calls_by_endpoint["strikes"] == 1

    def test_reset_usage(self):
        """reset_usage zeroes counters and sets a new session start."""
        from data.orats_client import ORATSClient
        client = ORATSClient.__new__(ORATSClient)
        client._call_count = 5
        client._calls_by_endpoint = {"summaries": 5}
        client._session_start = datetime.utcnow() - timedelta(hours=1)

        before_reset = datetime.utcnow()
        client.reset_usage()

        assert client._call_count == 0
        assert client._calls_by_endpoint == {}
        assert client._session_start >= before_reset

    def test_get_usage_returns_correct_shape(self):
        """get_usage returns expected keys."""
        from data.orats_client import ORATSClient
        client = ORATSClient.__new__(ORATSClient)
        client._call_count = 3
        client._calls_by_endpoint = {"summaries": 2, "cores": 1}
        client._session_start = datetime.utcnow() - timedelta(minutes=5)

        usage = client.get_usage()
        assert usage["total_calls"] == 3
        assert usage["by_endpoint"]["summaries"] == 2
        assert usage["by_endpoint"]["cores"] == 1
        assert "session_start" in usage
        assert usage["window_seconds"] > 0

    def test_track_call_on_summaries_http_call(self):
        """Calling get_summary (cache miss) increments the counter."""
        from data.orats_client import ORATSClient, _cache

        # Clear any cached value from in-memory fallback
        _cache._fallback.pop(("summaries", "SPY"), None)
        # Also invalidate SQLite cache by deleting the row if the cache has a DB connection
        if _cache._conn is not None:
            try:
                _cache._conn.execute(
                    "DELETE FROM orats_cache WHERE endpoint=? AND cache_key=?",
                    ("summaries", "SPY"),
                )
                _cache._conn.commit()
            except Exception:
                pass

        client = ORATSClient.__new__(ORATSClient)
        client.api_key = "fake-key"
        client._call_count = 0
        client._calls_by_endpoint = {}
        client._session_start = datetime.utcnow()

        # Patch requests.get to return a fake response
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {"data": []}

        with patch("data.orats_client.requests.get", return_value=fake_resp):
            result = client.get_summary("SPY")

        # get_summary returns None when data is empty, but the counter should have incremented
        assert client._call_count == 1
        assert client._calls_by_endpoint.get("summaries", 0) == 1


# ── ORATSUsageTracker tests ───────────────────────────────────────────────────


class TestORATSUsageTracker:
    def test_tracker_record_and_read(self, tmp_path):
        """Write 3 runs to tmp path, get_recent_runs returns 3."""
        from data.orats_usage_tracker import ORATSUsageTracker

        tracker = ORATSUsageTracker(path=str(tmp_path / "orats_usage.jsonl"))
        now = datetime.utcnow()

        for i in range(3):
            tracker.record_run(
                run_type=f"run_{i}",
                call_count=10 + i,
                by_endpoint={"summaries": 10 + i},
                started_at=now - timedelta(hours=i),
                completed_at=now - timedelta(hours=i) + timedelta(minutes=1),
            )

        runs = tracker.get_recent_runs(since_days=1)
        assert len(runs) == 3
        # Should be sorted newest first
        assert runs[0]["started_at"] > runs[1]["started_at"]

    def test_tracker_daily_totals(self, tmp_path):
        """Write runs on 2 dates, assert aggregation by day."""
        from data.orats_usage_tracker import ORATSUsageTracker

        tracker = ORATSUsageTracker(path=str(tmp_path / "orats_usage.jsonl"))

        day1 = datetime(2026, 4, 10, 8, 0, 0)
        day2 = datetime(2026, 4, 11, 8, 0, 0)

        tracker.record_run("scan", 15, {}, day1, day1 + timedelta(minutes=2))
        tracker.record_run("scan", 10, {}, day1, day1 + timedelta(minutes=1))
        tracker.record_run("sweep", 30, {}, day2, day2 + timedelta(minutes=5))

        totals = tracker.get_daily_totals(since_days=30)
        by_date = {t["date"]: t["total_calls"] for t in totals}

        assert by_date["2026-04-10"] == 25
        assert by_date["2026-04-11"] == 30

    def test_tracker_empty_file_returns_empty(self, tmp_path):
        """Non-existent file returns empty list."""
        from data.orats_usage_tracker import ORATSUsageTracker

        tracker = ORATSUsageTracker(path=str(tmp_path / "nonexistent.jsonl"))
        assert tracker.get_recent_runs(since_days=30) == []
        assert tracker.get_daily_totals(since_days=30) == []

    def test_tracker_filters_by_since_days(self, tmp_path):
        """Runs older than since_days are excluded."""
        from data.orats_usage_tracker import ORATSUsageTracker

        tracker = ORATSUsageTracker(path=str(tmp_path / "orats_usage.jsonl"))

        recent = datetime.utcnow() - timedelta(days=5)
        old = datetime.utcnow() - timedelta(days=45)

        tracker.record_run("recent", 5, {}, recent, recent + timedelta(minutes=1))
        tracker.record_run("old", 99, {}, old, old + timedelta(minutes=1))

        runs = tracker.get_recent_runs(since_days=30)
        run_types = [r["run_type"] for r in runs]
        assert "recent" in run_types
        assert "old" not in run_types
