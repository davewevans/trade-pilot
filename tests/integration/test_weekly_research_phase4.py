"""Integration test for weekly_research Phase 4."""

from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


@pytest.fixture
def db_with_recommendation(tmp_path):
    """Seed a DB with one accepted and one rejected recommendation from 91 days ago."""
    from database.db import Database
    from database.repositories.recommendations import RecommendationRepository

    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    conn = db.get_connection()
    rec_repo = RecommendationRepository(conn)

    old_date = (date.today() - timedelta(days=91)).isoformat()

    rec_repo.insert_batch([
        {
            "generated_at": old_date,
            "watchlist_name": "wheel",
            "symbol": "AAPL",
            "action": "add",
            "score": 80.0,
            "reasoning": "test",
            "data_confidence": "high",
            "operator_decision": "accepted",
            "operator_decided_at": old_date,
        },
        {
            "generated_at": old_date,
            "watchlist_name": "wheel",
            "symbol": "MSFT",
            "action": "add",
            "score": 75.0,
            "reasoning": "test rejected",
            "data_confidence": "high",
            "operator_decision": "rejected",
            "operator_decided_at": old_date,
        },
    ])

    return db


def test_phase4_writes_two_outcome_rows(db_with_recommendation):
    """Phase 4 runs on seeded DB and produces two outcome rows."""
    from database.repositories.trades import TradeRepository
    from database.repositories.recommendations import RecommendationRepository
    from database.repositories.outcome_repository import OutcomeRepository
    from research.recommendations.outcomes import OutcomeComputer

    conn = db_with_recommendation.get_connection()
    trade_repo = TradeRepository(conn)
    rec_repo = RecommendationRepository(conn)
    outcome_repo = OutcomeRepository(conn)

    # Mock the backtest engine to avoid ORATS calls
    mock_result = MagicMock()
    mock_result.trades = []
    mock_engine = MagicMock()
    mock_engine.run.return_value = mock_result

    computer = OutcomeComputer(
        trade_repo=trade_repo,
        backtest_engine=mock_engine,
        outcome_repo=outcome_repo,
        rec_repo=rec_repo,
    )
    result = computer.compute_pending_outcomes()

    assert result["errors"] == 0
    assert result["computed"] == 2

    outcomes = outcome_repo.get_all()
    assert len(outcomes) == 2

    outcome_types = {o["outcome_type"] for o in outcomes}
    # AAPL accepted add → ground_truth_live
    # MSFT rejected add → proxy_backtest
    assert "ground_truth_live" in outcome_types
    assert "proxy_backtest" in outcome_types
