"""I7: API tests for /api/research/winrate/* and /api/research/liquidity/scores."""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


# ── Fixtures (mirrors test_api.py pattern) ────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    snap = tmp_path / "snapshots"
    snap.mkdir()
    data = tmp_path / "data"
    data.mkdir()

    import api.server as srv

    monkeypatch.setattr(srv, "SNAPSHOTS", snap)
    monkeypatch.setattr(srv, "DATA_DIR", data)
    monkeypatch.setattr(srv, "JOURNAL_PATH", data / "journal.jsonl")
    monkeypatch.setattr(srv, "LOCK_PATH", data / "HALTED.lock")
    monkeypatch.setattr(srv, "DB_PATH", tmp_path / "test.db")

    _patch_paths.tmp = tmp_path
    _patch_paths.data = data


@pytest.fixture
def client():
    from api.server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def authed_client():
    from api.server import app, _issue_session, _SESSION_COOKIE
    tc = TestClient(app, raise_server_exceptions=False)
    token, _ = _issue_session()
    tc.cookies.set(_SESSION_COOKIE, token)
    return tc


@pytest.fixture
def db():
    from database.db import Database
    import api.server as srv
    db_instance = Database(path=str(srv.DB_PATH))
    db_instance.init_schema()
    yield db_instance
    db_instance.close()


# ── Auth guard tests ──────────────────────────────────────────────────────────

class TestAuthRequired:
    ENDPOINTS = [
        "/api/research/liquidity/scores",
        "/api/research/winrate/symbol-stats",
        "/api/research/winrate/regime-stats",
        "/api/research/winrate/symbol/AAPL",
        "/api/research/winrate/coverage",
    ]

    @pytest.mark.parametrize("path", ENDPOINTS)
    def test_unauthenticated_returns_401(self, client, path):
        r = client.get(path)
        assert r.status_code == 401, f"{path} should be auth-gated"


# ── /api/research/winrate/symbol-stats ───────────────────────────────────────

class TestSymbolStats:

    def test_empty_db_returns_grouped_structure(self, authed_client, db):
        r = authed_client.get("/api/research/winrate/symbol-stats")
        assert r.status_code == 200
        body = r.json()
        assert "generated_at" in body
        assert "strategies" in body
        assert isinstance(body["strategies"], dict)

    def test_seeded_data_appears_in_correct_strategy_group(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())
        repo.upsert_symbol_stat({
            "symbol": "AAPL",
            "strategy_type": "wheel_csp",
            "trade_count": 45,
            "win_count": 30,
            "win_rate": 0.67,
            "avg_pnl_per_trade": 45.20,
            "total_pnl": 2034.00,
            "max_drawdown": 850.00,
            "sharpe_ratio": 0.85,
            "confidence": "high",
            "date_range_start": "2022-04-01",
            "date_range_end": "2026-04-01",
        })

        r = authed_client.get("/api/research/winrate/symbol-stats")
        assert r.status_code == 200
        body = r.json()
        assert "wheel_csp" in body["strategies"]
        row = body["strategies"]["wheel_csp"][0]
        assert row["symbol"] == "AAPL"
        assert row["win_rate"] == pytest.approx(0.67)
        assert row["confidence"] == "high"

    def test_multiple_strategies_grouped_separately(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())
        for strat in ("wheel_csp", "bull_put_spread"):
            repo.upsert_symbol_stat({
                "symbol": "SPY",
                "strategy_type": strat,
                "trade_count": 30,
                "win_count": 20,
                "win_rate": 0.67,
                "avg_pnl_per_trade": 30.0,
                "total_pnl": 900.0,
                "max_drawdown": 200.0,
                "sharpe_ratio": None,
                "confidence": "high",
                "date_range_start": "2023-01-01",
                "date_range_end": "2026-01-01",
            })

        r = authed_client.get("/api/research/winrate/symbol-stats")
        body = r.json()
        assert "wheel_csp" in body["strategies"]
        assert "bull_put_spread" in body["strategies"]
        # Each group has exactly 1 row
        assert len(body["strategies"]["wheel_csp"]) == 1
        assert len(body["strategies"]["bull_put_spread"]) == 1


# ── /api/research/winrate/regime-stats ───────────────────────────────────────

