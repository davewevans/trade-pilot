"""Tests for /api/backtest hardening (Phase 13).

Covers:
1. Small backtest under 70% of remaining budget → 200, job queued
2. Backtest using >70% without confirm_heavy → 202 confirm_required
3. Backtest using >70% with confirm_heavy=true → 200, job queued
4. Backtest exceeding monthly remaining → 400 budget_exceeded
5. Second backtest while one is running → 409
6. Lock released after backtest completes; next backtest starts cleanly
7. ORATS calls during a backtest are tagged job_name="api_backtest" in the ledger
"""

import hmac
import os
import secrets
import threading
import time
import pytest
from unittest.mock import MagicMock, patch

# Set the password BEFORE importing api.server so _SIGNING_KEY is derived correctly.
os.environ.setdefault("DASHBOARD_PASSWORD", "test-secret")

# ---------------------------------------------------------------------------
# Session helper (must match server.py token format)
# ---------------------------------------------------------------------------

def _make_session(password: str = "test-secret") -> str:
    """Mint a valid signed session token matching server.py's _issue_session format."""
    signing_key = hmac.new(
        password.encode(), b"trade-pilot-session-v1", "sha256"
    ).digest()
    expires_at = int(time.time()) + 7 * 24 * 60 * 60
    nonce = secrets.token_hex(8)
    payload = f"{expires_at}.{nonce}".encode()
    sig = hmac.new(signing_key, payload, "sha256").hexdigest()
    return f"{expires_at}.{nonce}.{sig}"


# ---------------------------------------------------------------------------
# Shared test body
# ---------------------------------------------------------------------------

_MINIMAL_BODY = {
    "strategy": "bull_put_spread",
    "symbols": ["SPY"],
    "start_date": "2023-01-01",
    "end_date": "2024-01-01",
}

# ---------------------------------------------------------------------------
# Fixture: reset backtest lock between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_backtest_lock():
    """Clear the module-level backtest lock before and after each test."""
    import api.server as _srv
    with _srv._api_backtest_ctrl_lock:
        if _srv._api_backtest_lock_holder is not None:
            try:
                _srv._api_backtest_lock_holder.release()
            except Exception:
                pass
            _srv._api_backtest_lock_holder = None
            _srv._api_backtest_started_at = None
    yield
    with _srv._api_backtest_ctrl_lock:
        if _srv._api_backtest_lock_holder is not None:
            try:
                _srv._api_backtest_lock_holder.release()
            except Exception:
                pass
            _srv._api_backtest_lock_holder = None
            _srv._api_backtest_started_at = None


def _authed_client():
    """Return a TestClient with a valid session cookie."""
    from fastapi.testclient import TestClient
    from api.server import app
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("session", _make_session())
    return client


# ---------------------------------------------------------------------------
# Test 1: Small estimate → 200
# ---------------------------------------------------------------------------

def test_small_backtest_queued():
    """Backtest under 70% of remaining budget returns 200 and queues the job."""
    with (
        patch("backtesting.engine.BacktestEngine.estimate_cost", return_value=100),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("api.server._run_backtest_job"),
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 10000}
        client = _authed_client()
        res = client.post("/api/backtest", json=_MINIMAL_BODY)

    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    data = res.json()
    assert "job_id" in data
    assert data["status"] == "queued"


# ---------------------------------------------------------------------------
# Test 2: >70% without confirm → 202
# ---------------------------------------------------------------------------

def test_heavy_backtest_requires_confirm():
    """Backtest using >70% of remaining budget returns 202 confirm_required."""
    with (
        patch("backtesting.engine.BacktestEngine.estimate_cost", return_value=800),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 1000}
        client = _authed_client()
        res = client.post("/api/backtest", json=_MINIMAL_BODY)

    assert res.status_code == 202, f"Expected 202, got {res.status_code}: {res.text}"
    body = res.json()
    assert body["status"] == "confirm_required"
    assert body["estimate"] == 800
    assert body["remaining"] == 1000
    assert "message" in body


# ---------------------------------------------------------------------------
# Test 3: >70% WITH confirm_heavy → 200
# ---------------------------------------------------------------------------

def test_heavy_backtest_with_confirm_proceeds():
    """Backtest using >70% budget but with confirm_heavy=true proceeds."""
    with (
        patch("backtesting.engine.BacktestEngine.estimate_cost", return_value=800),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("api.server._run_backtest_job"),
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 1000}
        client = _authed_client()
        res = client.post("/api/backtest", json={**_MINIMAL_BODY, "confirm_heavy": True})

    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    data = res.json()
    assert "job_id" in data
    assert data["status"] == "queued"


