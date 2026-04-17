"""Tests for skip-breakdown endpoint and scorecard endpoint."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    """Redirect all snapshot/journal/db paths to a temp directory."""
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


def _seed_skip(db, **fields):
    """Insert a SKIP decision with defaults."""
    from database.repositories import DecisionRepository
    repo = DecisionRepository(db.get_connection())
    payload = {
        "timestamp": "2026-04-15T10:00:00",
        "strategy_type": "wheel",
        "underlying": "SPY",
        "action": "SKIP",
        "reasoning": "test skip",
        "skip_gate": None,
        "skip_reason_code": None,
    }
    payload.update(fields)
    return repo.insert(payload)


def _seed_rec(db, **fields):
    """Insert a watchlist_recommendation row and return its ID."""
    from database.repositories import RecommendationRepository
    repo = RecommendationRepository(db.get_connection())
    payload = {
        "generated_at": "2026-01-01T00:00:00",
        "watchlist_name": "wheel",
        "symbol": "AAPL",
        "action": "add",
        "score": 75.0,
        "reasoning": "good score",
        "data_confidence": "high",
        "operator_decision": None,
        "operator_decided_at": None,
    }
    payload.update(fields)
    repo.insert_batch([payload])
    # Retrieve the last inserted ID
    row = db.get_connection().execute(
        "SELECT MAX(recommendation_id) FROM watchlist_recommendations"
    ).fetchone()
    # If operator_decision was provided in fields, update it now
    rec_id = row[0]
    if "operator_decision" in fields and fields["operator_decision"] is not None:
        db.get_connection().execute(
            "UPDATE watchlist_recommendations SET operator_decision=? WHERE recommendation_id=?",
            (fields["operator_decision"], rec_id),
        )
        db.get_connection().commit()
    return rec_id


def _seed_outcome(db, recommendation_id: int, **fields):
    """Insert a recommendation_outcomes row."""
    from database.repositories.outcome_repository import OutcomeRepository
    repo = OutcomeRepository(db.get_connection())
    payload = {
        "recommendation_id": recommendation_id,
        "outcome_type": "ground_truth_live",
        "window_days": 90,
        "window_end_date": "2026-04-01",
        "trade_count": 5,
        "pnl_total": 200.0,
        "pnl_per_trade": 40.0,
        "win_rate": 0.6,
        "computed_at": "2026-04-10T00:00:00",
    }
    payload.update(fields)
    repo.insert(payload)


# ── Skip breakdown tests ───────────────────────────────────────────────────────


class TestSkipBreakdown:
    def test_skip_breakdown_returns_gate_structure(self, client, db):
        """Seeded SKIP rows with known gate values return expected gate breakdown."""
        _seed_skip(db, skip_gate="pre_check", skip_reason_code="ivr_too_low")
        _seed_skip(db, skip_gate="pre_check", skip_reason_code="ivr_too_low")
        _seed_skip(db, skip_gate="guardrail", skip_reason_code="guardrail_position_size")

        r = client.get("/api/decisions/skip-breakdown?since_days=30")
        assert r.status_code == 200
        body = r.json()

        assert body["total_skips"] == 3
        assert body["window_days"] == 30
        assert isinstance(body["by_gate"], list)

        gates = {g["gate"]: g for g in body["by_gate"]}
        assert "pre_check" in gates
        assert gates["pre_check"]["count"] == 2
        assert gates["guardrail"]["count"] == 1

        # pct should sum roughly to 100 (within rounding)
        total_pct = sum(g["pct"] for g in body["by_gate"])
        assert abs(total_pct - 100.0) < 1.0

        # Reasons within pre_check gate
        assert "pre_check" in body["by_reason_within_gate"]
        reasons = body["by_reason_within_gate"]["pre_check"]
        assert any(r["reason"] == "ivr_too_low" for r in reasons)

    def test_skip_breakdown_filters_by_account(self, client, db):
        """Filter by account returns only matching strategy_types."""
        _seed_skip(db, strategy_type="wheel", skip_gate="pre_check", skip_reason_code="ivr_too_low")
        _seed_skip(db, strategy_type="iron_condor", skip_gate="guardrail", skip_reason_code="guardrail_other")

        # Filter to wheel only
        r = client.get("/api/decisions/skip-breakdown?account=wheel&since_days=30")
        assert r.status_code == 200
        body = r.json()
        assert body["total_skips"] == 1
        gates = {g["gate"]: g for g in body["by_gate"]}
        assert "pre_check" in gates
        assert "guardrail" not in gates

        # Filter to iron_condor only
        r2 = client.get("/api/decisions/skip-breakdown?account=iron_condor&since_days=30")
        body2 = r2.json()
        assert body2["total_skips"] == 1
        gates2 = {g["gate"]: g for g in body2["by_gate"]}
        assert "guardrail" in gates2

    def test_skip_breakdown_counts_unclassified(self, client, db):
        """SKIP rows with NULL skip_gate are counted in unclassified_count."""
        _seed_skip(db, skip_gate=None, skip_reason_code=None)
        _seed_skip(db, skip_gate=None, skip_reason_code=None)
        _seed_skip(db, skip_gate="pre_check", skip_reason_code="ivr_too_low")

        r = client.get("/api/decisions/skip-breakdown?since_days=30")
        assert r.status_code == 200
        body = r.json()

        assert body["total_skips"] == 3
        assert body["unclassified_count"] == 2

    def test_skip_breakdown_empty_db(self, client, db):
        """Empty DB returns zero counts without error."""
        r = client.get("/api/decisions/skip-breakdown?since_days=30")
        assert r.status_code == 200
        body = r.json()
        assert body["total_skips"] == 0
        assert body["by_gate"] == []
        assert body["unclassified_count"] == 0


# ── Scorecard tests ────────────────────────────────────────────────────────────


class TestRecommendationScorecard:
    def test_scorecard_returns_4_cells(self, client, db):
        """Empty DB returns all 4 cell keys with count=0."""
        r = client.get("/api/research/recommendations/scorecard")
        assert r.status_code == 200
        body = r.json()
        assert "cells" in body
        cells = body["cells"]
        assert "accepted_add" in cells
        assert "rejected_add" in cells
        assert "accepted_remove" in cells
        assert "rejected_remove" in cells
        # All cells have count=0 in empty DB
        for key in ("accepted_add", "rejected_add", "accepted_remove", "rejected_remove"):
            assert cells[key]["count"] == 0

    def test_scorecard_correctly_labels_outcome_types(self, client, db):
        """Verify accepted_add has ground_truth_live and rejected_add has proxy_backtest."""
        r = client.get("/api/research/recommendations/scorecard")
        assert r.status_code == 200
        body = r.json()
        cells = body["cells"]
        assert cells["accepted_add"]["outcome_type"] == "ground_truth_live"
        assert cells["rejected_add"]["outcome_type"] == "proxy_backtest"
        assert cells["accepted_remove"]["outcome_type"] == "proxy_backtest"
        assert cells["rejected_remove"]["outcome_type"] == "ground_truth_live"

    def test_scorecard_pct_calculation(self, client, db):
        """Seed real recs + outcomes, verify pct calculation."""
        # accepted_add (ground_truth_live): 2 recs, 1 positive pnl → 50% correct
        rec_id1 = _seed_rec(db, symbol="AAPL", action="add", operator_decision="accepted")
        _seed_outcome(db, rec_id1, outcome_type="ground_truth_live", pnl_per_trade=50.0, window_days=90)

        rec_id2 = _seed_rec(db, symbol="MSFT", action="add", operator_decision="accepted")
        _seed_outcome(db, rec_id2, outcome_type="ground_truth_live", pnl_per_trade=-20.0, window_days=90)

        r = client.get("/api/research/recommendations/scorecard?window_days=90&since_days=180")
        assert r.status_code == 200
        body = r.json()
        cell = body["cells"]["accepted_add"]
        assert cell["count"] == 2
        assert cell["recommender_correct_pct"] == 50.0
        assert "AAPL" in cell["symbols"]
        assert "MSFT" in cell["symbols"]

    def test_scorecard_summary_present(self, client, db):
        """Summary block with caveat text is present."""
        r = client.get("/api/research/recommendations/scorecard")
        assert r.status_code == 200
        body = r.json()
        assert "summary" in body
        assert "caveat" in body["summary"]
        assert "proxy" in body["summary"]["caveat"].lower()
