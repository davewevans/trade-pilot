"""J6: Tests for RecommendationRepository."""

from __future__ import annotations

import pytest


@pytest.fixture
def db(tmp_path):
    from database.db import Database
    d = Database(path=str(tmp_path / "test.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def repo(db):
    from database.repositories import RecommendationRepository
    return RecommendationRepository(db.get_connection())


def _rec(**overrides) -> dict:
    base = {
        "generated_at": "2026-04-20T10:00:00",
        "watchlist_name": "wheel",
        "symbol": "AAPL",
        "action": "add",
        "score": 72.5,
        "reasoning": "Tier A liquidity, strong win rate",
        "data_confidence": "high",
        "sub_scores": {"liq_score": 85.0, "wr_tier": "strong"},
    }
    base.update(overrides)
    return base


# ── insert_batch ──────────────────────────────────────────────────────────────

class TestInsertBatch:

    def test_returns_count(self, repo):
        recs = [_rec(symbol="AAPL"), _rec(symbol="MSFT")]
        assert repo.insert_batch(recs) == 2

    def test_empty_batch_returns_zero(self, repo):
        assert repo.insert_batch([]) == 0

    def test_round_trip_fields(self, repo):
        repo.insert_batch([_rec()])
        rows = repo.get_latest_batch()
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"] == "AAPL"
        assert r["watchlist_name"] == "wheel"
        assert r["action"] == "add"
        assert abs(r["score"] - 72.5) < 0.001
        assert r["reasoning"] == "Tier A liquidity, strong win rate"
        assert r["data_confidence"] == "high"
        assert r["operator_decision"] is None

    def test_sub_scores_parsed_as_dict(self, repo):
        repo.insert_batch([_rec(sub_scores={"liq_score": 90.0, "wr_tier": "good"})])
        rows = repo.get_latest_batch()
        assert isinstance(rows[0]["sub_scores"], dict)
        assert rows[0]["sub_scores"]["liq_score"] == 90.0

    def test_null_sub_scores_returns_empty_dict(self, repo):
        repo.insert_batch([_rec(sub_scores=None)])
        rows = repo.get_latest_batch()
        assert rows[0]["sub_scores"] == {}


# ── get_latest_batch ──────────────────────────────────────────────────────────

class TestGetLatestBatch:

    def test_returns_only_most_recent_generated_at(self, repo):
        repo.insert_batch([_rec(generated_at="2026-04-13T10:00:00", symbol="OLD")])
        repo.insert_batch([_rec(generated_at="2026-04-20T10:00:00", symbol="NEW")])

        rows = repo.get_latest_batch()
        symbols = [r["symbol"] for r in rows]
        assert "NEW" in symbols
        assert "OLD" not in symbols

    def test_empty_db_returns_empty_list(self, repo):
        assert repo.get_latest_batch() == []

    def test_multiple_rows_same_batch(self, repo):
        recs = [
            _rec(symbol="AAPL", action="add"),
            _rec(symbol="MSFT", action="add"),
            _rec(symbol="SPY", action="no_change"),
        ]
        repo.insert_batch(recs)
        rows = repo.get_latest_batch()
        assert len(rows) == 3


# ── get_pending ───────────────────────────────────────────────────────────────

class TestGetPending:

    def test_returns_only_null_decision_rows(self, repo):
        repo.insert_batch([
            _rec(symbol="AAPL"),
            _rec(symbol="MSFT"),
        ])
        # Decide on one
        rows = repo.get_latest_batch()
        rid = next(r["recommendation_id"] for r in rows if r["symbol"] == "AAPL")
        repo.record_decision(rid, "accepted")

        pending = repo.get_pending()
        symbols = [r["symbol"] for r in pending]
        assert "MSFT" in symbols
        assert "AAPL" not in symbols

    def test_empty_db_returns_empty_list(self, repo):
        assert repo.get_pending() == []

    def test_all_decided_returns_empty(self, repo):
        repo.insert_batch([_rec(symbol="AAPL")])
        rows = repo.get_latest_batch()
        repo.record_decision(rows[0]["recommendation_id"], "rejected")
        assert repo.get_pending() == []


# ── record_decision ───────────────────────────────────────────────────────────

class TestRecordDecision:

    def test_sets_accepted(self, repo):
        repo.insert_batch([_rec()])
        rows = repo.get_latest_batch()
        rid = rows[0]["recommendation_id"]
        repo.record_decision(rid, "accepted", decided_at="2026-04-20T12:00:00")

        updated = repo.get_history(limit=1)[0]
        assert updated["operator_decision"] == "accepted"
        assert updated["operator_decided_at"] == "2026-04-20T12:00:00"

    def test_sets_rejected(self, repo):
        repo.insert_batch([_rec()])
        rows = repo.get_latest_batch()
        rid = rows[0]["recommendation_id"]
        repo.record_decision(rid, "rejected")

        updated = repo.get_history(limit=1)[0]
        assert updated["operator_decision"] == "rejected"

    def test_invalid_decision_raises(self, repo):
        repo.insert_batch([_rec()])
        rows = repo.get_latest_batch()
        rid = rows[0]["recommendation_id"]
        with pytest.raises(ValueError):
            repo.record_decision(rid, "maybe")

    def test_defaults_decided_at_to_now(self, repo):
        repo.insert_batch([_rec()])
        rows = repo.get_latest_batch()
        rid = rows[0]["recommendation_id"]
        repo.record_decision(rid, "accepted")

        updated = repo.get_history(limit=1)[0]
        assert updated["operator_decided_at"] is not None
        assert len(updated["operator_decided_at"]) > 10  # ISO string


# ── get_history ───────────────────────────────────────────────────────────────

class TestGetHistory:

    def test_returns_most_recent_first(self, repo):
        repo.insert_batch([_rec(generated_at="2026-04-13T10:00:00", symbol="OLD")])
        repo.insert_batch([_rec(generated_at="2026-04-20T10:00:00", symbol="NEW")])

        rows = repo.get_history(limit=10)
        # Most recent generated_at first
        assert rows[0]["symbol"] == "NEW"

    def test_limit_respected(self, repo):
        recs = [_rec(symbol=f"SYM{i}") for i in range(10)]
        repo.insert_batch(recs)
        assert len(repo.get_history(limit=3)) == 3

    def test_returns_all_decision_states(self, repo):
        repo.insert_batch([_rec(symbol="AAPL"), _rec(symbol="MSFT")])
        rows = repo.get_latest_batch()
        repo.record_decision(rows[0]["recommendation_id"], "accepted")
        # get_history should return both decided and undecided
        history = repo.get_history(limit=10)
        assert len(history) == 2
