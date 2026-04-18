"""Tests for /api/evaluations/* endpoints."""

from __future__ import annotations

import json

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


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _seed_eval(db, month="2026-04", decisions_evaluated=10, flags=None,
               score_distribution=None, reviewed_at=None, action_note=None):
    from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository
    repo = MonthlyEvaluationsRepository(db.get_connection())
    row = {
        "month": month,
        "decisions_evaluated": decisions_evaluated,
        "avg_score": 78.5,
        "pct_pass": 0.80,
        "score_distribution_json": json.dumps(score_distribution or {
            "overall": {"decisions_scored": decisions_evaluated, "closed_trades_in_window": 4},
            "by_strategy": {},
        }),
        "flags_json": json.dumps(flags or []),
        "created_at": f"{month}-30T23:59:59",
        "reviewed_at": reviewed_at,
        "action_note": action_note,
    }
    repo.insert(row)
    return repo


def _seed_decision(db, decision_id_hint=None):
    """Insert a minimal decision row and return its id."""
    conn = db.get_connection()
    cur = conn.execute(
        """
        INSERT INTO decisions (
            timestamp, strategy_type, underlying, action, reasoning,
            confidence, context_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-04-15T10:00:00",
            "wheel",
            "AAPL",
            "OPEN",
            "good signal",
            0.8,
            json.dumps({"iv_rank": 55}),
        ),
    )
    conn.commit()
    return cur.lastrowid


def _seed_score(db, decision_id, scored_at="2026-04-15T10:00:00",
                spot_check_pending=0, total_score=75.0):
    from database.repositories.decision_scores_repository import DecisionScoresRepository
    repo = DecisionScoresRepository(db.get_connection())
    score_id = repo.insert({
        "decision_id": decision_id,
        "scorer_type": "automated_rubric",
        "scored_at": scored_at,
        "total_score": total_score,
        "max_score": 100.0,
        "spot_check_pending": spot_check_pending,
        "dimension_scores_json": json.dumps({"entry_timing": 0.7, "size_discipline": 0.8}),
        "notes": "auto eval notes",
    })
    return score_id


# ── Auth guard ────────────────────────────────────────────────────────────────

class TestAuthRequired:
    ENDPOINTS = [
        ("GET", "/api/evaluations"),
        ("GET", "/api/evaluations/2026-04"),
        ("GET", "/api/evaluations/2026-04/flagged-decisions"),
        ("GET", "/api/evaluations/2026-04/spot-check-queue"),
        ("POST", "/api/evaluations/2026-04/spot-checks"),
        ("POST", "/api/evaluations/2026-04/mark-reviewed"),
    ]

    @pytest.mark.parametrize("method,path", ENDPOINTS)
    def test_unauthenticated_returns_401(self, client, method, path):
        r = client.request(method, path)
        assert r.status_code == 401, f"{method} {path} should be auth-gated"


# ── GET /api/evaluations ──────────────────────────────────────────────────────

class TestEvaluationsList:

    def test_empty_db_returns_empty_list(self, authed_client, db):
        r = authed_client.get("/api/evaluations")
        assert r.status_code == 200
        body = r.json()
        assert body["evaluations"] == []
        assert body["total"] == 0

    def test_returns_seeded_rows(self, authed_client, db):
        _seed_eval(db, month="2026-04")
        _seed_eval(db, month="2026-03")
        r = authed_client.get("/api/evaluations")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert len(body["evaluations"]) == 2

    def test_newest_first_ordering(self, authed_client, db):
        _seed_eval(db, month="2026-03")
        _seed_eval(db, month="2026-04")
        r = authed_client.get("/api/evaluations")
        months = [e["month"] for e in r.json()["evaluations"]]
        assert months == ["2026-04", "2026-03"]

    def test_response_shape(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.get("/api/evaluations")
        row = r.json()["evaluations"][0]
        for key in ("month", "generated_at", "decisions_scored",
                    "closed_trades_in_window", "insufficient_sample",
                    "flag_count", "review_status", "judge_operator_disagreement"):
            assert key in row, f"missing key: {key}"

    def test_flag_count_from_flags_json(self, authed_client, db):
        flags = [
            {"strategy": "wheel", "dimension": "entry_timing",
             "severity": "warning", "reason": "low"},
            {"strategy": "wheel", "dimension": "size", "severity": "warning", "reason": "low"},
        ]
        _seed_eval(db, flags=flags)
        r = authed_client.get("/api/evaluations")
        assert r.json()["evaluations"][0]["flag_count"] == 2

    def test_review_status_pending_when_not_reviewed(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.get("/api/evaluations")
        assert r.json()["evaluations"][0]["review_status"] == "pending"

    def test_review_status_reviewed_when_reviewed_at_set(self, authed_client, db):
        _seed_eval(db, reviewed_at="2026-05-01T10:00:00")
        r = authed_client.get("/api/evaluations")
        assert r.json()["evaluations"][0]["review_status"] == "reviewed"

    def test_review_status_reviewed_with_action(self, authed_client, db):
        _seed_eval(db, reviewed_at="2026-05-01T10:00:00", action_note="raised alert")
        r = authed_client.get("/api/evaluations")
        assert r.json()["evaluations"][0]["review_status"] == "reviewed-with-action"

    def test_closed_trades_from_score_distribution(self, authed_client, db):
        dist = {"overall": {"decisions_scored": 10, "closed_trades_in_window": 7}, "by_strategy": {}}
        _seed_eval(db, score_distribution=dist)
        r = authed_client.get("/api/evaluations")
        assert r.json()["evaluations"][0]["closed_trades_in_window"] == 7

    def test_pagination_limit_offset(self, authed_client, db):
        for m in ["2026-01", "2026-02", "2026-03"]:
            _seed_eval(db, month=m)
        r = authed_client.get("/api/evaluations?limit=2&offset=0")
        body = r.json()
        assert body["total"] == 3
        assert len(body["evaluations"]) == 2

        r2 = authed_client.get("/api/evaluations?limit=2&offset=2")
        assert len(r2.json()["evaluations"]) == 1

    def test_503_when_db_unavailable(self, authed_client, monkeypatch):
        import api.server as srv
        monkeypatch.setattr(srv, "_open_db", lambda: None)
        r = authed_client.get("/api/evaluations")
        assert r.status_code == 503


# ── GET /api/evaluations/{month} ─────────────────────────────────────────────

class TestEvaluationsDetail:

    def test_404_for_unknown_month(self, authed_client, db):
        r = authed_client.get("/api/evaluations/2020-01")
        assert r.status_code == 404

    def test_returns_full_row(self, authed_client, db):
        _seed_eval(db, month="2026-04", decisions_evaluated=12)
        r = authed_client.get("/api/evaluations/2026-04")
        assert r.status_code == 200
        body = r.json()
        assert body["month"] == "2026-04"
        assert body["decisions_scored"] == 12

    def test_response_shape(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.get("/api/evaluations/2026-04")
        body = r.json()
        for key in ("month", "generated_at", "decisions_scored", "avg_score",
                    "pct_pass", "reviewed_at", "action_note", "review_status",
                    "flag_summary", "score_distribution", "judge_operator_disagreement"):
            assert key in body, f"missing key: {key}"

    def test_flag_summary_is_parsed_list(self, authed_client, db):
        flags = [{"strategy": "wheel", "dimension": "entry_timing",
                  "severity": "warning", "reason": "low"}]
        _seed_eval(db, flags=flags)
        r = authed_client.get("/api/evaluations/2026-04")
        assert isinstance(r.json()["flag_summary"], list)
        assert len(r.json()["flag_summary"]) == 1

    def test_score_distribution_is_parsed_dict(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.get("/api/evaluations/2026-04")
        assert isinstance(r.json()["score_distribution"], dict)

    def test_review_status_derived_correctly(self, authed_client, db):
        _seed_eval(db, reviewed_at="2026-05-01T10:00:00", action_note="follow up")
        r = authed_client.get("/api/evaluations/2026-04")
        assert r.json()["review_status"] == "reviewed-with-action"

    def test_503_when_db_unavailable(self, authed_client, monkeypatch):
        import api.server as srv
        monkeypatch.setattr(srv, "_open_db", lambda: None)
        r = authed_client.get("/api/evaluations/2026-04")
        assert r.status_code == 503


# ── GET /api/evaluations/{month}/flagged-decisions ───────────────────────────

class TestFlaggedDecisions:

    def test_404_for_unknown_month(self, authed_client, db):
        r = authed_client.get("/api/evaluations/2020-01/flagged-decisions")
        assert r.status_code == 404

    def test_empty_flags_returns_empty_list(self, authed_client, db):
        _seed_eval(db, month="2026-04", flags=[])
        r = authed_client.get("/api/evaluations/2026-04/flagged-decisions")
        assert r.status_code == 200
        assert r.json()["flags"] == []

    def test_flag_shape(self, authed_client, db):
        did = _seed_decision(db)
        flags = [{
            "strategy": "wheel",
            "dimension": "entry_timing",
            "severity": "warning",
            "reason": "score below threshold",
            "lowest_scoring_decisions": [did],
        }]
        _seed_eval(db, flags=flags)
        r = authed_client.get("/api/evaluations/2026-04/flagged-decisions")
        assert r.status_code == 200
        body = r.json()
        assert len(body["flags"]) == 1
        flag = body["flags"][0]
        for key in ("strategy", "dimension", "severity", "reason", "decisions"):
            assert key in flag

    def test_decision_context_parsed(self, authed_client, db):
        did = _seed_decision(db)
        flags = [{
            "strategy": "wheel",
            "dimension": "entry_timing",
            "severity": "warning",
            "reason": "low",
            "lowest_scoring_decisions": [did],
        }]
        _seed_eval(db, flags=flags)
        r = authed_client.get("/api/evaluations/2026-04/flagged-decisions")
        decision = r.json()["flags"][0]["decisions"][0]
        assert isinstance(decision.get("context"), dict)

    def test_decision_scores_attached(self, authed_client, db):
        did = _seed_decision(db)
        _seed_score(db, did)
        flags = [{
            "strategy": "wheel",
            "dimension": "entry_timing",
            "severity": "warning",
            "reason": "low",
            "lowest_scoring_decisions": [did],
        }]
        _seed_eval(db, flags=flags)
        r = authed_client.get("/api/evaluations/2026-04/flagged-decisions")
        decision = r.json()["flags"][0]["decisions"][0]
        assert isinstance(decision["scores"], list)
        assert len(decision["scores"]) == 1

    def test_missing_decision_ids_skipped_gracefully(self, authed_client, db):
        flags = [{
            "strategy": "wheel",
            "dimension": "entry_timing",
            "severity": "warning",
            "reason": "low",
            "lowest_scoring_decisions": [99999],  # doesn't exist
        }]
        _seed_eval(db, flags=flags)
        r = authed_client.get("/api/evaluations/2026-04/flagged-decisions")
        assert r.status_code == 200
        assert r.json()["flags"][0]["decisions"] == []

    def test_503_when_db_unavailable(self, authed_client, monkeypatch):
        import api.server as srv
        monkeypatch.setattr(srv, "_open_db", lambda: None)
        r = authed_client.get("/api/evaluations/2026-04/flagged-decisions")
        assert r.status_code == 503


# ── GET /api/evaluations/{month}/spot-check-queue ────────────────────────────

class TestSpotCheckQueue:

    def test_404_for_unknown_month(self, authed_client, db):
        r = authed_client.get("/api/evaluations/2020-01/spot-check-queue")
        assert r.status_code == 404

    def test_empty_queue_when_no_pending(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        _seed_score(db, did, spot_check_pending=0)
        r = authed_client.get("/api/evaluations/2026-04/spot-check-queue")
        assert r.status_code == 200
        assert r.json()["queue"] == []

    def test_returns_pending_items(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        _seed_score(db, did, spot_check_pending=1)
        r = authed_client.get("/api/evaluations/2026-04/spot-check-queue")
        assert r.status_code == 200
        queue = r.json()["queue"]
        assert len(queue) == 1

    def test_queue_item_shape(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        _seed_score(db, did, spot_check_pending=1)
        r = authed_client.get("/api/evaluations/2026-04/spot-check-queue")
        item = r.json()["queue"][0]
        assert "decision" in item
        assert "score" in item

    def test_decision_context_parsed(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        _seed_score(db, did, spot_check_pending=1)
        r = authed_client.get("/api/evaluations/2026-04/spot-check-queue")
        decision = r.json()["queue"][0]["decision"]
        assert isinstance(decision.get("context"), dict)

    def test_dimension_scores_parsed(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        _seed_score(db, did, spot_check_pending=1)
        r = authed_client.get("/api/evaluations/2026-04/spot-check-queue")
        score = r.json()["queue"][0]["score"]
        assert isinstance(score.get("dimension_scores"), dict)

    def test_limit_param_respected(self, authed_client, db):
        _seed_eval(db)
        for _ in range(5):
            did = _seed_decision(db)
            _seed_score(db, did, spot_check_pending=1)
        r = authed_client.get("/api/evaluations/2026-04/spot-check-queue?limit=3")
        assert len(r.json()["queue"]) == 3

    def test_503_when_db_unavailable(self, authed_client, monkeypatch):
        import api.server as srv
        monkeypatch.setattr(srv, "_open_db", lambda: None)
        r = authed_client.get("/api/evaluations/2026-04/spot-check-queue")
        assert r.status_code == 503


# ── POST /api/evaluations/{month}/spot-checks ────────────────────────────────

class TestSubmitSpotCheck:

    def test_404_for_unknown_month(self, authed_client, db):
        r = authed_client.post("/api/evaluations/2020-01/spot-checks",
                               json={"decision_score_id": 1, "operator_verdict": "agree"})
        assert r.status_code == 404

    def test_invalid_json_returns_400(self, authed_client, db):
        r = authed_client.post(
            "/api/evaluations/2026-04/spot-checks",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_missing_decision_score_id_returns_400(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                               json={"operator_verdict": "agree"})
        assert r.status_code == 400

    def test_invalid_verdict_returns_400(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        sid = _seed_score(db, did, spot_check_pending=1)
        r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                               json={"decision_score_id": sid, "operator_verdict": "WRONG"})
        assert r.status_code == 400

    def test_nonexistent_score_id_returns_404(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                               json={"decision_score_id": 99999, "operator_verdict": "agree"})
        assert r.status_code == 404

    def test_score_wrong_month_returns_400(self, authed_client, db):
        _seed_eval(db, month="2026-04")
        did = _seed_decision(db)
        sid = _seed_score(db, did, scored_at="2026-05-10T10:00:00")  # May, not April
        r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                               json={"decision_score_id": sid, "operator_verdict": "agree"})
        assert r.status_code == 400

    def test_valid_submission_returns_spot_check(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        sid = _seed_score(db, did, spot_check_pending=1)
        r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                               json={"decision_score_id": sid, "operator_verdict": "agree",
                                     "note": "looks good"})
        assert r.status_code == 200
        body = r.json()
        assert "spot_check" in body
        assert body["spot_check"]["operator_verdict"] == "agree"

    def test_valid_submission_clears_pending_flag(self, authed_client, db):
        from database.repositories.decision_scores_repository import DecisionScoresRepository
        _seed_eval(db)
        did = _seed_decision(db)
        sid = _seed_score(db, did, spot_check_pending=1)
        authed_client.post("/api/evaluations/2026-04/spot-checks",
                           json={"decision_score_id": sid, "operator_verdict": "disagree"})
        rows = DecisionScoresRepository(db.get_connection()).get_by_decision(did)
        row = next(r for r in rows if r["id"] == sid)
        assert row["spot_check_pending"] == 0

    def test_all_valid_verdicts_accepted(self, authed_client, db):
        _seed_eval(db, month="2026-04")
        for verdict in ("agree", "disagree", "unclear"):
            did = _seed_decision(db)
            sid = _seed_score(db, did, spot_check_pending=1)
            r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                                   json={"decision_score_id": sid, "operator_verdict": verdict})
            assert r.status_code == 200, f"verdict {verdict!r} should be accepted"

    def test_note_null_is_accepted(self, authed_client, db):
        _seed_eval(db)
        did = _seed_decision(db)
        sid = _seed_score(db, did, spot_check_pending=1)
        r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                               json={"decision_score_id": sid, "operator_verdict": "agree",
                                     "note": None})
        assert r.status_code == 200

    def test_503_when_db_unavailable(self, authed_client, monkeypatch):
        import api.server as srv
        monkeypatch.setattr(srv, "_open_db", lambda: None)
        r = authed_client.post("/api/evaluations/2026-04/spot-checks",
                               json={"decision_score_id": 1, "operator_verdict": "agree"})
        assert r.status_code == 503


# ── POST /api/evaluations/{month}/mark-reviewed ──────────────────────────────

class TestMarkReviewed:

    def test_404_for_unknown_month(self, authed_client, db):
        r = authed_client.post("/api/evaluations/2020-01/mark-reviewed",
                               json={"action_note": None})
        assert r.status_code == 404

    def test_invalid_json_returns_400(self, authed_client, db):
        r = authed_client.post(
            "/api/evaluations/2026-04/mark-reviewed",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_review_status_reviewed_when_no_action_note(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.post("/api/evaluations/2026-04/mark-reviewed",
                               json={"action_note": None})
        assert r.status_code == 200
        body = r.json()
        assert body["review_status"] == "reviewed"
        assert body["reviewed_at"] is not None
        assert body["action_note"] is None

    def test_review_status_reviewed_with_action_when_note_given(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.post("/api/evaluations/2026-04/mark-reviewed",
                               json={"action_note": "escalated to lead"})
        assert r.status_code == 200
        body = r.json()
        assert body["review_status"] == "reviewed-with-action"
        assert body["action_note"] == "escalated to lead"

    def test_empty_string_action_note_treated_as_no_action(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.post("/api/evaluations/2026-04/mark-reviewed",
                               json={"action_note": ""})
        assert r.status_code == 200
        assert r.json()["review_status"] == "reviewed"

    def test_returns_month_and_reviewed_at(self, authed_client, db):
        _seed_eval(db)
        r = authed_client.post("/api/evaluations/2026-04/mark-reviewed", json={})
        body = r.json()
        assert body["month"] == "2026-04"
        assert body["reviewed_at"] is not None

    def test_idempotent_second_mark_reviewed(self, authed_client, db):
        _seed_eval(db)
        authed_client.post("/api/evaluations/2026-04/mark-reviewed", json={})
        r = authed_client.post("/api/evaluations/2026-04/mark-reviewed",
                               json={"action_note": "updated note"})
        assert r.status_code == 200
        assert r.json()["action_note"] == "updated note"

    def test_503_when_db_unavailable(self, authed_client, monkeypatch):
        import api.server as srv
        monkeypatch.setattr(srv, "_open_db", lambda: None)
        r = authed_client.post("/api/evaluations/2026-04/mark-reviewed", json={})
        assert r.status_code == 503
