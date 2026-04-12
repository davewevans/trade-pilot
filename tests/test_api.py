"""Tests for the FastAPI dashboard endpoints."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    """Redirect all snapshot/journal/db paths to a temp directory."""
    snap = tmp_path / "snapshots"
    snap.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    journal = data / "journal.jsonl"
    db_path = tmp_path / "test.db"

    import api.server as srv

    monkeypatch.setattr(srv, "SNAPSHOTS", snap)
    monkeypatch.setattr(srv, "DATA_DIR", tmp_path)
    monkeypatch.setattr(srv, "JOURNAL_PATH", journal)
    monkeypatch.setattr(srv, "LOCK_PATH", tmp_path / "HALTED.lock")
    monkeypatch.setattr(srv, "DB_PATH", db_path)

    # Expose to tests via the request fixture
    _patch_paths.snap = snap
    _patch_paths.data = data
    _patch_paths.journal = journal
    _patch_paths.tmp = tmp_path
    _patch_paths.db_path = db_path


@pytest.fixture
def client():
    from api.server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def snap():
    return _patch_paths.snap


@pytest.fixture
def journal_path():
    return _patch_paths.journal


@pytest.fixture
def tmp():
    return _patch_paths.tmp


@pytest.fixture
def db_path():
    return _patch_paths.db_path


@pytest.fixture
def db(db_path):
    """Initialized Database instance scoped to this test's tmp_path."""
    from database.db import Database

    db = Database(path=str(db_path))
    db.init_schema()
    yield db
    db.close()


def _seed_decision(db, **fields):
    """Insert a decision row with sensible defaults."""
    from database.repositories import DecisionRepository

    repo = DecisionRepository(db.get_connection())
    payload = {
        "timestamp": "2026-04-11T10:00:00",
        "strategy_type": "wheel",
        "underlying": "SPY",
        "action": "SELL_PUT",
        "reasoning": "test",
    }
    payload.update(fields)
    return repo.insert(payload)


def _seed_trade(db, **fields):
    """Insert a trade row with sensible defaults (filled by default)."""
    from database.repositories import CycleRepository, TradeRepository

    cycles = CycleRepository(db.get_connection())
    cycle_id = fields.pop("cycle_id", None) or cycles.insert({
        "strategy_type": fields.get("strategy_type", "wheel"),
        "underlying": fields.get("underlying", "SPY"),
    })
    repo = TradeRepository(db.get_connection())
    payload = {
        "cycle_id": cycle_id,
        "alpaca_order_id": fields.pop("alpaca_order_id", "ord_test"),
        "underlying": "SPY",
        "strategy_type": "wheel",
        "trade_type": "SELL_PUT",
        "symbol": "SPY260515P00485000",
        "limit_price": 1.25,
        "fill_price": 1.20,
        "fill_status": "filled",
        "submitted_at": "2026-04-11T10:00:00",
        "filled_at": "2026-04-11T10:00:30",
        "contracts": 1,
    }
    payload.update(fields)
    return repo.insert(payload)


# ── /api/health ─────────────────────────────────────────────