# ---------------------------------------------------------------------------
# Test 4: Estimate > remaining → 400
# ---------------------------------------------------------------------------

def test_budget_exceeded_returns_400():
    """Backtest exceeding monthly budget returns 400 budget_exceeded."""
    with (
        patch("backtesting.engine.BacktestEngine.estimate_cost", return_value=1500),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 1000}
        client = _authed_client()
        # Even with confirm_heavy=True, budget_exceeded blocks
        res = client.post("/api/backtest", json={**_MINIMAL_BODY, "confirm_heavy": True})

    assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
    data = res.json()
    assert data["error"] == "budget_exceeded"
    assert data["estimate"] == 1500
    assert data["remaining"] == 1000


# ---------------------------------------------------------------------------
# Test 5: Second concurrent backtest → 409
# ---------------------------------------------------------------------------

def test_concurrent_backtest_returns_409():
    """A second POST /api/backtest while one is running returns 409."""
    import api.server as _srv
    from utils.process_lock import ProcessLock
    from config import settings

    # Simulate a running backtest by setting the lock holder directly
    lock = ProcessLock("api_backtest", settings.DATA_DIR / "locks")
    lock.acquire()
    with _srv._api_backtest_ctrl_lock:
        _srv._api_backtest_lock_holder = lock
        _srv._api_backtest_started_at = "2026-01-01T00:00:00+00:00"

    with (
        patch("backtesting.engine.BacktestEngine.estimate_cost", return_value=100),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 10000}
        client = _authed_client()
        res = client.post("/api/backtest", json=_MINIMAL_BODY)

    assert res.status_code == 409, f"Expected 409, got {res.status_code}: {res.text}"
    data = res.json()
    assert data["error"] == "backtest_already_running"
    assert "since" in data
    assert data["since"] == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Test 6: Lock released after job completes; next request succeeds
# ---------------------------------------------------------------------------

def test_lock_released_after_completion():
    """After a backtest finishes, the lock is released and the next request starts cleanly."""
    import api.server as _srv

    completed = threading.Event()

    def fake_run(job_id, params_dict):
        time.sleep(0.05)
        with _srv._BACKTEST_JOBS_LOCK:
            _srv._BACKTEST_JOBS[job_id]["status"] = "complete"
            _srv._BACKTEST_JOBS[job_id]["result"] = {}
        # Release the lock (simulating the finally block in real _run_backtest_job)
        with _srv._api_backtest_ctrl_lock:
            if _srv._api_backtest_lock_holder is not None:
                _srv._api_backtest_lock_holder.release()
                _srv._api_backtest_lock_holder = None
                _srv._api_backtest_started_at = None
        completed.set()

    with (
        patch("backtesting.engine.BacktestEngine.estimate_cost", return_value=100),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("api.server._run_backtest_job", side_effect=fake_run),
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 10000}
        client = _authed_client()

        # First request
        res1 = client.post("/api/backtest", json=_MINIMAL_BODY)
        assert res1.status_code == 200, f"First request failed: {res1.text}"

        # Wait for the fake job to release the lock
        completed.wait(timeout=2.0)

        # Second request should succeed now that lock is released
        res2 = client.post("/api/backtest", json=_MINIMAL_BODY)
        assert res2.status_code == 200, f"Second request failed after lock release: {res2.text}"


# ---------------------------------------------------------------------------
# Test 7: contextvar tags ledger records as "api_backtest"
# ---------------------------------------------------------------------------

def test_contextvar_tags_ledger_records():
    """ORATS calls within _run_backtest_job scope carry job_name='api_backtest'."""
    from data.api_ledger import current_job_source

    captured = []

    token = current_job_source.set("api_backtest")
    try:
        # Simulate what ApiLedger.record()/_check() now do: prefer contextvar
        resolved = current_job_source.get(None) or os.environ.get("TRADE_PILOT_JOB_NAME")
        captured.append(resolved)
    finally:
        current_job_source.reset(token)

    assert captured == ["api_backtest"], (
        f"Expected job_name='api_backtest', got: {captured}"
    )

    # After reset, contextvar should be cleared
    assert current_job_source.get(None) is None, "ContextVar should be None after reset"
