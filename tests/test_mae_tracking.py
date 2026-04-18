"""Tests for max-adverse-excursion (MAE) tracking.

Covers:
- SpreadTracker.update_mae: correct updates, backward compat with pre-MAE records
- TradeJournal.update_mae + bulk_update_mae: correct updates, backward compat
- Simulated open → adverse move → favorable move → close sequence
- Spread MAE computation logic (as used by portfolio_refresh)
"""

import json
from pathlib import Path

import pytest

from data.spread_tracker import SpreadTracker, STATUS_OPEN
from data.trade_journal import TradeJournal


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_tracker(tmp_path: Path) -> SpreadTracker:
    return SpreadTracker(state_path=tmp_path / "open_spreads.json")


def _make_journal(tmp_path: Path) -> TradeJournal:
    return TradeJournal(path=tmp_path / "journal.jsonl")


def _register_and_open(tracker: SpreadTracker) -> str:
    """Register a bull-put spread and mark it open. Returns spread_id."""
    spread_id = tracker.register_spread(
        strategy_type="bull_put_spread",
        underlying="SPY",
        legs=[
            {"symbol": "SPY240119P00450000", "side": "sell"},
            {"symbol": "SPY240119P00440000", "side": "buy"},
        ],
        entry_credit=1.50,
        entry_date="2026-04-01",
        expiration="2026-04-19",
        max_loss=850.0,
        max_gain=150.0,
    )
    tracker.mark_open(spread_id, fill_price=1.50)
    return spread_id


# ── SpreadTracker MAE tests ───────────────────────────────────────────────────

def test_spread_mae_initialized_to_none(tmp_path):
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)
    spread = tracker._find(spread_id)
    assert spread["max_adverse_value"] is None
    assert spread["max_adverse_timestamp"] is None


def test_spread_mae_first_update(tmp_path):
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)
    tracker.update_mae(spread_id, -50.0, "2026-04-10T10:00:00")
    spread = tracker._find(spread_id)
    assert spread["max_adverse_value"] == -50.0
    assert spread["max_adverse_timestamp"] == "2026-04-10T10:00:00"


def test_spread_mae_worse_replaces(tmp_path):
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)
    tracker.update_mae(spread_id, -50.0, "2026-04-10T10:00:00")
    tracker.update_mae(spread_id, -120.0, "2026-04-10T11:00:00")  # worse
    spread = tracker._find(spread_id)
    assert spread["max_adverse_value"] == -120.0
    assert spread["max_adverse_timestamp"] == "2026-04-10T11:00:00"


def test_spread_mae_favorable_does_not_replace(tmp_path):
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)
    tracker.update_mae(spread_id, -120.0, "2026-04-10T11:00:00")
    tracker.update_mae(spread_id, -30.0, "2026-04-10T12:00:00")  # better — should NOT update
    spread = tracker._find(spread_id)
    assert spread["max_adverse_value"] == -120.0  # still worst
    assert spread["max_adverse_timestamp"] == "2026-04-10T11:00:00"


def test_spread_mae_positive_pl_no_adverse(tmp_path):
    """A profitable position: since stored is None, first value is always stored."""
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)
    tracker.update_mae(spread_id, 50.0, "2026-04-10T10:00:00")
    spread = tracker._find(spread_id)
    # 50.0 < None treated as first update, so it IS stored
    assert spread["max_adverse_value"] == 50.0


def test_spread_mae_persist_across_load(tmp_path):
    """MAE fields survive save/load round-trip."""
    path = tmp_path / "open_spreads.json"
    tracker = SpreadTracker(state_path=path)
    spread_id = _register_and_open(tracker)
    tracker.update_mae(spread_id, -200.0, "2026-04-10T09:00:00")

    # Reload from disk
    tracker2 = SpreadTracker(state_path=path)
    spread = tracker2._find(spread_id)
    assert spread["max_adverse_value"] == -200.0
    assert spread["max_adverse_timestamp"] == "2026-04-10T09:00:00"


