"""Tests for BacktestStatsRepository."""

import pytest

from database.db import Database
from database.repositories import BacktestStatsRepository


@pytest.fixture
def repo(tmp_path):
    db = Database(path=str(tmp_path / "test.db"))
    db.init_schema()
    r = BacktestStatsRepository(db.get_connection())
    yield r
    db.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _trade(overrides=None):
    base = {
        "sweep_run_id": "run-001",
        "symbol": "AAPL",
        "strategy_type": "bull_put_spread",
        "entry_date": "2025-01-10",
        "exit_date": "2025-02-01",
        "entry_credit": 1.50,
        "exit_debit": 0.75,
        "pnl": 75.0,
        "exit_reason": "profit_target",
        "entry_delta": 0.25,
        "entry_ivr": 45.0,
        "entry_regime": "BULL",
        "entry_iv_env": "MODERATE",
        "holding_days": 22,
        "contracts": 1,
    }
    if overrides:
        base.update(overrides)
    return base


def _sym_stat(overrides=None):
    base = {
        "symbol": "AAPL",
        "strategy_type": "bull_put_spread",
        "trade_count": 40,
        "win_count": 28,
        "win_rate": 0.70,
        "avg_pnl_per_trade": 65.0,
        "total_pnl": 2600.0,
        "max_drawdown": 300.0,
        "sharpe_ratio": 1.2,
        "confidence": "high",
        "date_range_start": "2022-01-01",
        "date_range_end": "2025-01-01",
    }
    if overrides:
        base.update(overrides)
    return base


def _regime_stat(overrides=None):
    base = {
        "entry_regime": "BULL",
        "strategy_type": "bull_put_spread",
        "trade_count": 120,
        "win_count": 84,
        "win_rate": 0.70,
        "avg_pnl_per_trade": 55.0,
        "total_pnl": 6600.0,
        "max_drawdown": 500.0,
        "confidence": "high",
    }
    if overrides:
        base.update(overrides)
    return base


# ── insert_trade round-trip ────────────────────────────────────────────────────

def test_insert_trade_round_trip(repo):
    trade_id = repo.insert_trade(_trade())
    assert trade_id > 0
    trades = repo.get_trades(symbol="AAPL", strategy_type="bull_put_spread")
    assert len(trades) == 1
    assert trades[0]["pnl"] == pytest.approx(75.0)
    assert trades[0]["entry_regime"] == "BULL"


# ── insert_trades_batch ────────────────────────────────────────────────────────

def test_insert_trades_batch_inserts_multiple_returns_count(repo):
    batch = [
        _trade({"entry_date": "2025-01-10", "pnl": 50.0}),
        _trade({"entry_date": "2025-01-20", "pnl": -30.0}),
        _trade({"entry_date": "2025-02-05", "pnl": 80.0}),
    ]
    count = repo.insert_trades_batch(batch, "run-batch-001")
    assert count == 3
    trades = repo.get_trades(symbol="AAPL")
    assert len(trades) == 3


def test_insert_trades_batch_empty_returns_zero(repo):
    assert repo.insert_trades_batch([], "run-empty") == 0


# ── get_trades filters ─────────────────────────────────────────────────────────

def test_get_trades_symbol_filter(repo):
    repo.insert_trade(_trade({"symbol": "AAPL", "pnl": 50.0}))
    repo.insert_trade(_trade({"symbol": "MSFT", "pnl": 60.0}))
    trades = repo.get_trades(symbol="AAPL")
    assert all(t["symbol"] == "AAPL" for t in trades)
    assert len(trades) == 1


def test_get_trades_strategy_and_regime_filter(repo):
    repo.insert_trade(_trade({"strategy_type": "bull_put_spread", "entry_regime": "BULL"}))
    repo.insert_trade(_trade({"strategy_type": "iron_condor", "entry_regime": "NEUTRAL"}))
    repo.insert_trade(_trade({"strategy_type": "bull_put_spread", "entry_regime": "NEUTRAL"}))
    results = repo.get_trades(strategy_type="bull_put_spread", entry_regime="BULL")
    assert len(results) == 1
    assert results[0]["strategy_type"] == "bull_put_spread"
    assert results[0]["entry_regime"] == "BULL"


