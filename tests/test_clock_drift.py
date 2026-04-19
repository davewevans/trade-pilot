"""Tests for utils/clock_drift.py — clock drift detection and halt logic."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import utils.clock_drift as clock_drift_mod
from utils.clock_drift import (
    check_and_halt_on_drift,
    fetch_reference_time_utc,
    measure_clock_drift,
    _write_drift_halt_lock,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def patch_settings(tmp_path):
    """Patch settings so every test gets an isolated DATA_DIR and known defaults."""
    mock = MagicMock()
    mock.CLOCK_DRIFT_HALT_ENABLED = True
    mock.CLOCK_DRIFT_THRESHOLD_SECONDS = 30
    mock.CLOCK_DRIFT_FETCH_TIMEOUT_SECONDS = 3.0
    mock.CLOCK_DRIFT_FETCH_MAX_RETRIES = 3
    mock.ALPACA_TRADE_URL = "https://paper-api.alpaca.markets"
    mock.DATA_DIR = tmp_path
    mock.get_all_broker_credentials.return_value = [("key", "secret")]
    with patch.object(clock_drift_mod, "settings", mock):
        yield mock


@pytest.fixture
def lock_path(tmp_path) -> Path:
    return tmp_path / "HALTED.lock"


# ── fetch_reference_time_utc ──────────────────────────────────────────────────


def test_fetch_returns_none_on_network_failure():
    with patch("utils.clock_drift.requests.get", side_effect=ConnectionError("timeout")):
        result = fetch_reference_time_utc()
    assert result is None


def test_fetch_returns_utc_datetime_on_success():
    ts = "2026-04-19T14:30:00-04:00"
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"timestamp": ts}
    with patch("utils.clock_drift.requests.get", return_value=mock_resp):
        result = fetch_reference_time_utc()
    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime.fromisoformat(ts).astimezone(timezone.utc)


def test_fetch_returns_none_when_timestamp_field_missing():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"is_open": True}
    with patch("utils.clock_drift.requests.get", return_value=mock_resp):
        result = fetch_reference_time_utc()
    assert result is None


# ── measure_clock_drift ───────────────────────────────────────────────────────


def test_measure_clock_drift_returns_none_on_network_failure():
    with patch.object(clock_drift_mod, "fetch_reference_time_utc", return_value=None):
        drift, err = measure_clock_drift()
    assert drift is None
    assert err != ""


def test_measure_clock_drift_computes_positive_drift_when_local_ahead():
    """Local clock is 45s ahead of reference — drift ≈ +45."""
    reference = datetime(2026, 4, 19, 14, 0, 0, tzinfo=timezone.utc)
    local_now = reference + timedelta(seconds=45)
    with patch.object(clock_drift_mod, "fetch_reference_time_utc", return_value=reference), \
         patch("utils.clock_drift.datetime") as mock_dt:
        mock_dt.now.return_value = local_now
        drift, err = measure_clock_drift()
    assert err == ""
    assert drift is not None
    assert abs(drift - 45.0) < 1.0


def test_measure_clock_drift_computes_negative_drift_when_local_behind():
    """Local clock is 45s behind reference — drift ≈ -45."""
    reference = datetime(2026, 4, 19, 14, 0, 0, tzinfo=timezone.utc)
    local_now = reference - timedelta(seconds=45)
    with patch.object(clock_drift_mod, "fetch_reference_time_utc", return_value=reference), \
         patch("utils.clock_drift.datetime") as mock_dt:
        mock_dt.now.return_value = local_now
        drift, err = measure_clock_drift()
    assert err == ""
    assert drift is not None
    assert abs(drift - (-45.0)) < 1.0


# ── check_and_halt_on_drift ───────────────────────────────────────────────────


def test_check_and_halt_fails_open_on_fetch_failure(lock_path):
    """Fetch failure → returns True (proceed) and does NOT write lock."""
    with patch.object(clock_drift_mod, "measure_clock_drift", return_value=(None, "network error")):
        result = check_and_halt_on_drift("test_job")
    assert result is True
    assert not lock_path.exists()


def test_check_and_halt_writes_lock_on_drift_above_threshold(lock_path):
    """Drift of 45s exceeds 30s threshold → returns False and writes lock."""
    with patch.object(clock_drift_mod, "measure_clock_drift", return_value=(45.0, "")):
        result = check_and_halt_on_drift("market_open")
    assert result is False
    assert lock_path.exists()
    data = json.loads(lock_path.read_text())
    assert data["source"] == "clock_drift"
    assert data["job_that_detected"] == "market_open"
    assert data["drift_seconds"] == 45.0
    assert data["reference_source"] == "alpaca_v2_clock"
    assert "halted_at" in data


def test_check_and_halt_returns_true_on_drift_under_threshold(lock_path):
    """Drift of 5s is well under threshold — no lock, returns True."""
    with patch.object(clock_drift_mod, "measure_clock_drift", return_value=(5.0, "")):
        result = check_and_halt_on_drift("pre_market")
    assert result is True
    assert not lock_path.exists()


def test_check_and_halt_respects_kill_switch(patch_settings, lock_path):
    """CLOCK_DRIFT_HALT_ENABLED=false → always returns True, no check."""
    patch_settings.CLOCK_DRIFT_HALT_ENABLED = False
    with patch.object(clock_drift_mod, "measure_clock_drift") as mock_measure:
        result = check_and_halt_on_drift("position_check")
    assert result is True
    mock_measure.assert_not_called()
    assert not lock_path.exists()


def test_check_and_halt_does_not_rewrite_existing_lock(lock_path):
    """Pre-existing HALTED.lock content is preserved — not overwritten."""
    existing = json.dumps({"source": "circuit_breaker", "reason": "drawdown"})
    lock_path.write_text(existing, encoding="utf-8")
    with patch.object(clock_drift_mod, "measure_clock_drift", return_value=(60.0, "")):
        check_and_halt_on_drift("expiry_guard")
    assert lock_path.read_text(encoding="utf-8") == existing


def test_drift_exactly_at_threshold_does_not_halt(lock_path):
    """Drift exactly equal to threshold (30.0s) does NOT halt — uses strict >."""
    with patch.object(clock_drift_mod, "measure_clock_drift", return_value=(30.0, "")):
        result = check_and_halt_on_drift("portfolio_refresh")
    assert result is True
    assert not lock_path.exists()
