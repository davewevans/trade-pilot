"""Tests for the /api/fill-realism endpoint."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from config import FILL_REALISM_GATE_SAMPLE, FILL_REALISM_GATE_PCT


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


def _seed_shadow(conn, strategy_type="sell_put", t2m_class="always_fillable",
                 eod_class=None, submitted_at=None):
    ts = submitted_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """
        INSERT INTO shadow_executions (
            submitted_at, submitted_at_et, strategy_type, action,
            underlying, order_kind, is_credit, net_limit_abs,
            t0_status, t0_attempts,
            t30s_status, t2m_status, t2m_class, t2m_attempts,
            t15m_status, eod_status, eod_class, eod_attempts,
            completed
        ) VALUES (
            ?, ?, ?, 'sell_put', 'SPY', 'single_leg', 1, 1.50,
            'captured', 1,
            'captured', 'captured', ?, 1,
            'captured', ?, ?, 1,
            1
        )
        """,
        (ts, ts, strategy_type, t2m_class, "captured" if eod_class else "pending", eod_class),
    )
    conn.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_unauthed_returns_401(self, unauthed_client, db):
        r = unauthed_client.get("/api/fill-realism")
        assert r.status_code == 401

    def test_authed_empty_db_returns_200(self, client, db):
        r = client.get("/api/fill-realism")
        assert r.status_code == 200


class TestFlagOff:
    def test_disabled_returns_404(self, client, db, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", False)
        r = client.get("/api/fill-realism")
        assert r.status_code == 404

    def test_health_surfaces_shadow_flag(self, client, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "SHADOW_EXECUTION_ENABLED", False)
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["shadow_execution_enabled"] is False


class TestEmptyDb:
    def test_empty_strategies_list(self, client, db):
        r = client.get("/api/fill-realism")
        body = r.json()
        assert body["strategies"] == []
        assert "gate_sample" in body
        assert "gate_pct" in body


class TestGateLogic:
    def test_below_gate_sample_no_percentage(self, client, db):
        conn = db.get_connection()
        # Seed fewer rows than gate_sample
        for _ in range(FILL_REALISM_GATE_SAMPLE - 1):
            _seed_shadow(conn, t2m_class="always_fillable")
        r = client.get("/api/fill-realism")
        body = r.json()
        assert len(body["strategies"]) == 1
        row = body["strategies"][0]
        assert row["t2m_realism_pct"] is None
        assert row["gate_met"] is False

    def test_at_gate_sample_with_100pct_fillable_gate_met(self, client, db):
        conn = db.get_connection()
        for _ in range(FILL_REALISM_GATE_SAMPLE):
            _seed_shadow(conn, t2m_class="always_fillable")
        r = client.get("/api/fill-realism")
        body = r.json()
        row = body["strategies"][0]
        assert row["t2m_realism_pct"] == pytest.approx(100.0)
        assert row["gate_met"] is True

    def test_data_unavailable_excluded_from_denominator(self, client, db):
        conn = db.get_connection()
        # Half always_fillable, half data_unavailable — gate_sample met
        half = FILL_REALISM_GATE_SAMPLE
        for _ in range(half):
            _seed_shadow(conn, t2m_class="always_fillable")
        for _ in range(half):
            _seed_shadow(conn, t2m_class="data_unavailable")
        r = client.get("/api/fill-realism")
        body = r.json()
        row = body["strategies"][0]
        # Denominator excludes data_unavailable, so realism = half/(half) = 100%
        assert row["t2m_realism_pct"] == pytest.approx(100.0)
        assert row["data_unavailable_count"] == half

    def test_not_fillable_lowers_percentage(self, client, db):
        conn = db.get_connection()
        n = FILL_REALISM_GATE_SAMPLE
        for _ in range(n // 2):
            _seed_shadow(conn, t2m_class="always_fillable")
        for _ in range(n // 2):
            _seed_shadow(conn, t2m_class="not_fillable")
        r = client.get("/api/fill-realism")
        body = r.json()
        row = body["strategies"][0]
        assert row["t2m_realism_pct"] == pytest.approx(50.0)
        assert row["gate_met"] is False


class TestDaysFilter:
    def test_days_filter_excludes_old_rows(self, client, db):
        conn = db.get_connection()
        # Old row (200 days ago)
        old_ts = "2020-01-01T12:00:00"
        _seed_shadow(conn, t2m_class="always_fillable", submitted_at=old_ts)
        r = client.get("/api/fill-realism?days=90")
        body = r.json()
        # Row should be excluded — no strategies in window
        assert body["strategies"] == []

    def test_days_param_max_365(self, client, db):
        r = client.get("/api/fill-realism?days=366")
        assert r.status_code == 422

    def test_days_param_min_1(self, client, db):
        r = client.get("/api/fill-realism?days=0")
        assert r.status_code == 422

    def test_valid_days_param_accepted(self, client, db):
        r = client.get("/api/fill-realism?days=30")
        assert r.status_code == 200


class TestResponseShape:
    def test_strategy_row_has_all_fields(self, client, db):
        conn = db.get_connection()
        for _ in range(FILL_REALISM_GATE_SAMPLE):
            _seed_shadow(conn, t2m_class="always_fillable", eod_class="always_fillable")
        r = client.get("/api/fill-realism")
        body = r.json()
        row = body["strategies"][0]
        required = [
            "strategy_type", "sample_size", "always_count", "sometimes_count",
            "not_fillable_count", "data_unavailable_count",
            "t2m_realism_pct", "eod_sample_size", "eod_realism_pct", "gate_met",
        ]
        for field in required:
            assert field in row, f"Missing field: {field}"