class TestHealth:
    def test_returns_200(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "timestamp" in body
        assert body["halted"] is False

    def test_halted_when_lock_exists(self, client, tmp):
        (tmp / "HALTED.lock").write_text("{}")
        r = client.get("/api/health")
        assert r.json()["halted"] is True


# ── /api/portfolio ──────────────────────────────────────────


class TestPortfolio:
    def test_503_when_no_snapshot(self, client):
        r = client.get("/api/portfolio")
        assert r.status_code == 503
        assert "error" in r.json()

    def test_returns_snapshot(self, client, snap):
        payload = {"timestamp": "2026-04-11", "account": {"total_equity": 100000}}
        (snap / "portfolio.json").write_text(json.dumps(payload))
        r = client.get("/api/portfolio")
        assert r.status_code == 200
        assert r.json()["account"]["total_equity"] == 100000


# ── /api/context ────────────────────────────────────────────


class TestContext:
    def test_503_when_missing(self, client):
        r = client.get("/api/context")
        assert r.status_code == 503

    def test_returns_context(self, client, snap):
        (snap / "context.json").write_text(json.dumps({"vix": 18.5}))
        r = client.get("/api/context")
        assert r.status_code == 200
        assert r.json()["vix"] == 18.5


# ── /api/circuit-breakers ──────────────────────────────────


class TestCircuitBreakers:
    def test_503_when_missing(self, client):
        r = client.get("/api/circuit-breakers")
        assert r.status_code == 503

    def test_returns_data(self, client, snap):
        (snap / "circuit_breakers.json").write_text(
            json.dumps({"status": "GREEN", "halted": False})
        )
        r = client.get("/api/circuit-breakers")
        assert r.status_code == 200
        assert r.json()["status"] == "GREEN"


# ── /api/decisions ──────────────────────────────────────────


class TestDecisions:
    def test_empty_when_no_db_and_no_file(self, client):
        r = client.get("/api/decisions")
        assert r.status_code == 200
        body = r.json()
        assert body["decisions"] == []
        assert body["total"] == 0

    def test_returns_decisions_most_recent_first(self, client, db):
        for i in range(5):
            _seed_decision(
                db,
                timestamp=f"2026-04-11T10:0{i}:00",
                underlying="SPY",
                action="SELL_PUT",
            )
        r = client.get("/api/decisions")
        body = r.json()
        assert body["total"] == 5
        assert body["decisions"][0]["timestamp"] == "2026-04-11T10:04:00"

    def test_limit_parameter(self, client, db):
        for i in range(20):
            _seed_decision(
                db,
                timestamp=f"2026-04-11T10:{i:02d}:00",
                underlying="X",
                action="SELL_PUT",
            )
        r = client.get("/api/decisions?limit=5")
        body = r.json()
        assert len(body["decisions"]) == 5
        assert body["total"] == 20

    def test_filter_by_underlying(self, client, db):
        _seed_decision(db, action="SELL_PUT", underlying="SPY")
        _seed_decision(db, action="SELL_PUT", underlying="AAPL")
        _seed_decision(db, action="SKIP", underlying="SPY")
        r = client.get("/api/decisions?underlying=SPY")
        body = r.json()
        assert body["total"] == 2
        assert all(d["underlying"] == "SPY" for d in body["decisions"])

    def test_filter_by_action(self, client, db):
        _seed_decision(db, action="SKIP", underlying="SPY")
        _seed_decision(db, action="SELL_PUT", underlying="SPY")
        r = client.get("/api/decisions?action=SKIP")
        body = r.json()
        assert body["total"] == 1
        assert body["decisions"][0]["action"] == "SKIP"

    def test_db_wins_over_jsonl(self, client, db, snap):
        """When both DB rows and the JSONL file exist, DB takes precedence."""
        _seed_decision(db, underlying="SPY", action="SELL_PUT")
        # JSONL has a different row that should NOT appear in the response.
        (snap / "decisions.jsonl").write_text(
            json.dumps({"underlying": "AAPL", "action": "sell_call"}) + "\n"
        )
        r = client.get("/api/decisions")
        body = r.json()
        assert body["total"] == 1
        assert body["decisions"][0]["underlying"] == "SPY"

    def test_falls_back_to_jsonl_when_db_missing(self, client, snap, db_path):
        """If the DB file isn't there, the JSONL fallback path runs."""
        # Don't initialize a DB — db_path file doesn't exist.
        assert not db_path.exists()
        (snap / "decisions.jsonl").write_text(
            json.dumps({"underlying": "AAPL", "action": "sell_put", "timestamp": "x"}) + "\n"
        )
        r = client.get("/api/decisions")
        body = r.json()
        assert body["total"] == 1
        assert body["decisions"][0]["underlying"] == "AAPL"

    def test_filter_by_confidence(self, client, db):
        _seed_decision(db, action="SELL_PUT", confidence=0.9, underlying="A")
        _seed_decision(db, action="SELL_PUT", confidence=0.6, underlying="B")
        _seed_decision(db, action="SELL_PUT", confidence=0.3, underlying="C")
        body = client.get("/api/decisions?confidence=0.9").json()
        assert body["total"] == 1
        assert body["decisions"][0]["underlying"] == "A"

    def test_offset_paginates_correctly(self, client, db):
        # Seed 5 rows with strictly-increasing timestamps so DESC order is
        # deterministic.
        for i in range(5):
            _seed_decision(
                db,
                timestamp=f"2026-04-12T10:0{i}:00",
                action="SELL_PUT",
                underlying=f"X{i}",
            )
        # Most-recent-first → expected order: X4, X3, X2, X1, X0
        body = client.get("/api/decisions?limit=2&offset=2").json()
        assert body["total"] == 5  # full filtered count, ignoring offset
        assert [d["underlying"] for d in body["decisions"]] == ["X2", "X1"]

    def test_confidence_offset_combine_with_account(self, client, db):
        # Mixed strategies + confidences. Only wheel rows with conf=0.9 match.
        _seed_decision(db, strategy_type="wheel", confidence=0.9, underlying="W1")
        _seed_decision(db, strategy_type="wheel", confidence=0.6, underlying="W2")
        _seed_decision(db, strategy_type="iron_condor", confidence=0.9, underlying="I1")
        body = client.get("/api/decisions?account=wheel&confidence=0.9").json()
        assert body["total"] == 1
        assert body["decisions"][0]["underlying"] == "W1"


# ── /api/decisions/stats ────────────────────────────────────


class TestDecisionStats:
    def test_empty_stats(self, client):
        r = client.get("/api/decisions/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_decisions"] == 0
        assert body["win_rate"] == 0.0

    def test_computed_stats_from_db(self, client, db):
        _seed_decision(db, action="SELL_PUT", underlying="SPY", reasoning="strong setup")
        _seed_decision(db, action="SKIP", underlying="AAPL", reasoning="Earnings too close")

        # Two filled trades: one profitable SELL_PUT, one losing BUY_PUT close.
        _seed_trade(
            db, alpaca_order_id="ord_a", trade_type="SELL_PUT",
            fill_price=2.00, contracts=1,  # +$200
        )
        _seed_trade(
            db, alpaca_order_id="ord_b", trade_type="BUY_PUT",
            fill_price=0.50, contracts=1,  # -$50
        )

        r = client.get("/api/decisions/stats")
        body = r.json()
        assert body["total_decisions"] == 2
        assert body["trades"] == 1   # everything except SKIP/HOLD
        assert body["skips"] == 1
        assert body["win_rate"] == 50.0  # 1 of 2 trades positive
        # iv_rank not currently populated — null per design note 3
        assert body["avg_iv_rank_at_entry"] is None
        assert body["decisions_by_underlying"]["SPY"] == 1
        assert body["skip_reasons"] == {"Earnings too close": 1}

    def test_db_wins_over_jsonl(self, client, db, snap, journal_path):
        _seed_decision(db, action="SELL_PUT", underlying="SPY")
        # JSONL has different data — should not be used.
        (snap / "decisions.jsonl").write_text(
            json.dumps({"action": "skip", "underlying": "AAPL", "action_taken": False}) + "\n"
        )
        journal_path.write_text(
            json.dumps({"pnl": -999, "closed_at": "2026-04-10"}) + "\n"
        )
        r = client.get("/api/decisions/stats")
        body = r.json()
        assert body["total_decisions"] == 1
        assert body["skips"] == 0
        assert body["decisions_by_underlying"] == {"SPY": 1}

    def test_falls_back_to_jsonl_when_db_missing(self, client, snap, journal_path, db_path):
        assert not db_path.exists()
        (snap / "decisions.jsonl").write_text(
            json.dumps({"action": "sell_put", "action_taken": True, "underlying": "SPY",
                        "key_inputs": {"iv_rank": 55}}) + "\n"
        )
        journal_path.write_text(
            json.dumps({"pnl": 150, "closed_at": "2026-04-10"}) + "\n"
        )
        r = client.get("/api/decisions/stats")
        body = r.json()
        assert body["total_decisions"] == 1
        assert body["avg_iv_rank_at_entry"] == 55.0
        assert body["win_rate"] == 100.0


# ── /api/performance ────────────────────────────────────────


class TestTrades:
    def test_empty_when_no_filled(self, client, db):
        # DB exists but no filled trades.
        r = client.get("/api/trades")
        assert r.status_code == 200
        body = r.json()
        assert body["trades"] == []
        assert body["total"] == 0

    def test_filter_by_account(self, client, db):
        _seed_trade(
            db, alpaca_order_id="o_w", strategy_type="wheel",
            trade_type="SELL_PUT", fill_price=1.0, filled_at="2026-04-10T10:00:00",
        )
        _seed_trade(
            db, alpaca_order_id="o_ic", strategy_type="iron_condor",
            trade_type="SELL_PUT", fill_price=1.0, filled_at="2026-04-10T10:01:00",
        )
        _seed_trade(
            db, alpaca_order_id="o_bp", strategy_type="bull_put_spread",
            trade_type="SELL_PUT", fill_price=1.0, filled_at="2026-04-10T10:02:00",
        )
        body = client.get("/api/trades?account=wheel").json()
        assert body["total"] == 1
        assert body["trades"][0]["strategy_type"] == "wheel"

        body = client.get("/api/trades?account=spreads").json()
        assert body["total"] == 1
        assert body["trades"][0]["strategy_type"] == "bull_put_spread"

    def test_filter_by_underlying(self, client, db):
        _seed_trade(
            db, alpaca_order_id="o_spy", underlying="SPY",
            trade_type="SELL_PUT", fill_price=1.0, filled_at="2026-04-10T10:00:00",
        )
        _seed_trade(
            db, alpaca_order_id="o_aapl", underlying="AAPL",
            trade_type="SELL_PUT", fill_price=1.0, filled_at="2026-04-10T10:01:00",
        )
        body = client.get("/api/trades?underlying=spy").json()
        assert body["total"] == 1
        assert body["trades"][0]["underlying"] == "SPY"

    def test_pending_excluded(self, client, db):
        _seed_trade(
            db, alpaca_order_id="o_pending", trade_type="SELL_PUT",
            fill_price=None, fill_status="pending", filled_at=None,
        )
        _seed_trade(
            db, alpaca_order_id="o_filled", trade_type="SELL_PUT",
            fill_price=1.0, filled_at="2026-04-10T10:00:00",
        )
        body = client.get("/api/trades").json()
        assert body["total"] == 1
        assert body["trades"][0]["alpaca_order_id"] == "o_filled"

    def test_falls_back_to_jsonl_when_db_missing(self, client, journal_path, db_path):
        assert not db_path.exists()
        journal_path.write_text(
            "\n".join([
                json.dumps({
                    "timestamp": "2026-04-10T10:00:00",
                    "underlying": "SPY", "action": "sell_put",
                    "contract_symbol": "SPY260515P00485000",
                    "fill_price": 1.5, "qty": 1, "wheel_state": "IDLE",
                    "closed_at": "2026-04-10T10:01:00",
                }),
                json.dumps({
                    "timestamp": "2026-04-10T10:02:00",
                    "underlying": "AAPL", "action": "skip",
                    "fill_price": None,
                }),
            ]) + "\n"
        )
        body = client.get("/api/trades").json()
        assert body["total"] == 1  # skip with null fill_price excluded
        assert body["trades"][0]["underlying"] == "SPY"


class TestPerformance:
    def test_empty_when_no_db_and_no_journal(self, client):
        r = client.get("/api/performance")
        assert r.status_code == 200
        body = r.json()
        assert body["total_pnl"] == 0
        assert body["equity_curve"] == []

    def test_with_db_trades(self, client, db):
        # SELL_PUT at $2.00 × 100 × 1 contract = +$200
        _seed_trade(
            db, alpaca_order_id="ord_a", trade_type="SELL_PUT",
            fill_price=2.00, contracts=1, filled_at="2026-04-10T10:00:00",
        )
        # BUY_PUT at $0.50 × 100 × 1 contract = -$50
        _seed_trade(
            db, alpaca_order_id="ord_b", trade_type="BUY_PUT",
            fill_price=0.50, contracts=1, filled_at="2026-04-11T10:00:00",
        )

        r = client.get("/api/performance")
        body = r.json()
        assert body["total_pnl"] == 150.0
        assert body["realized_pnl"] == 150.0
        assert body["best_trade"]["pnl"] == 200.0
        assert body["worst_trade"]["pnl"] == -50.0
        # Equity curve: one point per date, cumulative
        assert body["equity_curve"] == [
            {"date": "2026-04-10", "cumulative_pnl": 200.0},
            {"date": "2026-04-11", "cumulative_pnl": 150.0},
        ]

    def test_pending_trades_excluded(self, client, db):
        """fill_price IS NULL → not counted as a $0 trade."""
        _seed_trade(
            db, alpaca_order_id="ord_pending", trade_type="SELL_PUT",
            fill_price=None, fill_status="pending", filled_at=None,
        )
        _seed_trade(
            db, alpaca_order_id="ord_filled", trade_type="SELL_PUT",
            fill_price=1.00, contracts=1, filled_at="2026-04-10T10:00:00",
        )
        r = client.get("/api/performance")
        body = r.json()
        assert body["total_pnl"] == 100.0  # only the filled one

    def test_db_wins_over_jsonl(self, client, db, journal_path):
        _seed_trade(
            db, alpaca_order_id="ord_a", trade_type="SELL_PUT",
            fill_price=1.00, contracts=1, filled_at="2026-04-10T10:00:00",
        )
        journal_path.write_text(
            json.dumps({"timestamp": "x", "pnl": 9999, "fill_price": 1.0, "closed_at": "y"}) + "\n"
        )
        r = client.get("/api/performance")
        body = r.json()
        # DB number, not the JSONL number
        assert body["total_pnl"] == 100.0

    def test_falls_back_to_jsonl_when_db_missing(self, client, journal_path, db_path):
        assert not db_path.exists()
        entries = [
            {"timestamp": "2026-04-10T10:00:00", "pnl": 200, "fill_price": 3.2, "closed_at": "2026-04-10"},
            {"timestamp": "2026-04-11T10:00:00", "pnl": -50, "fill_price": 1.1, "closed_at": "2026-04-11"},
        ]
        journal_path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n"
        )
        r = client.get("/api/performance")
        body = r.json()
        assert body["total_pnl"] == 150.0
        assert body["best_trade"]["pnl"] == 200


# ── /api/regime-history ─────────────────────────────────────


class TestRegimeHistory:
    def test_default_when_missing(self, client):
        r = client.get("/api/regime-history")
        assert r.status_code == 200
        body = r.json()
        assert body["confirmed"] == "NEUTRAL"
        assert body["readings"] == []

    def test_returns_history(self, client, snap):
        data = {"readings": ["BULL", "BULL", "BULL"], "confirmed": "BULL"}
        (snap / "regime_history.json").write_text(json.dumps(data))
        r = client.get("/api/regime-history")
        assert r.json()["confirmed"] == "BULL"
