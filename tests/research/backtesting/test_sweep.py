"""Tests for BacktestSweep."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from database.db import Database
from database.repositories import BacktestStatsRepository
from research.backtesting.sweep import BacktestSweep, _compute_max_drawdown, _compute_sharpe


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db_and_repo(tmp_path):
    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    repo = BacktestStatsRepository(db.get_connection())
    yield db, repo
    db.close()


def _make_sweep(db_and_repo, tmp_path, universe=None):
    db, repo = db_and_repo
    if universe is None:
        universe = MagicMock()
        universe.all_symbols.return_value = ["AAPL", "MSFT"]
    engine = MagicMock()
    sweep = BacktestSweep(
        engine=engine,
        repo=repo,
        universe=universe,
        data_dir=tmp_path,
    )
    return sweep, engine, repo


# ── SimulatedTrade stub ────────────────────────────────────────────────────────

@dataclass
class FakeTrade:
    symbol: str
    strategy: str
    entry_date: str
    exit_date: Optional[str]
    expiration_date: str = "2025-03-21"
    short_strike: float = 190.0
    long_strike: Optional[float] = 185.0
    short_strike_2: Optional[float] = None
    long_strike_2: Optional[float] = None
    entry_credit: float = 1.50
    exit_debit: Optional[float] = 0.75
    contracts: int = 1
    pnl: Optional[float] = 75.0
    exit_reason: Optional[str] = "profit_target"
    entry_delta: float = 0.25
    entry_ivr: float = 45.0
    entry_regime: str = "BULL"
    entry_iv_env: str = "MODERATE"
    holding_days: Optional[int] = 22
    entry_implied_earnings_move: Optional[float] = None
    entry_historical_earnings_move: Optional[float] = None
    earnings_iv_premium_at_entry: Optional[float] = None
    actual_earnings_move: Optional[float] = None
    earnings_occurred_in_window: bool = False


def _fake_result(trades):
    result = MagicMock()
    result.trades = trades
    return result


# ── sweep_symbols ─────────────────────────────────────────────────────────────

def test_sweep_symbols_calls_insert_trades_batch(db_and_repo, tmp_path, monkeypatch):
    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)

    fake_trade = FakeTrade(
        symbol="AAPL",
        strategy="bull_put_spread",
        entry_date="2025-01-10",
        exit_date="2025-02-01",
    )
    engine.run.return_value = _fake_result([fake_trade])

    result = sweep.sweep_symbols(
        symbols=["AAPL"],
        strategies=["bull_put_spread"],
        start_date="2022-01-01",
        end_date="2025-01-01",
        sweep_run_id="test-run-001",
    )

    assert result["total_attempts"] == 1
    assert result["successes"] == 1
    assert result["failures"] == 0
    assert result["trades_inserted"] == 1

    trades = repo.get_trades(symbol="AAPL", strategy_type="bull_put_spread")
    assert len(trades) == 1
    assert trades[0]["pnl"] == pytest.approx(75.0)
    assert trades[0]["sweep_run_id"] == "test-run-001"


def test_sweep_symbols_engine_failure_returns_failure_count(db_and_repo, tmp_path):
    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)
    engine.run.side_effect = RuntimeError("ORATS timeout")

    result = sweep.sweep_symbols(
        symbols=["AAPL", "MSFT"],
        strategies=["bull_put_spread"],
        start_date="2022-01-01",
        end_date="2025-01-01",
        sweep_run_id="test-run-fail",
    )

    assert result["failures"] == 2
    assert result["successes"] == 0
    assert result["trades_inserted"] == 0
    assert len(repo.get_trades()) == 0


def test_sweep_symbols_trade_dict_shape(db_and_repo, tmp_path):
    """Trade rows must have required fields in the correct columns."""
    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)
    fake_trade = FakeTrade(
        symbol="SPY",
        strategy="wheel_csp",
        entry_date="2025-03-01",
        exit_date="2025-03-20",
        pnl=120.0,
        entry_regime="NEUTRAL",
    )
    engine.run.return_value = _fake_result([fake_trade])

    sweep.sweep_symbols(
        symbols=["SPY"],
        strategies=["wheel_csp"],
        start_date="2022-01-01",
        end_date="2025-01-01",
        sweep_run_id="shape-test",
    )

    trades = repo.get_trades(symbol="SPY")
    assert len(trades) == 1
    t = trades[0]
    assert t["strategy_type"] == "wheel_csp"
    assert t["entry_regime"] == "NEUTRAL"
    assert t["pnl"] == pytest.approx(120.0)
    assert t["sweep_run_id"] == "shape-test"


# ── recompute_symbol_stats ─────────────────────────────────────────────────────

def _seed_trades(repo, symbol, strategy, pnl_series, regime="BULL"):
    trades = [
        {
            "sweep_run_id": "seed-run",
            "symbol": symbol,
            "strategy_type": strategy,
            "entry_date": f"2023-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "pnl": pnl,
            "entry_regime": regime,
            "contracts": 1,
        }
        for i, pnl in enumerate(pnl_series)
    ]
    repo.insert_trades_batch(trades, "seed-run")


def test_recompute_symbol_stats_correct_metrics(db_and_repo, tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 5)

    db, repo = db_and_repo
    sweep, engine, _ = _make_sweep(db_and_repo, tmp_path)

    # 40 trades: 28 wins (pnl > 0), 12 losses
    pnls = [100.0] * 28 + [-50.0] * 12
    _seed_trades(repo, "AAPL", "bull_put_spread", pnls)

    result = sweep.recompute_symbol_stats(lookback_years=5)

    stat = repo.get_symbol_stat("AAPL", "bull_put_spread")
    assert stat is not None
    assert stat["trade_count"] == 40
    assert stat["win_count"] == 28
    assert stat["win_rate"] == pytest.approx(0.70)
    assert stat["confidence"] == "high"
    assert "bull_put_spread" in result


def test_recompute_symbol_stats_5_trades_none_confidence(db_and_repo, tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 5)

    db, repo = db_and_repo
    sweep, _, _ = _make_sweep(db_and_repo, tmp_path)

    _seed_trades(repo, "AAPL", "bull_put_spread", [50.0] * 5)
    sweep.recompute_symbol_stats(lookback_years=5)

    stat = repo.get_symbol_stat("AAPL", "bull_put_spread")
    assert stat["confidence"] == "none"


def test_recompute_symbol_stats_15_trades_low_confidence(db_and_repo, tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 5)

    db, repo = db_and_repo
    sweep, _, _ = _make_sweep(db_and_repo, tmp_path)

    _seed_trades(repo, "AAPL", "bull_put_spread", [50.0] * 15)
    sweep.recompute_symbol_stats(lookback_years=5)

    stat = repo.get_symbol_stat("AAPL", "bull_put_spread")
    assert stat["confidence"] == "low"


def test_recompute_symbol_stats_50_trades_high_confidence(db_and_repo, tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 5)

    db, repo = db_and_repo
    sweep, _, _ = _make_sweep(db_and_repo, tmp_path)

    _seed_trades(repo, "AAPL", "bull_put_spread", [50.0] * 50)
    sweep.recompute_symbol_stats(lookback_years=5)

    stat = repo.get_symbol_stat("AAPL", "bull_put_spread")
    assert stat["confidence"] == "high"


# ── recompute_regime_stats ─────────────────────────────────────────────────────

def test_recompute_regime_stats_buckets_by_regime_and_strategy(
    db_and_repo, tmp_path, monkeypatch
):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_REGIME_MIN_TRADES", 50)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 5)

    db, repo = db_and_repo
    sweep, _, _ = _make_sweep(db_and_repo, tmp_path)

    _seed_trades(repo, "AAPL", "bull_put_spread", [100.0] * 60, regime="BULL")
    _seed_trades(repo, "MSFT", "bull_put_spread", [50.0] * 60, regime="NEUTRAL")
    _seed_trades(repo, "SPY", "iron_condor", [80.0] * 40, regime="NEUTRAL")

    counts = sweep.recompute_regime_stats(lookback_years=5)

    bull_bps = repo.get_regime_stat("BULL", "bull_put_spread")
    neutral_bps = repo.get_regime_stat("NEUTRAL", "bull_put_spread")
    neutral_ic = repo.get_regime_stat("NEUTRAL", "iron_condor")

    assert bull_bps is not None
    assert bull_bps["trade_count"] == 60
    assert bull_bps["confidence"] == "high"

    assert neutral_bps is not None
    assert neutral_bps["trade_count"] == 60

    assert neutral_ic is not None
    assert neutral_ic["trade_count"] == 40
    assert neutral_ic["confidence"] == "low"  # below threshold of 50


# ── run_sweep state file / resumability ───────────────────────────────────────

def test_run_sweep_state_file_resumability(db_and_repo, tmp_path, monkeypatch):
    """Second call with same mode picks up where first left off, same run_id."""
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_SWEEP_MODE", "watchlist")
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL", "MSFT"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_REGIME_MIN_TRADES", 100)

    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)
    engine.run.return_value = _fake_result([])

    result1 = sweep.run_sweep(mode="watchlist")
    run_id_1 = result1["run_id"]
    assert result1["chunk_size"] == 1  # capped at 1

    result2 = sweep.run_sweep(mode="watchlist")
    run_id_2 = result2["run_id"]

    # Same run ID — resumed
    assert run_id_1 == run_id_2
    # Second call processes remaining symbol(s)
    assert result2["chunk_size"] >= 0


def test_run_sweep_mode_change_starts_new_run(db_and_repo, tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_REGIME_MIN_TRADES", 100)

    universe = MagicMock()
    universe.all_symbols.return_value = ["AAPL", "MSFT", "SPY"]
    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path, universe=universe)
    engine.run.return_value = _fake_result([])

    result1 = sweep.run_sweep(mode="watchlist")
    run_id_1 = result1["run_id"]

    result2 = sweep.run_sweep(mode="universe")
    run_id_2 = result2["run_id"]

    assert run_id_1 != run_id_2


def test_run_sweep_respects_max_symbols_per_run(db_and_repo, tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 2)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL", "MSFT", "SPY", "GOOGL"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_REGIME_MIN_TRADES", 100)

    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)
    engine.run.return_value = _fake_result([])

    result = sweep.run_sweep(mode="watchlist")
    assert result["chunk_size"] <= 2


# ── run_sweep stats gate ───────────────────────────────────────────────────────

def test_run_sweep_recomputes_stats_on_partial_prime(db_and_repo, tmp_path, monkeypatch):
    """recompute_*_stats is called when pairs_primed > 0, even if not all pairs are primed."""
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL", "MSFT"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_REGIME_MIN_TRADES", 100)

    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)
    engine.run.return_value = _fake_result([])

    with patch.object(sweep, "recompute_symbol_stats", return_value={}) as mock_sym, \
         patch.object(sweep, "recompute_regime_stats", return_value={}) as mock_reg:
        sweep.run_sweep(mode="watchlist")

    mock_sym.assert_called_once()
    mock_reg.assert_called_once()


def test_run_sweep_skips_recompute_when_no_pairs_primed(db_and_repo, tmp_path, monkeypatch):
    """recompute_*_stats is NOT called when all eligible pairs fail (pairs_primed == 0)."""
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_REGIME_MIN_TRADES", 100)

    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)
    engine.run.side_effect = RuntimeError("simulated engine failure")

    with patch.object(sweep, "recompute_symbol_stats", return_value={}) as mock_sym, \
         patch.object(sweep, "recompute_regime_stats", return_value={}) as mock_reg:
        sweep.run_sweep(mode="watchlist")

    mock_sym.assert_not_called()
    mock_reg.assert_not_called()


def test_run_sweep_skips_recompute_when_full_list_empty(db_and_repo, tmp_path, monkeypatch):
    """recompute_*_stats is NOT called when no symbols are in the watchlist."""
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "WATCHLIST", [])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", 30)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_REGIME_MIN_TRADES", 100)

    sweep, engine, repo = _make_sweep(db_and_repo, tmp_path)
    engine.run.return_value = _fake_result([])

    with patch.object(sweep, "recompute_symbol_stats", return_value={}) as mock_sym, \
         patch.object(sweep, "recompute_regime_stats", return_value={}) as mock_reg:
        sweep.run_sweep(mode="watchlist")

    mock_sym.assert_not_called()
    mock_reg.assert_not_called()


# ── Helper unit tests ─────────────────────────────────────────────────────────

def test_compute_max_drawdown_basic():
    pnl = [100, -50, 80, -200, 50]
    # cumulative: 100, 50, 130, -70, -20
    # peak at 130, trough at -70 → dd = 200
    dd = _compute_max_drawdown(pnl)
    assert dd == pytest.approx(200.0)


def test_compute_max_drawdown_all_positive():
    dd = _compute_max_drawdown([50.0, 100.0, 75.0])
    assert dd == pytest.approx(0.0)


def test_compute_sharpe_returns_none_for_zero_variance():
    result = _compute_sharpe([100.0, 100.0, 100.0])
    assert result is None


def test_compute_sharpe_computes_ratio():
    pnl = [100.0, -50.0, 200.0, -100.0]
    s = _compute_sharpe(pnl)
    assert s is not None
    import math
    mean = sum(pnl) / len(pnl)
    var = sum((p - mean) ** 2 for p in pnl) / len(pnl)
    expected = mean / math.sqrt(var)
    assert s == pytest.approx(expected, rel=1e-4)