class TestRegimeStats:

    def test_empty_db_returns_grouped_structure(self, authed_client, db):
        r = authed_client.get("/api/research/winrate/regime-stats")
        assert r.status_code == 200
        body = r.json()
        assert "generated_at" in body
        assert "regimes" in body
        assert isinstance(body["regimes"], dict)

    def test_seeded_data_grouped_by_regime(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())
        repo.upsert_regime_stat({
            "entry_regime": "BULL",
            "strategy_type": "bull_put_spread",
            "trade_count": 340,
            "win_count": 241,
            "win_rate": 0.71,
            "avg_pnl_per_trade": 52.0,
            "total_pnl": 17680.0,
            "max_drawdown": 1200.0,
            "confidence": "high",
        })

        r = authed_client.get("/api/research/winrate/regime-stats")
        body = r.json()
        assert "BULL" in body["regimes"]
        row = body["regimes"]["BULL"][0]
        assert row["strategy_type"] == "bull_put_spread"
        assert row["win_rate"] == pytest.approx(0.71)

    def test_multiple_regimes_grouped_separately(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())
        for regime in ("BULL", "NEUTRAL", "BEAR"):
            repo.upsert_regime_stat({
                "entry_regime": regime,
                "strategy_type": "wheel_csp",
                "trade_count": 100,
                "win_count": 65,
                "win_rate": 0.65,
                "avg_pnl_per_trade": 40.0,
                "total_pnl": 4000.0,
                "max_drawdown": 500.0,
                "confidence": "high",
            })

        r = authed_client.get("/api/research/winrate/regime-stats")
        body = r.json()
        for regime in ("BULL", "NEUTRAL", "BEAR"):
            assert regime in body["regimes"]


# ── /api/research/winrate/symbol/{ticker} ────────────────────────────────────

class TestSymbolDeepDive:

    def test_unknown_symbol_returns_404(self, authed_client, db):
        r = authed_client.get("/api/research/winrate/symbol/UNKNOWN")
        assert r.status_code == 404

    def test_known_symbol_returns_stats_and_trades(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())
        repo.upsert_symbol_stat({
            "symbol": "MSFT",
            "strategy_type": "wheel_csp",
            "trade_count": 32,
            "win_count": 22,
            "win_rate": 0.69,
            "avg_pnl_per_trade": 60.0,
            "total_pnl": 1920.0,
            "max_drawdown": 400.0,
            "sharpe_ratio": 1.1,
            "confidence": "high",
            "date_range_start": "2023-01-01",
            "date_range_end": "2026-01-01",
        })
        repo.insert_trade({
            "sweep_run_id": "run-001",
            "symbol": "MSFT",
            "strategy_type": "wheel_csp",
            "entry_date": "2025-01-10",
            "pnl": 80.0,
        })

        r = authed_client.get("/api/research/winrate/symbol/MSFT")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "MSFT"
        assert "wheel_csp" in body["stats"]
        assert "recent_trades" in body
        assert "wheel_csp" in body["recent_trades"]
        assert len(body["recent_trades"]["wheel_csp"]) == 1

    def test_ticker_uppercased(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())
        repo.upsert_symbol_stat({
            "symbol": "TSLA",
            "strategy_type": "bull_put_spread",
            "trade_count": 15,
            "win_count": 10,
            "win_rate": 0.67,
            "avg_pnl_per_trade": 35.0,
            "total_pnl": 525.0,
            "max_drawdown": 200.0,
            "sharpe_ratio": None,
            "confidence": "low",
            "date_range_start": "2024-01-01",
            "date_range_end": "2026-01-01",
        })

        r = authed_client.get("/api/research/winrate/symbol/tsla")
        assert r.status_code == 200
        assert r.json()["symbol"] == "TSLA"

    def test_recent_trades_capped_at_20(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())
        repo.upsert_symbol_stat({
            "symbol": "SPY",
            "strategy_type": "iron_condor",
            "trade_count": 30,
            "win_count": 20,
            "win_rate": 0.67,
            "avg_pnl_per_trade": 50.0,
            "total_pnl": 1500.0,
            "max_drawdown": 300.0,
            "sharpe_ratio": None,
            "confidence": "high",
            "date_range_start": "2023-01-01",
            "date_range_end": "2026-01-01",
        })
        # Insert 25 trades
        repo.insert_trades_batch(
            [
                {
                    "symbol": "SPY",
                    "strategy_type": "iron_condor",
                    "entry_date": f"2025-{(i % 12) + 1:02d}-01",
                    "pnl": float(i),
                }
                for i in range(25)
            ],
            sweep_run_id="run-999",
        )

        r = authed_client.get("/api/research/winrate/symbol/SPY")
        body = r.json()
        assert len(body["recent_trades"]["iron_condor"]) == 20