def test_spread_mae_backward_compat(tmp_path):
    """Spreads loaded from disk without MAE fields are treated as None."""
    path = tmp_path / "open_spreads.json"
    # Write a pre-MAE spread directly (no MAE fields)
    old_spread = {
        "spread_id": "old-spread-001",
        "strategy_type": "bull_put_spread",
        "underlying": "QQQ",
        "legs": [],
        "entry_credit": 1.0,
        "entry_date": "2026-01-01",
        "expiration": "2026-01-19",
        "max_loss": 900.0,
        "max_gain": 100.0,
        "status": STATUS_OPEN,
        "entry_order_id": None,
        "close_order_id": None,
        "cb_status_at_entry": None,
        "original_dte": 45,
        "exit_credit": None,
        "pnl": None,
        "closed_at": None,
        "registered_at": "2026-01-01T09:00:00",
    }
    path.write_text(json.dumps([old_spread]), encoding="utf-8")

    tracker = SpreadTracker(state_path=path)
    spread = tracker._find("old-spread-001")
    # Missing fields should be treated as None via .get()
    assert spread.get("max_adverse_value") is None
    assert spread.get("max_adverse_timestamp") is None

    # Can update without error
    tracker.update_mae("old-spread-001", -75.0, "2026-04-10T10:00:00")
    spread = tracker._find("old-spread-001")
    assert spread["max_adverse_value"] == -75.0


def test_spread_mae_unknown_id_no_error(tmp_path):
    """update_mae on a nonexistent spread_id logs a warning and does not raise."""
    tracker = _make_tracker(tmp_path)
    try:
        tracker.update_mae("nonexistent-id", -50.0, "2026-04-10T10:00:00")
    except Exception:
        pytest.fail("update_mae raised unexpectedly for unknown spread_id")


def test_spread_mae_full_lifecycle(tmp_path):
    """Open → adverse move → favorable move → close leaves worst value."""
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)

    tracker.update_mae(spread_id, -30.0, "2026-04-10T10:00:00")
    tracker.update_mae(spread_id, -80.0, "2026-04-10T11:00:00")   # worst
    tracker.update_mae(spread_id, -40.0, "2026-04-10T12:00:00")   # recovers
    tracker.update_mae(spread_id, 10.0, "2026-04-10T13:00:00")    # back in profit
    tracker.close_spread(spread_id, exit_credit=0.20)

    spread = tracker._find(spread_id)
    assert spread["max_adverse_value"] == -80.0
    assert spread["max_adverse_timestamp"] == "2026-04-10T11:00:00"


# ── TradeJournal MAE tests ───────────────────────────────────────────────────

def _append_entry(journal: TradeJournal, order_id: str, contract_symbol: str) -> None:
    journal.append({
        "symbol": None,
        "underlying": "SPY",
        "action": "sell_put",
        "contract_symbol": contract_symbol,
        "order_id": order_id,
        "status": "filled",
        "fill_price": 1.50,
        "strategy_type": "wheel_csp",
    })


def test_journal_mae_initialized_to_none(tmp_path):
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] is None
    assert entries[0]["max_adverse_timestamp"] is None


def test_journal_mae_first_update(tmp_path):
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    journal.update_mae("ord-001", -60.0, "2026-04-10T10:00:00")
    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] == -60.0


def test_journal_mae_worse_replaces(tmp_path):
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    journal.update_mae("ord-001", -60.0, "2026-04-10T10:00:00")
    journal.update_mae("ord-001", -150.0, "2026-04-10T11:00:00")
    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] == -150.0


def test_journal_mae_better_does_not_replace(tmp_path):
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    journal.update_mae("ord-001", -150.0, "2026-04-10T11:00:00")
    journal.update_mae("ord-001", -30.0, "2026-04-10T12:00:00")
    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] == -150.0


def test_journal_mae_unknown_order_id_no_error(tmp_path):
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    # Should not raise
    journal.update_mae("nonexistent", -60.0, "2026-04-10T10:00:00")
    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] is None  # unchanged


def test_journal_mae_backward_compat(tmp_path):
    """Pre-MAE entries (no MAE keys) are updated cleanly."""
    path = tmp_path / "journal.jsonl"
    old_entry = {
        "timestamp": "2026-01-01T09:00:00",
        "version": "1.0.0",
        "underlying": "SPY",
        "order_id": "ord-old",
        "contract_symbol": "SPY240119P00450000",
        "status": "filled",
        "action": "sell_put",
    }
    path.write_text(json.dumps(old_entry) + "\n", encoding="utf-8")

    journal = TradeJournal(path=path)
    journal.update_mae("ord-old", -55.0, "2026-04-10T10:00:00")
    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] == -55.0


