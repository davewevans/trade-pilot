"""Tests for wheel strategy liquidity scoring integration.

Covers evaluate_entry_liquidity() across the four WheelState values,
the Tier D hard floor, kill-switch behaviour, and repo failure fallback.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from strategies.wheel_strategy import WheelState, WheelStrategy


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_strategy(multiplier=1.0, tier="B", confidence="none"):
    """Return a WheelStrategy with a mocked liquidity_repo."""
    mock_repo = MagicMock()
    mock_repo.get_multiplier.return_value = (multiplier, tier, confidence)

    # Stub out broker and state loading so __init__ doesn't touch the filesystem.
    mock_broker = MagicMock()
    with patch.object(WheelStrategy, "_load_state"):
        strategy = WheelStrategy(mock_broker, liquidity_repo=mock_repo)
    return strategy, mock_repo


def _make_strategy_no_repo():
    """Return a WheelStrategy with liquidity_repo=None."""
    mock_broker = MagicMock()
    with patch.object(WheelStrategy, "_load_state"):
        strategy = WheelStrategy(mock_broker, liquidity_repo=None)
    return strategy


# ── CSP entry (IDLE state) ────────────────────────────────────────────────────

def test_csp_entry_no_repo_disabled_confidence():
    """liquidity_repo=None → confidence='disabled', entry proceeds."""
    strategy = _make_strategy_no_repo()
    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.IDLE)
    assert result is None  # no skip
    assert context["_research"]["liquidity"]["confidence"] == "disabled"
    assert context["_research"]["liquidity"]["multiplier"] == 1.0


def test_csp_entry_tier_a_metadata_attached():
    """Tier A → None returned, metadata attached to context."""
    strategy, mock_repo = _make_strategy(multiplier=1.2, tier="A", confidence="high")
    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.IDLE)
    assert result is None
    liq = context["_research"]["liquidity"]
    assert liq["tier"] == "A"
    assert liq["confidence"] == "high"
    assert liq["multiplier"] == pytest.approx(1.2)
    mock_repo.get_multiplier.assert_called_once_with("AAPL", "wheel_csp")


def test_csp_entry_tier_d_returns_skip():
    """Tier D → SKIP dict returned, skip_reason='below_liquidity_floor'."""
    strategy, mock_repo = _make_strategy(multiplier=0.0, tier="D", confidence="high")
    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.IDLE)
    assert result is not None
    assert result["action"] == "SKIP"
    assert result["skip_reason"] == "below_liquidity_floor"
    assert result["_research"]["liquidity"]["tier"] == "D"
    assert result["_research"]["liquidity"]["multiplier"] == 0.0


def test_csp_entry_repo_raises_falls_back_to_neutral():
    """repo.get_multiplier() raising → falls back to (1.0, 'B', 'none'), entry proceeds."""
    strategy, mock_repo = _make_strategy()
    mock_repo.get_multiplier.side_effect = RuntimeError("db error")
    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.IDLE)
    assert result is None
    liq = context["_research"]["liquidity"]
    assert liq["tier"] == "B"
    assert liq["confidence"] == "none"
    assert liq["multiplier"] == pytest.approx(1.0)


def test_csp_entry_kill_switch_off_no_tier_d_skip(monkeypatch):
    """Kill switch off → multiplier=1.0, confidence='disabled', Tier D NOT skipped."""
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_SCORE_MULTIPLIER_ENABLED", False)

    # Even if DB has Tier D, get_multiplier returns (1.0, 'B', 'disabled') when
    # kill switch is off. Verify the strategy doesn't skip.
    strategy, mock_repo = _make_strategy()
    # Override what get_multiplier returns to simulate kill-switch-off behaviour
    mock_repo.get_multiplier.return_value = (1.0, "B", "disabled")

    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.IDLE)
    assert result is None  # no skip even though underlying data might be Tier D
    liq = context["_research"]["liquidity"]
    assert liq["multiplier"] == pytest.approx(1.0)
    assert liq["confidence"] == "disabled"


# ── CC entry (LONG_STOCK state) ───────────────────────────────────────────────

def test_cc_entry_uses_wheel_cc_strategy_type():
    """LONG_STOCK state → get_multiplier called with strategy_type='wheel_cc'."""
    strategy, mock_repo = _make_strategy(multiplier=1.0, tier="B", confidence="low")
    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.LONG_STOCK)
    assert result is None
    mock_repo.get_multiplier.assert_called_once_with("AAPL", "wheel_cc")
    assert context["_research"]["liquidity"]["tier"] == "B"


# ── Management states (no check) ─────────────────────────────────────────────

def test_short_put_state_no_liquidity_check():
    """SHORT_PUT = management state → get_multiplier never called."""
    strategy, mock_repo = _make_strategy()
    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.SHORT_PUT)
    assert result is None
    mock_repo.get_multiplier.assert_not_called()
    assert "_research" not in context


def test_short_call_state_no_liquidity_check():
    """SHORT_CALL = management state → get_multiplier never called."""
    strategy, mock_repo = _make_strategy()
    context = {}
    result = strategy.evaluate_entry_liquidity("AAPL", context, WheelState.SHORT_CALL)
    assert result is None
    mock_repo.get_multiplier.assert_not_called()
    assert "_research" not in context


# ── Recorder research_metadata round-trip ────────────────────────────────────

def test_recorder_research_metadata_round_trip(tmp_path):
    """research_metadata is persisted and readable from the decisions table."""
    from database.db import Database
    from database.recorder import TradeRecorder
    from database.repositories import DecisionRepository

    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    conn = db.get_connection()
    recorder = TradeRecorder(conn)
    decision_repo = DecisionRepository(conn)

    research = {"liquidity": {"tier": "A", "confidence": "high", "multiplier": 1.2}}
    recorder.record_decision(
        strategy_type="wheel",
        underlying="AAPL",
        action="SELL_PUT",
        wheel_state="IDLE",
        reasoning="test",
        research_metadata=research,
    )

    rows = decision_repo.get_recent(limit=1)
    assert rows, "expected a decision row"
    row = rows[0]
    import json
    stored = json.loads(row["research_metadata_json"])
    assert stored["liquidity"]["tier"] == "A"
    assert stored["liquidity"]["multiplier"] == pytest.approx(1.2)

    db.close()


def test_recorder_research_metadata_none_writes_null(tmp_path):
    """research_metadata=None → research_metadata_json column is NULL."""
    from database.db import Database
    from database.recorder import TradeRecorder
    from database.repositories import DecisionRepository

    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    conn = db.get_connection()
    recorder = TradeRecorder(conn)
    decision_repo = DecisionRepository(conn)

    recorder.record_decision(
        strategy_type="wheel",
        underlying="AAPL",
        action="SKIP",
        research_metadata=None,
    )

    rows = decision_repo.get_recent(limit=1)
    assert rows
    assert rows[0].get("research_metadata_json") is None

    db.close()