# ── /api/research/winrate/coverage ───────────────────────────────────────────

class TestCoverage:

    def test_expected_keys_present(self, authed_client, db):
        r = authed_client.get("/api/research/winrate/coverage")
        assert r.status_code == 200
        body = r.json()
        expected_keys = {
            "total_backtest_trades",
            "symbols_with_stats",
            "high_confidence_pairs",
            "low_confidence_pairs",
            "no_data_pairs",
            "oldest_trade",
            "newest_trade",
            "last_sweep_run",
        }
        assert expected_keys.issubset(body.keys())

    def test_counts_reflect_seeded_data(self, authed_client, db):
        from database.repositories import BacktestStatsRepository
        repo = BacktestStatsRepository(db.get_connection())

        repo.upsert_symbol_stat({
            "symbol": "AAPL",
            "strategy_type": "wheel_csp",
            "trade_count": 40,
            "win_count": 28,
            "win_rate": 0.70,
            "avg_pnl_per_trade": 55.0,
            "total_pnl": 2200.0,
            "max_drawdown": 500.0,
            "sharpe_ratio": 1.0,
            "confidence": "high",
            "date_range_start": "2022-01-01",
            "date_range_end": "2026-01-01",
        })
        repo.upsert_symbol_stat({
            "symbol": "AAPL",
            "strategy_type": "bull_put_spread",
            "trade_count": 12,
            "win_count": 8,
            "win_rate": 0.67,
            "avg_pnl_per_trade": 30.0,
            "total_pnl": 360.0,
            "max_drawdown": 100.0,
            "sharpe_ratio": None,
            "confidence": "low",
            "date_range_start": "2024-01-01",
            "date_range_end": "2026-01-01",
        })
        repo.insert_trade({
            "sweep_run_id": "run-001",
            "symbol": "AAPL",
            "strategy_type": "wheel_csp",
            "entry_date": "2025-03-01",
            "pnl": 100.0,
        })

        r = authed_client.get("/api/research/winrate/coverage")
        body = r.json()
        assert body["total_backtest_trades"] == 1
        assert body["symbols_with_stats"] == 1  # only AAPL
        assert body["high_confidence_pairs"] == 1
        assert body["low_confidence_pairs"] == 1

    def test_no_db_returns_zeros(self, authed_client):
        # DB_PATH points to non-existent file by default (tmp_path/test.db not initialized)
        r = authed_client.get("/api/research/winrate/coverage")
        # Should not crash — returns 200 with zero counts or handles gracefully
        assert r.status_code in (200, 503)


# ── /api/research/liquidity/scores ───────────────────────────────────────────

class TestLiquidityScores:

    def test_empty_db_returns_grouped_structure(self, authed_client, db):
        r = authed_client.get("/api/research/liquidity/scores")
        assert r.status_code == 200
        body = r.json()
        assert "generated_at" in body
        assert "strategies" in body
        assert isinstance(body["strategies"], dict)

    def test_seeded_score_appears_in_correct_strategy(self, authed_client, db):
        from database.repositories import LiquidityRepository
        repo = LiquidityRepository(db.get_connection())
        repo.upsert_score({
            "symbol": "QQQ",
            "strategy_type": "iron_condor",
            "composite_score": 0.88,
            "tier": "A",
            "confidence": "high",
            "lookback_days": 60,
            "snapshot_count": 10,
            "below_floor": False,
            "sub_metrics": {},
        })

        r = authed_client.get("/api/research/liquidity/scores")
        body = r.json()
        assert "iron_condor" in body["strategies"]
        row = body["strategies"]["iron_condor"][0]
        assert row["symbol"] == "QQQ"
        assert row["tier"] == "A"
