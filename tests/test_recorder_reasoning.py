"""Round-trip tests for reasoning serialization in TradeRecorder + DecisionRepository."""

import pytest

from database.db import Database
from database.recorder import TradeRecorder
from database.repositories import DecisionRepository


@pytest.fixture
def repo(tmp_path):
    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    rec = TradeRecorder(db.get_connection())
    yield rec, DecisionRepository(db.get_connection())
    db.close()


def _latest(decision_repo: DecisionRepository) -> dict:
    rows = decision_repo.get_recent(limit=1)
    assert rows, "expected at least one decision row"
    return rows[0]


def test_dict_reasoning_round_trips_as_dict(repo):
    rec, decisions = repo
    reasoning = {
        "macro": "VIX 24",
        "fundamental": "Earnings 30 days out",
        "technical": "Above 50-SMA",
        "volatility": "IV rank 42",
        "selection": "180P May 2",
        "risk": "<10% BP",
    }
    rec.record_decision(
        strategy_type="wheel", underlying="AAPL", action="SELL_PUT",
        reasoning=reasoning, confidence="high",
    )
    row = _latest(decisions)
    assert isinstance(row["reasoning"], dict)
    assert row["reasoning"]["macro"] == "VIX 24"
    assert row["reasoning"]["risk"] == "<10% BP"


def test_string_reasoning_round_trips_as_string(repo):
    rec, decisions = repo
    plain = "Guardrail rejected: Earnings in 14 days"
    rec.record_decision(
        strategy_type="wheel", underlying="AAPL", action="SKIP",
        reasoning=plain, confidence="medium",
    )
    row = _latest(decisions)
    # Plain prose isn't valid JSON → stays as a string. No double-encoding,
    # no surprise dict wrapping.
    assert isinstance(row["reasoning"], str)
    assert row["reasoning"] == plain


def test_none_reasoning_round_trips_as_none(repo):
    rec, decisions = repo
    rec.record_decision(
        strategy_type="wheel", underlying="AAPL", action="HOLD",
        reasoning=None, confidence="low",
    )
    row = _latest(decisions)
    assert row["reasoning"] is None


def test_legacy_python_repr_string_is_passed_through(repo):
    """Existing rows written before the fix used `str(dict)` — Python repr,
    not JSON. The read path must NOT crash on those, just leave them as
    strings so the frontend's string-fallback render path handles them.
    """
    rec, decisions = repo
    # Simulate a legacy row by writing a Python-repr string directly.
    legacy = "{'macro': 'test', 'risk': 'fine'}"  # single quotes — invalid JSON
    decisions.insert({
        "timestamp": "2026-04-12T10:00:00",
        "strategy_type": "wheel",
        "underlying": "LEGACY",
        "action": "SELL_PUT",
        "reasoning": legacy,
    })
    # Avoid pytest.warns; just confirm it round-trips without raising.
    row = _latest(decisions)
    assert isinstance(row["reasoning"], str)
    assert row["reasoning"] == legacy
    # Recorder reference kept to satisfy the unused-fixture-pair pattern.
    assert rec is not None
