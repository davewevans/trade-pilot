"""Tests for the token_usage-backed /api/claude-costs aggregator.

The decisions-based aggregator (_claude_costs_from_db) is empty in
production because no caller passes usage_data to record_decision.
_claude_costs_from_token_usage reads the populated token_usage table
instead, returning the identical response shape.
"""

from datetime import datetime, timedelta, timezone

import pytest

from database.db import Database
from database.repositories.token_usage_repository import TokenUsageRepository


def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


@pytest.fixture
def test_db(tmp_path):
    db = Database(path=str(tmp_path / "test_token_usage_costs.db"))
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def seeded_conn(test_db):
    """Seed token_usage with rows spanning multiple windows/models/actions/strategies."""
    conn = test_db.get_connection()
    repo = TokenUsageRepository(conn)

    # Inside the 7d current window, strategy=wheel
    repo.insert({
        "timestamp": _ts(1), "strategy_type": "wheel", "underlying": "AAPL",
        "model": "claude-sonnet-4-6", "cache_read_tokens": 100, "cache_creation_tokens": 0,
        "estimated_cost_usd": 1.0, "decision_action": "SELL_PUT",
    })
    # Exactly at the 7d window edge — must land in the CURRENT window, not prior.
    repo.insert({
        "timestamp": _ts(7), "strategy_type": "wheel", "underlying": "MSFT",
        "model": "claude-sonnet-4-6", "cache_read_tokens": 0, "cache_creation_tokens": 100,
        "estimated_cost_usd": 0.5, "decision_action": "SKIP",
    })
    # NULL decision_action — must fall in the "skip" bucket and be excluded
    # from filled_trades_count.
    repo.insert({
        "timestamp": _ts(2), "strategy_type": "wheel", "underlying": "SPY",
        "model": "claude-sonnet-4-6", "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "estimated_cost_usd": 0.3, "decision_action": None,
    })
    # Different strategy, inside the current window — exercises the
    # strategy_types filter.
    repo.insert({
        "timestamp": _ts(1), "strategy_type": "iron_condor", "underlying": "QQQ",
        "model": "claude-opus-4-7", "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "estimated_cost_usd": 0.7, "decision_action": "SELL_CALL",
    })
    # Inside the prior window (7-14 days ago) for a 7d window.
    repo.insert({
        "timestamp": _ts(10), "strategy_type": "wheel", "underlying": "TSLA",
        "model": "claude-opus-4-7", "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "estimated_cost_usd": 2.0, "decision_action": "CLOSE",
    })
    # Older than both windows — only shows up in the "all" window.
    repo.insert({
        "timestamp": _ts(40), "strategy_type": "wheel", "underlying": "NVDA",
        "model": "claude-sonnet-4-6", "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "estimated_cost_usd": 5.0, "decision_action": "SKIP",
    })
    return conn


def test_empty_strategy_types_short_circuits(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", [])

    assert result["total_cost_usd"] == 0.0
    assert result["decisions_count"] == 0
    assert result["filled_trades_count"] == 0
    assert result["cost_per_filled_trade_usd"] is None
    assert result["cache_hit_rate"] is None
    assert result["by_model_version"] == {}
    for bucket in ("open", "close", "skip"):
        assert result["cost_by_outcome"][bucket] == {"count": 0, "cost_usd": 0.0}


def test_7d_window_totals_and_edge_row(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", None)

    # 4 rows within [-7d, now]: recent, edge, null-action, other-strategy.
    assert result["decisions_count"] == 4
    assert abs(result["total_cost_usd"] - 2.5) < 1e-9
    # Prior window (7-14d ago) captures only the -10d row; the edge row
    # (-7d exactly) belongs to the current window per >= semantics.
    assert abs(result["prior_window_cost_usd"] - 2.0) < 1e-9


def test_filled_trades_and_null_action_excluded(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", None)

    # Filled = SELL_PUT (wheel) + SELL_CALL (iron_condor) = 2; SKIP + NULL excluded.
    assert result["filled_trades_count"] == 2
    assert result["cost_per_filled_trade_usd"] is not None
    assert abs(result["cost_per_filled_trade_usd"] - (2.5 / 2)) < 1e-9


def test_cost_by_outcome_buckets(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", None)
    outcome = result["cost_by_outcome"]

    assert outcome["open"]["count"] == 2
    assert abs(outcome["open"]["cost_usd"] - 1.7) < 1e-9
    assert outcome["close"]["count"] == 0
    # SKIP row + NULL-action row both land in "skip".
    assert outcome["skip"]["count"] == 2
    assert abs(outcome["skip"]["cost_usd"] - 0.8) < 1e-9


def test_cache_hit_rate(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", None)
    # total_read=100, total_creation=100 within the current window.
    assert abs(result["cache_hit_rate"] - 0.5) < 1e-9


def test_by_model_version(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", None)
    by_model = result["by_model_version"]

    assert by_model["claude-sonnet-4-6"]["count"] == 3
    assert abs(by_model["claude-sonnet-4-6"]["cost_usd"] - 1.8) < 1e-9
    assert by_model["claude-opus-4-7"]["count"] == 1
    assert abs(by_model["claude-opus-4-7"]["cost_usd"] - 0.7) < 1e-9


def test_strategy_types_filter(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", ["wheel"])

    # Excludes the iron_condor row: 3 decisions, cost 1.0+0.5+0.3=1.8.
    assert result["decisions_count"] == 3
    assert abs(result["total_cost_usd"] - 1.8) < 1e-9
    assert result["filled_trades_count"] == 1


def test_unknown_strategy_type_returns_zeros(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "7d", ["nonexistent_strategy"])

    assert result["decisions_count"] == 0
    assert result["total_cost_usd"] == 0.0
    assert result["cache_hit_rate"] is None
    assert result["by_model_version"] == {}


def test_all_window_includes_everything_and_empty_prior(seeded_conn):
    from api.server import _claude_costs_from_token_usage

    result = _claude_costs_from_token_usage(seeded_conn, "all", None)

    assert result["window"] == "all"
    assert result["decisions_count"] == 6
    assert abs(result["total_cost_usd"] - 9.5) < 1e-9
    assert result["prior_window_cost_usd"] == 0.0
