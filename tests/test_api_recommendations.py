"""K5: Tests for /api/research/recommendations/* endpoints."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient


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

    _patch_paths.tmp = tmp_path
    _patch_paths.snap = snap
    _patch_paths.data = data


@pytest.fixture
def client():
    from api.server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def authed_client():
    from api.server import app, _issue_session, _SESSION_COOKIE
    tc = TestClient(app, raise_server_exceptions=False)
    token, _ = _issue_session()
    tc.cookies.set(_SESSION_COOKIE, token)
    return tc


@pytest.fixture
def db():
    from database.db import Database
    import api.server as srv
    db_instance = Database(path=str(srv.DB_PATH))
    db_instance.init_schema()
    yield db_instance
    db_instance.close()


def _seed_watchlist(data_dir, members=None):
    members = members or {"wheel": ["AAPL", "MSFT", "AMD", "GOOGL", "AMZN", "SPY"],
                          "iron_condor": ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL"],
                          "spreads": ["AAPL", "MSFT", "AMD", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "GS"]}
    path = data_dir / "watchlist.json"
    path.write_text(json.dumps(members, indent=2), encoding="utf-8")
    return members


def _seed_recs_snapshot(snap_dir, generated_at="2026-04-20T10:00:00"):
    data = {
        "generated_at": generated_at,
        "wheel": {
            "watchlist_name": "wheel",
            "add": [
                {
                    "recommendation_id": 1,
                    "symbol": "NVDA",
                    "score": 82.5,
                    "reasoning": "Tier A liquidity, strong win rate",
                    "data_confidence": "high",
                    "sub_scores": {"liq_score": 88.0, "wr_tier": "strong"},
                }
            ],
            "remove": [],
            "no_change": [],
            "considered_but_rejected": [],
        },
        "iron_condor": {
            "watchlist_name": "iron_condor",
            "add": [],
            "remove": [],
            "no_change": [],
            "considered_but_rejected": [],
        },
        "iron_butterfly": {
            "watchlist_name": "iron_butterfly",
            "add": [],
            "remove": [],
            "no_change": [],
            "considered_but_rejected": [],
        },
        "spreads": {
            "watchlist_name": "spreads",
            "add": [],
            "remove": [],
            "no_change": [],
            "considered_but_rejected": [],
        },
    }
    path = snap_dir / "watchlist_recommendations.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _seed_db_recs(db, generated_at="2026-04-20T10:00:00"):
    from database.repositories import RecommendationRepository
    repo = RecommendationRepository(db.get_connection())
    recs = [
        {
            "generated_at": generated_at,
            "watchlist_name": "wheel",
            "symbol": "NVDA",
            "action": "add",
            "score": 82.5,
            "reasoning": "Tier A liquidity",
            "data_confidence": "high",
            "sub_scores": {"liq_score": 88.0},
        },
        {
            "generated_at": generated_at,
            "watchlist_name": "wheel",
            "symbol": "AAPL",
            "action": "no_change",
            "score": 75.0,
            "reasoning": "Tier A, incumbent",
            "data_confidence": "high",
            "sub_scores": {},
        },
    ]
    repo.insert_batch(recs)
    return repo


# ── Auth guard ────────────────────────────────────────────────────────────────

class TestAuthRequired:
    ENDPOINTS = [
        ("GET", "/api/research/recommendations"),
        ("GET", "/api/research/recommendations/history"),
        ("POST", "/api/research/recommendations/apply"),
    ]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_unauthenticated_returns_401(self, client, method, path):
        r = client.request(method, path)
        assert r.status_code == 401, f"{method} {path} should be auth-gated"


# ── GET /api/research/recommendations ────────────────────────────────────────

class TestGetRecommendations:

    def test_returns_404_when_no_file(self, authed_client):
        r = authed_client.get("/api/research/recommendations")
        assert r.status_code == 404

    def test_returns_structured_data_when_file_present(self, authed_client):
        import api.server as srv
        _seed_recs_snapshot(srv.SNAPSHOTS)
        _seed_watchlist(srv.DATA_DIR)

        r = authed_client.get("/api/research/recommendations")
        assert r.status_code == 200
        body = r.json()
        assert "generated_at" in body
        assert "watchlists" in body
        for key in ("wheel", "iron_condor", "iron_butterfly", "spreads"):
            assert key in body["watchlists"]
            wl = body["watchlists"][key]
            assert "current_members" in wl
            assert "add" in wl
            assert "remove" in wl
            assert "no_change" in wl
            assert "considered_but_rejected" in wl

    def test_current_members_loaded_from_watchlist_json(self, authed_client):
        import api.server as srv
        _seed_recs_snapshot(srv.SNAPSHOTS)
        _seed_watchlist(srv.DATA_DIR)

        r = authed_client.get("/api/research/recommendations")
        body = r.json()
        assert "AAPL" in body["watchlists"]["wheel"]["current_members"]

    def test_add_list_populated_from_snapshot(self, authed_client):
        import api.server as srv
        _seed_recs_snapshot(srv.SNAPSHOTS)
        _seed_watchlist(srv.DATA_DIR)

        r = authed_client.get("/api/research/recommendations")
        body = r.json()
        add_syms = [i["symbol"] for i in body["watchlists"]["wheel"]["add"]]
        assert "NVDA" in add_syms


# ── GET /api/research/recommendations/history ────────────────────────────────

class TestGetHistory:

    def test_empty_db_returns_empty_list(self, authed_client, db):
        r = authed_client.get("/api/research/recommendations/history")
        assert r.status_code == 200
        assert r.json()["rows"] == []

    def test_seeded_rows_returned(self, authed_client, db):
        _seed_db_recs(db)
        r = authed_client.get("/api/research/recommendations/history")
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert len(rows) == 2
        symbols = {row["symbol"] for row in rows}
        assert "NVDA" in symbols
        assert "AAPL" in symbols

    def test_rows_include_recommendation_id(self, authed_client, db):
        _seed_db_recs(db)
        r = authed_client.get("/api/research/recommendations/history")
        rows = r.json()["rows"]
        for row in rows:
            assert "recommendation_id" in row
            assert isinstance(row["recommendation_id"], int)


# ── POST /api/research/recommendations/apply ─────────────────────────────────

class TestApply:

    def test_malformed_body_returns_400(self, authed_client, db):
        r = authed_client.post(
            "/api/research/recommendations/apply",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_missing_arrays_returns_400(self, authed_client, db):
        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": "NVDA"},  # not a list
        )
        assert r.status_code == 400

    def test_nonexistent_id_returns_400(self, authed_client, db):
        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": [{"recommendation_id": 9999}], "rejected": []},
        )
        assert r.status_code == 400
        assert "not found" in r.json()["errors"][0]

    def test_already_decided_id_returns_400(self, authed_client, db):
        repo = _seed_db_recs(db)
        rows = repo.get_latest_batch()
        rid = rows[0]["recommendation_id"]
        repo.record_decision(rid, "rejected")

        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": [{"recommendation_id": rid}], "rejected": []},
        )
        assert r.status_code == 400
        assert "already decided" in r.json()["errors"][0]

    def test_valid_accept_updates_watchlist_json(self, authed_client, db):
        import api.server as srv
        wl = _seed_watchlist(srv.DATA_DIR)
        repo = _seed_db_recs(db)
        rows = repo.get_latest_batch()
        add_rec = next(r for r in rows if r["action"] == "add")
        rid = add_rec["recommendation_id"]

        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": [{"recommendation_id": rid}], "rejected": []},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == 1
        assert body["rejected"] == 0

        # Check watchlist.json was updated
        updated = json.loads((srv.DATA_DIR / "watchlist.json").read_text())
        assert "NVDA" in updated["wheel"]

    def test_valid_reject_records_decision_only(self, authed_client, db):
        import api.server as srv
        _seed_watchlist(srv.DATA_DIR)
        repo = _seed_db_recs(db)
        rows = repo.get_latest_batch()
        add_rec = next(r for r in rows if r["action"] == "add")
        rid = add_rec["recommendation_id"]

        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": [], "rejected": [{"recommendation_id": rid}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["rejected"] == 1
        assert body["applied"] == 0

        # Symbol should NOT be in watchlist
        updated = json.loads((srv.DATA_DIR / "watchlist.json").read_text())
        assert "NVDA" not in updated.get("wheel", [])

    def test_response_includes_updated_watchlists(self, authed_client, db):
        import api.server as srv
        _seed_watchlist(srv.DATA_DIR)
        repo = _seed_db_recs(db)
        rows = repo.get_latest_batch()
        rid = next(r["recommendation_id"] for r in rows if r["action"] == "add")

        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": [{"recommendation_id": rid}], "rejected": []},
        )
        body = r.json()
        assert "updated_watchlists" in body
        assert "wheel" in body["updated_watchlists"]

    def test_hot_reload_updates_settings(self, authed_client, db):
        import api.server as srv
        _seed_watchlist(srv.DATA_DIR)
        repo = _seed_db_recs(db)
        rows = repo.get_latest_batch()
        rid = next(r["recommendation_id"] for r in rows if r["action"] == "add")

        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": [{"recommendation_id": rid}], "rejected": []},
        )
        assert r.status_code == 200
        # After apply, settings.WATCHLIST should include the new symbol
        from config import settings
        assert "NVDA" in settings.WATCHLIST

    def test_refuse_remove_below_min_members(self, authed_client, db):
        """Removing from a watchlist that would drop below 5 members returns 400."""
        import api.server as srv
        from database.repositories import RecommendationRepository

        # Watchlist with exactly 5 members
        _seed_watchlist(srv.DATA_DIR, members={
            "wheel": ["AAPL", "MSFT", "GOOGL", "AMZN", "SPY"],
            "iron_condor": ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL"],
            "spreads": ["AAPL", "MSFT", "AMD", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "JPM", "GS"],
        })
        # Seed a "remove" recommendation for a wheel member
        conn = db.get_connection()
        repo = RecommendationRepository(conn)
        repo.insert_batch([{
            "generated_at": "2026-04-20T10:00:00",
            "watchlist_name": "wheel",
            "symbol": "AAPL",
            "action": "remove",
            "score": 15.0,
            "reasoning": "Tier D, poor win rate",
            "data_confidence": "high",
            "sub_scores": {},
        }])
        rows = repo.get_latest_batch()
        rid = rows[0]["recommendation_id"]

        r = authed_client.post(
            "/api/research/recommendations/apply",
            json={"accepted": [{"recommendation_id": rid}], "rejected": []},
        )
        assert r.status_code == 400
        assert "minimum" in r.json()["error"].lower()
