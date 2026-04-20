"""Tests for evaluation/decision_hydration.py."""

from __future__ import annotations

import json

import pytest

from database.db import Database
from database.repositories.decision_scores_repository import DecisionScoresRepository
from evaluation.decision_hydration import hydrate_decisions


@pytest.fixture
def db(tmp_path):
    d = Database(path=str(tmp_path / "test.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def conn(db):
    return db.get_connection()


@pytest.fixture
def scores_repo(conn):
    return DecisionScoresRepository(conn)


def _insert_decision(conn, *, decision_id: int, context_json=None, reasoning=None):
    conn.execute(
        """
        INSERT OR REPLACE INTO decisions
            (id, timestamp, strategy_type, action, underlying, confidence,
             reasoning, prompt_version, context_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            "2026-04-15T10:00:00",
            "wheel",
            "SELL_PUT",
            "AAPL",
            0.8,
            reasoning if reasoning is not None else "basic reasoning",
            "v1",
            context_json,
        ),
    )
    conn.commit()


def _insert_score(conn, decision_id: int):
    conn.execute(
        """
        INSERT INTO decision_scores
            (decision_id, scorer_type, scored_at, total_score, max_score,
             spot_check_pending, dimension_scores_json, pass_fail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (decision_id, "automated_rubric", "2026-04-16T10:00:00",
         75.0, 100.0, 0, "{}", "pass"),
    )
    conn.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestHydrateDecisions:

    def test_empty_input_returns_empty_list(self, conn, scores_repo):
        result = hydrate_decisions([], conn, scores_repo)
        assert result == []

    def test_unknown_decision_ids_silently_skipped(self, conn, scores_repo):
        result = hydrate_decisions([99999, 88888], conn, scores_repo)
        assert result == []

    def test_valid_decision_with_context_json_parsed(self, conn, scores_repo):
        _insert_decision(conn, decision_id=1, context_json=json.dumps({"iv_rank": 55}))
        result = hydrate_decisions([1], conn, scores_repo)
        assert len(result) == 1
        assert isinstance(result[0]["context"], dict)
        assert result[0]["context"]["iv_rank"] == 55

    def test_invalid_context_json_returns_none_no_raise(self, conn, scores_repo):
        _insert_decision(conn, decision_id=2, context_json="not valid json{{{")
        result = hydrate_decisions([2], conn, scores_repo)
        assert len(result) == 1
        assert result[0]["context"] is None

    def test_missing_context_json_returns_none(self, conn, scores_repo):
        _insert_decision(conn, decision_id=3, context_json=None)
        result = hydrate_decisions([3], conn, scores_repo)
        assert len(result) == 1
        assert result[0]["context"] is None

    def test_reasoning_as_json_string_gets_parsed(self, conn, scores_repo):
        reasoning_dict = {"macro": "stable", "risk": "low"}
        _insert_decision(conn, decision_id=4, reasoning=json.dumps(reasoning_dict))
        result = hydrate_decisions([4], conn, scores_repo)
        assert len(result) == 1
        assert isinstance(result[0]["reasoning"], dict)
        assert result[0]["reasoning"]["macro"] == "stable"

    def test_reasoning_as_plain_string_stays_as_string(self, conn, scores_repo):
        _insert_decision(conn, decision_id=5, reasoning="plain text reasoning")
        result = hydrate_decisions([5], conn, scores_repo)
        assert len(result) == 1
        assert result[0]["reasoning"] == "plain text reasoning"

    def test_scores_attached(self, conn, scores_repo):
        _insert_decision(conn, decision_id=6)
        _insert_score(conn, decision_id=6)
        result = hydrate_decisions([6], conn, scores_repo)
        assert len(result) == 1
        assert isinstance(result[0]["scores"], list)
        assert len(result[0]["scores"]) == 1

    def test_order_preserved(self, conn, scores_repo):
        for did in [10, 20, 30]:
            _insert_decision(conn, decision_id=did)
        result = hydrate_decisions([30, 10, 20], conn, scores_repo)
        assert [r["id"] for r in result] == [30, 10, 20]

    def test_missing_ids_in_middle_skipped_order_preserved(self, conn, scores_repo):
        _insert_decision(conn, decision_id=100)
        _insert_decision(conn, decision_id=200)
        result = hydrate_decisions([100, 999, 200], conn, scores_repo)
        assert [r["id"] for r in result] == [100, 200]
