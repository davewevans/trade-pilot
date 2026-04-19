"""Tests for Phase 7: ORATS kill switch (ORATS_DISABLED.lock)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from data.api_ledger import (
    ApiLedger,
    OratsDisabled,
    disable_orats,
    enable_orats,
    is_orats_disabled,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_ledger(tmp_path: Path) -> ApiLedger:
    return ApiLedger(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# case 1: check_and_reserve raises OratsDisabled when lock file present
# ---------------------------------------------------------------------------


def test_check_and_reserve_raises_when_disabled(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    ledger = make_ledger(tmp_path)
    disable_orats("test disable")

    with pytest.raises(OratsDisabled):
        ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)


# ---------------------------------------------------------------------------
# case 2: check_and_reserve passes normally when kill switch not active
# ---------------------------------------------------------------------------


def test_check_and_reserve_passes_when_enabled(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    ledger = make_ledger(tmp_path)
    # No lock file — should not raise
    ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)


# ---------------------------------------------------------------------------
# case 3: disable_orats / enable_orats / is_orats_disabled round-trip
# ---------------------------------------------------------------------------


def test_disable_enable_round_trip(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    assert not is_orats_disabled()

    disable_orats("integration test")
    assert is_orats_disabled()

    enable_orats()
    assert not is_orats_disabled()


# ---------------------------------------------------------------------------
# case 4: non-ORATS APIs not blocked by kill switch
# ---------------------------------------------------------------------------


def test_non_orats_api_not_blocked(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)

    ledger = make_ledger(tmp_path)
    disable_orats("test")

    # anthropic is not capped and not ORATS — should not raise OratsDisabled
    ledger.check_and_reserve("anthropic", "messages", "AAPL", None)


# ---------------------------------------------------------------------------
# case 5: POST /api/admin/orats/disable wires up correctly
# ---------------------------------------------------------------------------


def test_admin_disable_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from config import settings
    from api import server as srv

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    original_disabled_path = srv._ORATS_DISABLED_PATH
    srv._ORATS_DISABLED_PATH = tmp_path / "ORATS_DISABLED.lock"

    try:
        client = TestClient(srv.app, raise_server_exceptions=True)
        with patch.object(srv, "_is_authenticated", return_value=True):
            resp = client.post(
                "/api/admin/orats/disable",
                json={"reason": "quota emergency"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "disabled"
        assert (tmp_path / "ORATS_DISABLED.lock").exists()
    finally:
        srv._ORATS_DISABLED_PATH = original_disabled_path
        # Clean up lock file so other tests are not affected
        lock = tmp_path / "ORATS_DISABLED.lock"
        if lock.exists():
            lock.unlink()


# ---------------------------------------------------------------------------
# case 6: POST /api/admin/orats/enable + GET /api/admin/orats/status
# ---------------------------------------------------------------------------


def test_admin_enable_and_status_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from config import settings
    from api import server as srv

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    original_disabled_path = srv._ORATS_DISABLED_PATH
    srv._ORATS_DISABLED_PATH = tmp_path / "ORATS_DISABLED.lock"

    try:
        # Pre-create lock file
        (tmp_path / "ORATS_DISABLED.lock").write_text("test reason", encoding="utf-8")

        client = TestClient(srv.app, raise_server_exceptions=True)
        with patch.object(srv, "_is_authenticated", return_value=True):
            status_resp = client.get("/api/admin/orats/status")
            assert status_resp.json()["disabled"] is True
            assert status_resp.json()["reason"] == "test reason"

            enable_resp = client.post("/api/admin/orats/enable")
            assert enable_resp.json()["status"] == "enabled"

            status_resp2 = client.get("/api/admin/orats/status")
            assert status_resp2.json()["disabled"] is False
    finally:
        srv._ORATS_DISABLED_PATH = original_disabled_path
        lock = tmp_path / "ORATS_DISABLED.lock"
        if lock.exists():
            lock.unlink()
