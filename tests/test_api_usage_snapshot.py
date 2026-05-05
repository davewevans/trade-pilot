"""Tests for Phase 5: ApiLedger.write_snapshot() and GET /api/usage endpoint."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from data.api_ledger import ApiLedger


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_ledger(tmp_path: Path) -> ApiLedger:
    from database.db import Database
    db = Database(path=tmp_path / "test.db")
    db.init_schema()
    return ApiLedger(db.get_connection())


# ---------------------------------------------------------------------------
# case 1: write_snapshot() creates api_usage.json with expected keys
# ---------------------------------------------------------------------------


def test_write_snapshot_creates_file(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 14000)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 14000)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 600)
    monkeypatch.setattr(settings, "ORATS_LIVE_MONTHLY_CAP", 4000)
    monkeypatch.setattr(settings, "ORATS_LIVE_DAILY_CAP", 700)
    monkeypatch.setattr(settings, "ORATS_LIVE_MINUTE_CAP", 120)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 45, None)
    ledger.write_snapshot()

    snapshot_path = tmp_path / "snapshots" / "api_usage.json"
    assert snapshot_path.exists()

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert "ts" in data
    assert "orats_historical" in data
    assert "orats_live" in data

    hist = data["orats_historical"]
    assert hist["month_used"] == 1
    assert hist["month_cap"] == 14000
    assert hist["month_remaining"] == 13999


# ---------------------------------------------------------------------------
# case 2: snapshot auto-triggers every 50 billable calls
# ---------------------------------------------------------------------------


def test_snapshot_auto_triggers_every_50_calls(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)
    snapshot_path = tmp_path / "snapshots" / "api_usage.json"

    # 49 calls — no snapshot yet
    for _ in range(49):
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 10, None)
    assert not snapshot_path.exists()

    # 50th call — snapshot should appear
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 10, None)
    assert snapshot_path.exists()

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert data["orats_historical"]["month_used"] == 50


# ---------------------------------------------------------------------------
# case 3: cache hits do NOT count toward snapshot trigger
# ---------------------------------------------------------------------------


def test_cache_hits_do_not_trigger_snapshot(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)
    snapshot_path = tmp_path / "snapshots" / "api_usage.json"

    # 100 cache hits — should not trigger snapshot
    for _ in range(100):
        ledger.record("orats_historical", "hist/summaries", "AAPL", True, None, None, None)
    assert not snapshot_path.exists()


# ---------------------------------------------------------------------------
# case 4: snapshot is written when a cap is exceeded (OratsQuotaExceeded)
# ---------------------------------------------------------------------------


def test_snapshot_written_on_quota_exceeded(tmp_path, monkeypatch):
    from config import settings
    from data.api_ledger import OratsQuotaExceeded

    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 1)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)
    # Use the 1-call budget
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    snapshot_path = tmp_path / "snapshots" / "api_usage.json"
    assert not snapshot_path.exists()  # record alone doesn't trigger (1 < 50)

    with pytest.raises(OratsQuotaExceeded):
        ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    assert snapshot_path.exists()
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert data["orats_historical"]["month_remaining"] == 0


# ---------------------------------------------------------------------------
# case 5: GET /api/usage returns 503 when snapshot absent, 200 when present
# ---------------------------------------------------------------------------


def test_api_usage_endpoint_no_snapshot(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from config import settings

    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "empty_snapshots")
    (tmp_path / "empty_snapshots").mkdir()

    # Patch _read_json within the server module to use our temp snapshots dir
    from api import server as srv

    original_snapshots = srv.SNAPSHOTS
    srv.SNAPSHOTS = tmp_path / "empty_snapshots"
    try:
        client = TestClient(srv.app, raise_server_exceptions=True)
        # Need an auth token — use a direct call bypassing middleware
        # by patching _is_authenticated
        with patch.object(srv, "_is_authenticated", return_value=True):
            resp = client.get("/api/usage")
        assert resp.status_code == 503
    finally:
        srv.SNAPSHOTS = original_snapshots


def test_api_usage_endpoint_returns_snapshot(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from config import settings

    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", snapshots_dir)

    # Write a snapshot manually
    snapshot = {
        "ts": "2026-04-18T00:00:00Z",
        "orats_historical": {"month_used": 42, "month_cap": 14000, "month_remaining": 13958},
        "orats_live": {"month_used": 5, "month_cap": 4000, "month_remaining": 3995},
    }
    (snapshots_dir / "api_usage.json").write_text(json.dumps(snapshot), encoding="utf-8")

    from api import server as srv

    original_snapshots = srv.SNAPSHOTS
    srv.SNAPSHOTS = snapshots_dir
    try:
        client = TestClient(srv.app, raise_server_exceptions=True)
        with patch.object(srv, "_is_authenticated", return_value=True):
            resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["orats_historical"]["month_used"] == 42
        assert data["orats_live"]["month_remaining"] == 3995
    finally:
        srv.SNAPSHOTS = original_snapshots
