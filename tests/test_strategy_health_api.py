"""Tests for the /api/strategy-health endpoint."""

import pytest
from fastapi.testclient import TestClient

from datetime import datetime, timedelta, timezone
from database.repositories.strategy_health import _monday_of_week


# ── Fixtures (mirror pattern from test_skip_breakdown.py) ────────────────────


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    snap = tmp_path / "snapshots"
    snap.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    import api.server as srv

    monkeypatch.setattr(srv, "SNAPSHOTS", snap)
    monkeypatch.setattr(srv, "DATA_DIR", data)
    monkeypatch.setattr(srv, "JOURNAL_PATH", data / "journal.jsonl")
    monkeypatch.setattr(srv, "LOCK_PATH", data / "HALTED.lock")
    monkeypatch.setattr(srv, "DB_PATH", tmp_path / "test.db")


@pytest.fixture
def client():
    from api.server import app, _issue_session, _SESSION_COOKIE
    tc = TestClient(app, raise_server_exceptions=False)
    token, _ = _issue_session()
    tc.cookies.set(_SESSION_COOKIE, token)
    return tc


@pytest.fixture
def unauthed_client():
    from api.server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def db(tmp_path):
    import api.server as srv
    from database.db import Database
    db_instance = Database(path=str(srv.DB_PATH))
    db_instance.init_schema()
    yield db_instance
    db_instance.close()


def _seed_decision(conn, **fields):
    import uuid
    defaults = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "strategy_type": "wheel",
        "underlying": "SPY",
        "action": "SKIP",
        "skip_gate": "pre_check",
    }
    defaults.update(fields)
    conn.execute(
        """
        INSERT INTO decisions (timestamp, strategy_type, underlying, action, skip_gate)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            defaults["timestamp"],
            defaults["strategy_type"],
            defaults["underlying"],
            defaults["action"],
            defaults.get("skip_gate"),
        ),
    )
    conn.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestAuth:
    def test_requires_authentication(self, unauthed_client):
        r = unauthed_client.get("/api/strategy-health")
        assert r.status_code == 401

    def test_authenticated_request_succeeds(self, client, db):
        r = client.get("/api/strategy-health?weeks=1")
        assert r.status_code == 200


class TestResponseShape:
    def test_response_has_expected_keys(self, client, db):
        r = client.get("/api/strategy-health?weeks=1")
        assert r.status_code == 200
        body = r.json()
        assert "weeks" in body
        assert "generated_at" in body
        assert body["paper_mode"] is True

    def test_weeks_field_is_list(self, client, db):
        r = client.get("/api/strategy-health?weeks=1")
        assert isinstance(r.json()["weeks"], list)

    def test_funnel_row_has_all_required_fields(self, client, db):
        _seed_decision(db.get_connection())
        r = client.get("/api/strategy-health?weeks=1")
        rows = r.json()["weeks"]
        assert len(rows) >= 1
        row = rows[0]
        required = [
            "strategy_type", "iso_week", "week_start_date",
            "decisions_total",
            "skip_pre_check", "skip_claude", "skip_guardrail",
            "skip_circuit_breaker", "skip_liquidity_floor", "skip_winrate_floor",
            "skip_no_candidate", "skip_data_missing", "skip_halted", "skip_unclassified",
            "hold", "actions_proposed",
            "trades_submitted", "trades_filled", "trades_pending",
            "trades_closed_profit", "trades_closed_loss",
            "trades_closed_breakeven", "trades_closed_unknown",
            "top_claude_skip_reason", "top_guardrail_reason",
        ]
        for field in required:
            assert field in row, f"Missing field: {field}"


class TestQueryParameters:
    def test_weeks_1_returns_one_weeks_worth(self, client, db):
        conn = db.get_connection()
        monday = _monday_of_week(datetime.now(timezone.utc))

        # Current week decision
        _seed_decision(conn, timestamp=monday.strftime("%Y-%m-%dT%H:%M:%S"), strategy_type="wheel")
        # Two weeks ago — must be excluded
        two_ago = (monday - timedelta(weeks=2)).strftime("%Y-%m-%dT%H:%M:%S")
        _seed_decision(conn, timestamp=two_ago, strategy_type="iron_condor")

        r = client.get("/api/strategy-health?weeks=1")
        rows = r.json()["weeks"]
        iso_weeks = {row["iso_week"] for row in rows}
        assert len(iso_weeks) == 1

    def test_weeks_53_returns_422(self, client, db):
        r = client.get("/api/strategy-health?weeks=53")
        assert r.status_code == 422

    def test_weeks_0_returns_422(self, client, db):
        r = client.get("/api/strategy-health?weeks=0")
        assert r.status_code == 422

    def test_strategy_filter_applied(self, client, db):
        conn = db.get_connection()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        _seed_decision(conn, timestamp=ts, strategy_type="wheel")
        _seed_decision(conn, timestamp=ts, strategy_type="iron_condor")

        r = client.get("/api/strategy-health?weeks=1&strategy=wheel")
        rows = r.json()["weeks"]
        assert all(row["strategy_type"] == "wheel" for row in rows)

    def test_strategy_filter_none_returns_all(self, client, db):
        conn = db.get_connection()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        _seed_decision(conn, timestamp=ts, strategy_type="wheel")
        _seed_decision(conn, timestamp=ts, strategy_type="iron_condor")

        r = client.get("/api/strategy-health?weeks=1")
        strategies = {row["strategy_type"] for row in r.json()["weeks"]}
        assert "wheel" in strategies
        assert "iron_condor" in strategies


class TestKillSwitch:
    def test_disabled_flag_returns_404(self, client, db, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "STRATEGY_HEALTH_PAGE_ENABLED", False)
        r = client.get("/api/strategy-health")
        assert r.status_code == 404

    def test_enabled_flag_returns_200(self, client, db, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "STRATEGY_HEALTH_PAGE_ENABLED", True)
        r = client.get("/api/strategy-health?weeks=1")
        assert r.status_code == 200

    def test_health_endpoint_surfaces_flag(self, client, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "STRATEGY_HEALTH_PAGE_ENABLED", False)
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["strategy_health_enabled"] is False
