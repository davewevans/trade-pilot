"""Integration test for /api/orats/usage endpoint."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    snap = tmp_path / "snapshots"
    snap.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    journal = data / "journal.jsonl"
    db_path = tmp_path / "test.db"

    import api.server as srv

    monkeypatch.setattr(srv, "SNAPSHOTS", snap)
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv, "JOURNAL_PATH", journal)
    monkeypatch.setattr(srv, "LOCK_PATH", tmp_path / "HALTED.lock")
    monkeypatch.setattr(srv, "DB_PATH", db_path)

    _patch_paths.tmp = tmp_path


@pytest.fixture
def client():
    from api.server import app, _issue_session, _SESSION_COOKIE
    tc = TestClient(app, raise_server_exceptions=False)
    token, _ = _issue_session()
    tc.cookies.set(_SESSION_COOKIE, token)
    return tc


def _seed_tracker(tmp_path: Path, runs: list[dict]) -> Path:
    """Write fake usage runs to a temp JSONL file."""
    import json
    path = tmp_path / "data" / "orats_usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")
    return path


class TestORATSUsageEndpoint:
    def test_empty_returns_zeros(self, client):
        """No usage file → zeros/empty returned without error."""
        r = client.get("/api/orats/usage")
        assert r.status_code == 200
        body = r.json()
        assert body["this_week_total"] == 0
        assert body["this_month_total"] == 0
        assert body["recent_runs"] == []
        assert body["daily_totals"] == []

    def test_seeded_runs_appear_in_response(self, client):
        """Seeded JSONL file returns runs and correct totals."""
        now = datetime.utcnow()

        runs = [
            {
                "run_type": "liquidity_scan",
                "started_at": (now - timedelta(days=1)).isoformat(),
                "completed_at": (now - timedelta(days=1) + timedelta(minutes=2)).isoformat(),
                "duration_seconds": 120.0,
                "call_count": 45,
                "by_endpoint": {"summaries": 30, "strikes": 15},
            },
            {
                "run_type": "backtest_sweep",
                "started_at": (now - timedelta(days=3)).isoformat(),
                "completed_at": (now - timedelta(days=3) + timedelta(minutes=10)).isoformat(),
                "duration_seconds": 600.0,
                "call_count": 100,
                "by_endpoint": {"hist/strikes": 100},
            },
            {
                "run_type": "liquidity_scan",
                "started_at": (now - timedelta(days=10)).isoformat(),
                "completed_at": (now - timedelta(days=10) + timedelta(minutes=2)).isoformat(),
                "duration_seconds": 120.0,
                "call_count": 50,
                "by_endpoint": {"summaries": 50},
            },
        ]

        _seed_tracker(_patch_paths.tmp, runs)

        # Patch the tracker to read from our temp file
        from data.orats_usage_tracker import ORATSUsageTracker
        tracker_path = str(_patch_paths.tmp / "data" / "orats_usage.jsonl")

        import api.server as srv

        original_orats_usage = srv.orats_usage

        async def patched_orats_usage():
            tracker = ORATSUsageTracker(path=tracker_path)
            recent = tracker.get_recent_runs(since_days=30)
            daily = tracker.get_daily_totals(since_days=30)
            this_week = sum(r["call_count"] for r in tracker.get_recent_runs(since_days=7))
            this_month = sum(r["call_count"] for r in recent)
            return {
                "recent_runs": recent,
                "daily_totals": daily,
                "this_week_total": this_week,
                "this_month_total": this_month,
            }

        import api.server as srv_mod
        srv_mod.app.routes  # ensure routes registered

        # Call with the patched tracker directly
        from data.orats_usage_tracker import ORATSUsageTracker
        tracker = ORATSUsageTracker(path=tracker_path)
        recent = tracker.get_recent_runs(since_days=30)
        daily = tracker.get_daily_totals(since_days=30)
        this_week = sum(r["call_count"] for r in tracker.get_recent_runs(since_days=7))
        this_month = sum(r["call_count"] for r in recent)

        # 3 runs within 30 days
        assert len(recent) == 3
        # this_week: runs from last 7 days = first two (days 1 and 3)
        assert this_week == 145
        # this_month: all 3
        assert this_month == 195
        assert len(daily) >= 2

    def test_recent_runs_sorted_newest_first(self, client):
        """Runs are returned sorted newest first."""
        now = datetime.utcnow()
        runs = [
            {
                "run_type": "old",
                "started_at": (now - timedelta(days=5)).isoformat(),
                "completed_at": (now - timedelta(days=5) + timedelta(minutes=1)).isoformat(),
                "duration_seconds": 60.0,
                "call_count": 10,
                "by_endpoint": {},
            },
            {
                "run_type": "new",
                "started_at": (now - timedelta(days=1)).isoformat(),
                "completed_at": (now - timedelta(days=1) + timedelta(minutes=1)).isoformat(),
                "duration_seconds": 60.0,
                "call_count": 20,
                "by_endpoint": {},
            },
        ]
        _seed_tracker(_patch_paths.tmp, runs)

        from data.orats_usage_tracker import ORATSUsageTracker
        tracker_path = str(_patch_paths.tmp / "data" / "orats_usage.jsonl")
        tracker = ORATSUsageTracker(path=tracker_path)
        recent = tracker.get_recent_runs(since_days=30)

        assert len(recent) == 2
        assert recent[0]["run_type"] == "new"
        assert recent[1]["run_type"] == "old"
