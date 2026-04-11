"""Tests for data.spread_tracker.SpreadTracker."""

import json

import pytest

from data.spread_tracker import SpreadTracker


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "open_spreads.json"


@pytest.fixture
def tracker(state_path):
    return SpreadTracker(state_path=state_path)


def _sample_legs():
    return [
        {"symbol": "SPY250502P00530000", "side": "sell",
         "ratio_qty": 1, "position_intent": "sell_to_open"},
        {"symbol": "SPY250502P00525000", "side": "buy",
         "ratio_qty": 1, "position_intent": "buy_to_open"},
    ]


# ── register_spread ────────────────────────────────────────


class TestRegisterSpread:
    def test_persists_to_file(self, tracker, state_path):
        sid = tracker.register_spread(
            strategy_type="bull_put_spread",
            underlying="SPY",
            legs=_sample_legs(),
            entry_credit=1.20,
            entry_date="2026-04-11",
            expiration="2026-05-02",
            max_loss=380.0,
            max_gain=120.0,
        )

        assert sid  # non-empty UUID string
        assert state_path.exists()

        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["spread_id"] == sid
        assert data[0]["strategy_type"] == "bull_put_spread"
        assert data[0]["status"] == "open"
        assert data[0]["entry_credit"] == 1.20

    def test_multiple_spreads_same_underlying(self, tracker):
        sid1 = tracker.register_spread(
            strategy_type="bull_put_spread",
            underlying="SPY",
            legs=_sample_legs(),
            entry_credit=1.20,
            entry_date="2026-04-11",
            expiration="2026-05-02",
            max_loss=380.0,
            max_gain=120.0,
        )
        sid2 = tracker.register_spread(
            strategy_type="bear_call_spread",
            underlying="SPY",
            legs=[
                {"symbol": "SPY250502C00550000", "side": "sell"},
                {"symbol": "SPY250502C00555000", "side": "buy"},
            ],
            entry_credit=0.90,
            entry_date="2026-04-11",
            expiration="2026-05-02",
            max_loss=410.0,
            max_gain=90.0,
        )

        assert sid1 != sid2
        assert len(tracker.get_open_spreads()) == 2
        assert len(tracker.get_open_spreads(underlying="SPY")) == 2

    def test_filter_by_strategy_type(self, tracker):
        tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.0,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=400, max_gain=100,
        )
        tracker.register_spread(
            strategy_type="iron_condor", underlying="SPY",
            legs=_sample_legs(), entry_credit=2.0,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=300, max_gain=200,
        )

        assert len(tracker.get_open_spreads(strategy_type="iron_condor")) == 1


# ── get_spread_by_leg_symbol ────────────────────────────────


class TestGetSpreadByLegSymbol:
    def test_finds_correct_spread(self, tracker):
        tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )

        found = tracker.get_spread_by_leg_symbol("SPY250502P00530000")
        assert found is not None
        assert found["underlying"] == "SPY"

        found2 = tracker.get_spread_by_leg_symbol("SPY250502P00525000")
        assert found2 is not None
        assert found2["spread_id"] == found["spread_id"]

    def test_returns_none_for_unknown(self, tracker):
        tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )
        assert tracker.get_spread_by_leg_symbol("AAPL250509C00185000") is None

    def test_ignores_closed_spreads(self, tracker):
        sid = tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )
        tracker.close_spread(sid, exit_credit=0.30)

        assert tracker.get_spread_by_leg_symbol("SPY250502P00530000") is None


# ── close_spread ────────────────────────────────────────────


class TestCloseSpread:
    def test_marks_closed_with_pnl(self, tracker):
        sid = tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )
        tracker.close_spread(sid, exit_credit=0.30)

        spreads = tracker.get_open_spreads()
        assert len(spreads) == 0  # no open spreads

        all_spreads = tracker._spreads
        closed = [s for s in all_spreads if s["status"] == "closed"]
        assert len(closed) == 1
        assert closed[0]["pnl"] == 0.90  # 1.20 - 0.30
        assert closed[0]["closed_at"] is not None

    def test_close_without_exit_credit(self, tracker):
        sid = tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )
        tracker.close_spread(sid)

        closed = [s for s in tracker._spreads if s["status"] == "closed"]
        assert len(closed) == 1
        assert closed[0]["pnl"] is None


# ── reconcile_with_alpaca ───────────────────────────────────


class TestReconcileWithAlpaca:
    def test_detects_fully_closed(self, tracker):
        sid = tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )

        # Alpaca has no matching positions
        closed_ids = tracker.reconcile_with_alpaca([
            {"symbol": "AAPL250509C00185000"},
        ])
        assert sid in closed_ids

    def test_all_legs_present_not_flagged(self, tracker):
        sid = tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )

        closed_ids = tracker.reconcile_with_alpaca([
            {"symbol": "SPY250502P00530000"},
            {"symbol": "SPY250502P00525000"},
        ])
        assert closed_ids == []

    def test_partial_legs_not_in_closed_ids(self, tracker, caplog):
        """Partial leg removal is warned but not auto-closed."""
        import logging

        tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )

        with caplog.at_level(logging.WARNING, logger="data.spread_tracker"):
            closed_ids = tracker.reconcile_with_alpaca([
                {"symbol": "SPY250502P00530000"},
                # SPY250502P00525000 is missing
            ])

        assert closed_ids == []  # not auto-closed
        assert any("partial legs missing" in m.lower() for m in caplog.messages)


# ── persistence across instances ────────────────────────────


class TestPersistence:
    def test_state_survives_restart(self, state_path):
        t1 = SpreadTracker(state_path=state_path)
        sid = t1.register_spread(
            strategy_type="iron_condor", underlying="SPY",
            legs=_sample_legs(), entry_credit=2.50,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=250, max_gain=250,
        )

        t2 = SpreadTracker(state_path=state_path)
        assert len(t2.get_open_spreads()) == 1
        assert t2.get_open_spreads()[0]["spread_id"] == sid

    def test_close_persists(self, state_path):
        t1 = SpreadTracker(state_path=state_path)
        sid = t1.register_spread(
            strategy_type="bull_put_spread", underlying="AAPL",
            legs=[{"symbol": "A"}, {"symbol": "B"}], entry_credit=1.0,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=400, max_gain=100,
        )
        t1.close_spread(sid, exit_credit=0.20)

        t2 = SpreadTracker(state_path=state_path)
        assert len(t2.get_open_spreads()) == 0
        closed = [s for s in t2._spreads if s["status"] == "closed"]
        assert len(closed) == 1
        assert closed[0]["pnl"] == 0.80


# ── to_snapshot ─────────────────────────────────────────────


class TestToSnapshot:
    def test_returns_all_spreads(self, tracker):
        tracker.register_spread(
            strategy_type="bull_put_spread", underlying="SPY",
            legs=_sample_legs(), entry_credit=1.20,
            entry_date="2026-04-11", expiration="2026-05-02",
            max_loss=380, max_gain=120,
        )
        snapshot = tracker.to_snapshot()
        assert len(snapshot) == 1
        assert "spread_id" in snapshot[0]

    def test_empty_tracker_returns_empty(self, tracker):
        assert tracker.to_snapshot() == []