def test_get_trades_no_filter_returns_all(repo):
    repo.insert_trade(_trade({"symbol": "AAPL"}))
    repo.insert_trade(_trade({"symbol": "MSFT"}))
    assert len(repo.get_trades()) == 2


# ── upsert_symbol_stat ─────────────────────────────────────────────────────────

def test_upsert_symbol_stat_inserts_then_updates(repo):
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.70, "total_pnl": 2600.0}))
    row = repo.get_symbol_stat("AAPL", "bull_put_spread")
    assert row is not None
    assert row["win_rate"] == pytest.approx(0.70)

    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.55, "total_pnl": 1000.0}))
    row = repo.get_symbol_stat("AAPL", "bull_put_spread")
    assert row["win_rate"] == pytest.approx(0.55)
    assert row["total_pnl"] == pytest.approx(1000.0)


# ── upsert_regime_stat ─────────────────────────────────────────────────────────

def test_upsert_regime_stat_inserts_then_updates(repo):
    repo.upsert_regime_stat(_regime_stat({"trade_count": 120, "win_rate": 0.70}))
    row = repo.get_regime_stat("BULL", "bull_put_spread")
    assert row is not None
    assert row["win_rate"] == pytest.approx(0.70)

    repo.upsert_regime_stat(_regime_stat({"trade_count": 200, "win_rate": 0.65}))
    row = repo.get_regime_stat("BULL", "bull_put_spread")
    assert row["trade_count"] == 200
    assert row["win_rate"] == pytest.approx(0.65)


# ── get_winrate_multiplier ─────────────────────────────────────────────────────

def test_get_winrate_multiplier_kill_switch_off(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", False)
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.75, "confidence": "high"}))
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.0)
    assert tier == "neutral"
    assert conf == "disabled"


def test_get_winrate_multiplier_no_row(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.0)
    assert tier == "neutral"
    assert conf == "none"


def test_get_winrate_multiplier_low_confidence(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.75, "confidence": "low"}))
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.0)
    assert conf == "low"


def test_get_winrate_multiplier_high_conf_strong(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.75, "confidence": "high"}))
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.3)
    assert tier == "strong"
    assert conf == "high"


def test_get_winrate_multiplier_high_conf_reject(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.25, "confidence": "high"}))
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(0.0)
    assert tier == "reject"
    assert conf == "high"


def test_get_winrate_multiplier_high_conf_good(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.65, "confidence": "high"}))
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(1.15)
    assert tier == "good"


def test_get_winrate_multiplier_high_conf_weak(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.45, "confidence": "high"}))
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(0.85)
    assert tier == "weak"


def test_get_winrate_multiplier_high_conf_poor(repo, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)
    repo.upsert_symbol_stat(_sym_stat({"win_rate": 0.35, "confidence": "high"}))
    mult, tier, conf = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
    assert mult == pytest.approx(0.7)
    assert tier == "poor"


def test_get_winrate_multiplier_bounds(repo, monkeypatch):
    """Multipliers must be bounded: never < 0.7 except 0.0; never > 1.3."""
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_WINRATE_MULTIPLIER_ENABLED", True)

    # All non-reject high-confidence multipliers
    cases = [
        (0.75, 1.3),   # strong
        (0.65, 1.15),  # good
        (0.55, 1.0),   # neutral
        (0.45, 0.85),  # weak
        (0.35, 0.7),   # poor
        (0.25, 0.0),   # reject — allowed to be 0
    ]
    for win_rate, expected in cases:
        repo.upsert_symbol_stat(_sym_stat({"win_rate": win_rate, "confidence": "high"}))
        mult, _, _ = repo.get_winrate_multiplier("AAPL", "bull_put_spread")
        assert mult == pytest.approx(expected), f"win_rate={win_rate}"
        if mult != 0.0:
            assert mult >= 0.7, f"mult {mult} below 0.7 for win_rate={win_rate}"
        assert mult <= 1.3, f"mult {mult} above 1.3 for win_rate={win_rate}"