def test_journal_bulk_update_mae(tmp_path):
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    _append_entry(journal, "ord-002", "QQQ240119P00400000")

    updated = journal.bulk_update_mae({
        "SPY240119P00450000": (-80.0, "2026-04-10T10:00:00"),
        "QQQ240119P00400000": (-30.0, "2026-04-10T10:00:00"),
    })
    assert updated == 2

    entries = journal._read_all()
    spy_entry = next(e for e in entries if e["order_id"] == "ord-001")
    qqq_entry = next(e for e in entries if e["order_id"] == "ord-002")
    assert spy_entry["max_adverse_value"] == -80.0
    assert qqq_entry["max_adverse_value"] == -30.0


def test_journal_bulk_update_mae_only_worse(tmp_path):
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    journal.update_mae("ord-001", -100.0, "2026-04-10T10:00:00")

    # Send a better value — should NOT update
    updated = journal.bulk_update_mae({
        "SPY240119P00450000": (-20.0, "2026-04-10T11:00:00"),
    })
    assert updated == 0
    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] == -100.0


def test_journal_bulk_update_mae_empty_map(tmp_path):
    """bulk_update_mae with empty map returns 0 and does not write."""
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")
    updated = journal.bulk_update_mae({})
    assert updated == 0


def test_journal_mae_full_lifecycle(tmp_path):
    """Open → adverse → recovery → close: worst value preserved."""
    journal = _make_journal(tmp_path)
    _append_entry(journal, "ord-001", "SPY240119P00450000")

    journal.update_mae("ord-001", -20.0, "2026-04-10T10:00:00")
    journal.update_mae("ord-001", -90.0, "2026-04-10T11:00:00")   # worst
    journal.update_mae("ord-001", -50.0, "2026-04-10T12:00:00")
    journal.update_mae("ord-001", 15.0, "2026-04-10T13:00:00")

    journal.update("ord-001", {"status": "closed", "closed_at": "2026-04-10T14:00:00", "pnl": 15.0})

    entries = journal._read_all()
    assert entries[0]["max_adverse_value"] == -90.0


# ── Portfolio refresh MAE logic tests ─────────────────────────────────────────

def test_portfolio_refresh_mae_spread_logic(tmp_path):
    """Verify spread MAE computation (sum of legs) works correctly."""
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)

    # Simulate what portfolio_refresh does: sum leg unrealized_pl
    position_pl = {
        "SPY240119P00450000": -75.0,  # short leg adverse
        "SPY240119P00440000": 25.0,   # long leg partially offsets
    }
    spread = tracker._find(spread_id)
    spread_pl = sum(
        position_pl.get((leg.get("symbol") or "").upper(), 0.0)
        for leg in spread.get("legs", [])
    )
    tracker.update_mae(spread_id, spread_pl, "2026-04-10T10:00:00")

    spread2 = tracker._find(spread_id)
    assert spread2["max_adverse_value"] == -50.0  # -75 + 25


def test_portfolio_refresh_mae_spread_logic_multiple_ticks(tmp_path):
    """Multiple ticks: only worst (most negative) value is retained."""
    tracker = _make_tracker(tmp_path)
    spread_id = _register_and_open(tracker)

    ticks = [
        {"SPY240119P00450000": -10.0, "SPY240119P00440000": 5.0},   # -5
        {"SPY240119P00450000": -60.0, "SPY240119P00440000": 20.0},  # -40 (worst)
        {"SPY240119P00450000": -25.0, "SPY240119P00440000": 10.0},  # -15 (recovery)
    ]
    for position_pl in ticks:
        spread = tracker._find(spread_id)
        spread_pl = sum(
            position_pl.get((leg.get("symbol") or "").upper(), 0.0)
            for leg in spread.get("legs", [])
        )
        tracker.update_mae(spread_id, spread_pl, "2026-04-10T10:00:00")

    spread = tracker._find(spread_id)
    assert spread["max_adverse_value"] == -40.0


def test_portfolio_refresh_mae_exception_does_not_propagate(tmp_path):
    """update_mae with unknown spread_id logs a warning and does not raise."""
    tracker = _make_tracker(tmp_path)
    _register_and_open(tracker)

    try:
        tracker.update_mae("nonexistent-id", -50.0, "2026-04-10T10:00:00")
    except Exception:
        pytest.fail("update_mae raised unexpectedly")
