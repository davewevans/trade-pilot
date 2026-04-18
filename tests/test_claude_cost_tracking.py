"""Tests for Claude API cost tracking (Phase A).

Covers:
1. Cost calculation correctness for a mocked response.usage with known tokens.
2. Graceful handling of an unknown model_version (cost = None, no crash).
3. Graceful handling of response.usage missing cache fields (default to 0).
4. The /api/claude-costs endpoint returns the correct shape and handles
   windows with zero decisions without erroring.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from database.db import Database
from database.recorder import TradeRecorder


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_advisor(model: str = "claude-sonnet-4-6"):
    """Construct a ClaudeAdvisor without touching Anthropic or the filesystem."""
    from ai.claude_advisor import ClaudeAdvisor
    from strategies.wheel_strategy import WheelState

    with patch("ai.claude_advisor.anthropic.Anthropic"), \
         patch("ai.claude_advisor.settings"):
        advisor = ClaudeAdvisor.__new__(ClaudeAdvisor)
        advisor.client = MagicMock()
        advisor.model = model
        advisor._last_usage = None
        advisor._api_usage_repo = None
        advisor.thinking_mode = "off"
        advisor.phase_prompts = {s: "" for s in WheelState}
        advisor.spread_prompts = {}
        advisor.system_prompt = ""
    return advisor


def _make_usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> MagicMock:
    """Build a mock Anthropic UsageBlock."""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.cache_read_input_tokens = cache_read
    usage.cache_creation_input_tokens = cache_creation
    usage.model_dump.return_value = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }
    return usage


def _make_response(payload: dict, stop_reason: str = "end_turn", **usage_kwargs):
    """Build a mock Anthropic response."""
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = [MagicMock()]
    resp.content[0].text = json.dumps(payload)
    resp.usage = _make_usage(**usage_kwargs)
    return resp


# ── Test 1: Cost calculation correctness ─────────────────────────────────────

def test_cost_calculation_known_tokens():
    """_build_usage_dict must compute cost correctly against known pricing."""
    advisor = _make_advisor("claude-sonnet-4-6")
    usage = _make_usage(
        input_tokens=1_000_000,   # $3.00
        output_tokens=100_000,    # $1.50
        cache_read=500_000,       # $0.15
        cache_creation=200_000,   # $1.20
    )
    result = advisor._build_usage_dict(usage)

    assert result is not None
    assert result["model_version"] == "claude-sonnet-4-6"
    assert result["input_tokens"] == 1_000_000
    assert result["output_tokens"] == 100_000
    assert result["cache_read_tokens"] == 500_000
    assert result["cache_creation_tokens"] == 200_000

    # Expected: (1.0*3.00) + (0.1*15.00) + (0.5*0.30) + (0.2*6.00)
    #         = 3.00 + 1.50 + 0.15 + 1.20 = 5.85
    assert abs(result["estimated_cost_usd"] - 5.85) < 1e-5


def test_cost_calculation_zero_tokens():
    """Zero token counts should give zero cost."""
    advisor = _make_advisor("claude-sonnet-4-6")
    usage = _make_usage(0, 0, 0, 0)
    result = advisor._build_usage_dict(usage)
    assert result["estimated_cost_usd"] == 0.0


# ── Test 2: Unknown model version ─────────────────────────────────────────────

def test_unknown_model_version_stores_null_cost(caplog):
    """Advisor must log a warning and set estimated_cost_usd = None, not crash."""
    advisor = _make_advisor("claude-unknown-99")
    usage = _make_usage(input_tokens=100, output_tokens=50)

    import logging
    with caplog.at_level(logging.WARNING, logger="ai.claude_advisor"):
        result = advisor._build_usage_dict(usage)

    assert result is not None
    assert result["estimated_cost_usd"] is None
    assert "Unknown model version" in caplog.text


# ── Test 3: Missing cache fields default to 0 ─────────────────────────────────

def test_missing_cache_fields_default_to_zero():
    """If cache fields are absent on the usage object, treat them as 0."""
    advisor = _make_advisor("claude-sonnet-4-6")

    # Build a usage mock that lacks the cache attributes entirely
    usage = MagicMock(spec=["input_tokens", "output_tokens", "model_dump"])
    usage.input_tokens = 200
    usage.output_tokens = 100
    usage.model_dump.return_value = {"input_tokens": 200, "output_tokens": 100}
    # spec means getattr(usage, 'cache_read_input_tokens') would raise
    # AttributeError — but _build_usage_dict uses getattr(..., 0) so it's safe.

    result = advisor._build_usage_dict(usage)

    assert result["cache_read_tokens"] == 0
    assert result["cache_creation_tokens"] == 0
    assert result["estimated_cost_usd"] is not None  # should still compute


# ── Test 4: /api/claude-costs endpoint ────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path):
    db = Database(path=str(tmp_path / "test_costs.db"))
    db.init_schema()
    yield db
    db.close()


def test_claude_costs_empty_db(test_db):
    """Endpoint returns valid zero-filled shape when no decisions have cost data."""
    from api.server import _claude_costs_from_db

    result = _claude_costs_from_db(test_db.get_connection(), "7d", None)

    assert result["window"] == "7d"
    assert result["total_cost_usd"] == 0.0
    assert result["decisions_count"] == 0
    assert result["filled_trades_count"] == 0
    assert result["cost_per_filled_trade_usd"] is None
    assert result["cache_hit_rate"] is None
    assert isinstance(result["cost_by_outcome"], dict)
    for bucket in ("open", "close", "skip"):
        assert bucket in result["cost_by_outcome"]


def test_claude_costs_with_decisions(test_db):
    """Endpoint sums cost correctly across multiple decision rows."""
    conn = test_db.get_connection()
    recorder = TradeRecorder(conn)

    usage_skip = {
        "model_version": "claude-sonnet-4-6",
        "input_tokens": 1000,
        "output_tokens": 50,
        "cache_read_tokens": 800,
        "cache_creation_tokens": 0,
        "estimated_cost_usd": 0.001,
    }
    usage_open = {
        "model_version": "claude-sonnet-4-6",
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_tokens": 800,
        "cache_creation_tokens": 0,
        "estimated_cost_usd": 0.002,
    }

    # Two skips + one open
    recorder.record_decision(
        strategy_type="wheel", underlying="AAPL", action="SKIP",
        usage_data=usage_skip,
    )
    recorder.record_decision(
        strategy_type="wheel", underlying="MSFT", action="SKIP",
        usage_data=usage_skip,
    )
    recorder.record_decision(
        strategy_type="wheel", underlying="AAPL", action="SELL_PUT",
        usage_data=usage_open,
    )

    from api.server import _claude_costs_from_db
    result = _claude_costs_from_db(conn, "7d", None)

    assert result["decisions_count"] == 3
    assert abs(result["total_cost_usd"] - 0.004) < 1e-6
    # filled = rows where action not in SKIP/HOLD = 1
    assert result["filled_trades_count"] == 1
    assert result["cost_per_filled_trade_usd"] is not None
    assert result["by_model_version"]["claude-sonnet-4-6"]["count"] == 3


def test_claude_costs_all_window(test_db):
    """'all' window returns all decisions and prior_window_cost = 0."""
    conn = test_db.get_connection()
    recorder = TradeRecorder(conn)

    recorder.record_decision(
        strategy_type="wheel", underlying="SPY", action="SKIP",
        usage_data={
            "model_version": "claude-sonnet-4-6",
            "input_tokens": 500, "output_tokens": 40,
            "cache_read_tokens": 400, "cache_creation_tokens": 0,
            "estimated_cost_usd": 0.0005,
        },
    )

    from api.server import _claude_costs_from_db
    result = _claude_costs_from_db(conn, "all", None)

    assert result["window"] == "all"
    assert result["prior_window_cost_usd"] == 0.0
    assert result["decisions_count"] == 1


def test_claude_costs_null_usage_not_counted(test_db):
    """Decisions with no cost data (pre-check skips) are excluded from totals."""
    conn = test_db.get_connection()
    recorder = TradeRecorder(conn)

    # Insert one skip with no usage data
    recorder.record_decision(
        strategy_type="wheel", underlying="AAPL", action="SKIP",
        usage_data=None,
    )
    # Insert one skip with usage data
    recorder.record_decision(
        strategy_type="wheel", underlying="MSFT", action="SKIP",
        usage_data={
            "model_version": "claude-sonnet-4-6",
            "input_tokens": 100, "output_tokens": 10,
            "cache_read_tokens": 80, "cache_creation_tokens": 0,
            "estimated_cost_usd": 0.0001,
        },
    )

    from api.server import _claude_costs_from_db
    result = _claude_costs_from_db(conn, "7d", None)

    # Only the row with estimated_cost_usd IS NOT NULL counts
    assert result["decisions_count"] == 1
    assert abs(result["total_cost_usd"] - 0.0001) < 1e-8
