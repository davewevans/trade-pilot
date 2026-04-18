"""Unit tests for /api/cycle-summary and /api/claude-agreement endpoints."""

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    import api.server as srv
    monkeypatch.setattr(srv, "SNAPSHOTS", tmp_path / "snapshots")
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv, "DB_PATH", db_path)
    monkeypatch.setattr(srv, "LOCK_PATH", tmp_path / "HALTED.lock")
    _patch_paths.db_path = db_path
    (tmp_path / "snapshots").mkdir()


@pytest.fixture
def client():
    from api.server import app, _issue_session, _SESSION_COOKIE
    tc = TestClient(app, raise_server_exceptions=False)
    token, _ = _issue_session()
    tc.cookies.set(_SESSION_COOKIE, token)
    return tc


@pytest.fixture
def db():
    from database.db import Database
    path = _patch_paths.db_path
    d = Database(path=str(path))
    d.init_schema()
    yield d
    d.close()


def _seed(db, **fields):
    from database.repositories import DecisionRepository
    repo = DecisionRepository(db.get_connection())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    defaults = {
        "timestamp": now,
        "strategy_type": "wheel",
        "underlying": "SPY",
        "action": "SKIP",
        "reasoning": "test",
    }
    defaults.update(fields)
    return repo.insert(defaults)


# ── /api/cycle-summary ───────────────────────────────────────


class TestCycleSummary:
    def test_no_data_returns_zero_shape_not_500(self, client, db):
        """With no decisions in the DB, endpoint returns zeros — not a 500."""
        r = client.get("/api/cycle-summary")
        assert r.status_code == 200
        body = r.json()
        assert body["symbols_evaluated"] == 0
        assert body["pre_check_skipped"]["total"] == 0
        assert body["sent_to_claude"] == 0
        assert body["orders_placed"] == 0
        assert body["claude_outcomes"] == {"SKIP": 0, "OPEN": 0, "CLOSE": 0, "HOLD": 0}
        assert body["guardrail_rejections"]["total"] == 0

    def test_aggregation_produces_correct_counts(self, client, db):
        """Fixture rows produce the expected funnel counts."""
        import uuid
        run_id = f"market_open.{uuid.uuid4()}"

        # 2 pre-check skips with different reason codes
        _seed(db, job_run_id=run_id, pre_check_verdict="SKIP",
              action="SKIP", skip_reason_code="ivr_too_low")
        _seed(db, job_run_id=run_id, pre_check_verdict="SKIP",
              action="SKIP", skip_reason_code="earnings_too_close")
        # Claude skipped after passing pre-check
        _seed(db, job_run_id=run_id, pre_check_verdict="OPEN", action="SKIP")
        # Claude opened after passing pre-check
        _seed(db, job_run_id=run_id, pre_check_verdict="OPEN", action="SELL_PUT")
        # Different run — must not appear
        _seed(db, job_run_id=f"market_open.{uuid.uuid4()}", pre_check_verdict="OPEN", action="SKIP")

        r = client.get(f"/api/cycle-summary?job_run_id={run_id}")
        assert r.status_code == 200
        body = r.json()

        assert body["symbols_evaluated"] == 4
        assert body["pre_check_skipped"]["total"] == 2
        assert body["pre_check_skipped"]["by_reason"]["ivr_too_low"] == 1
        assert body["pre_check_skipped"]["by_reason"]["earnings_too_close"] == 1
        assert body["sent_to_claude"] == 2
        assert body["claude_outcomes"]["SKIP"] == 1
        assert body["claude_outcomes"]["OPEN"] == 1
        assert body["cycle_type"] == "market_open"


# ── /api/claude-agreement ────────────────────────────────────


class TestClaudeAgreement:
    def test_zero_precheck_open_returns_null_skip_rate_not_error(self, client, db):
        """No pre_check_verdict=OPEN rows → null skip rate, no ZeroDivisionError."""
        r = client.get("/api/claude-agreement?window=30d")
        assert r.status_code == 200
        body = r.json()
        assert body["precheck_open_count"] == 0
        assert body["claude_skip_rate_when_precheck_says_open"] is None
        # Response shape is present
        assert "daily_series" in body
        assert "decisions_evaluated" in body

    def test_agreement_rates_computed_correctly(self, client, db):
        """4 pre-check-OPEN decisions (2 skipped by Claude) → rate = 0.5."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for action in ("SKIP", "SKIP", "SELL_PUT", "SELL_PUT"):
            _seed(db, pre_check_verdict="OPEN", action=action, timestamp=now)

        # MANAGE decisions must be excluded from the metric
        _seed(db, pre_check_verdict="MANAGE", action="HOLD", timestamp=now)
        # pre-check SKIP decisions — always action=SKIP, never opens
        _seed(db, pre_check_verdict="SKIP", action="SKIP", timestamp=now)

        r = client.get("/api/claude-agreement?window=all")
        assert r.status_code == 200
        body = r.json()

        # Only OPEN + SKIP verdict rows counted (MANAGE excluded)
        assert body["decisions_evaluated"] == 5  # 4 OPEN + 1 SKIP
        assert body["precheck_open_count"] == 4
        assert body["claude_skip_rate_when_precheck_says_open"] == pytest.approx(0.5)
        assert body["precheck_skip_count"] == 1
        # Claude never opens when pre-check says skip (by design)
        assert body["claude_open_rate_when_precheck_says_skip"] == pytest.approx(0.0)
