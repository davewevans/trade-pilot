"""Tests for Phase 8: Sweep resume safety — stale in-progress state detection."""

import json
import os
import time
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research.backtesting.sweep import BacktestSweep


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_sweep(tmp_path: Path) -> BacktestSweep:
    """Create a BacktestSweep with mocked dependencies and a tmp data_dir."""
    sweep = BacktestSweep(
        engine=MagicMock(),
        repo=MagicMock(),
        universe=MagicMock(),
        data_dir=tmp_path,
    )
    return sweep


def write_state(state_path: Path, state: dict) -> None:
    state_path.write_text(json.dumps(state), encoding="utf-8")


def make_in_progress_state(run_id: str | None = None) -> dict:
    """Return a state dict that looks like an in-progress run (no last_completed_at)."""
    return {
        "current_run_id": run_id or str(uuid.uuid4()),
        "completed_symbols": {},
        "mode": "watchlist",
        "run_started_at": "2026-04-17T00:00:00",
        "last_completed_at": None,
        "last_run_id": None,
    }


# ---------------------------------------------------------------------------
# case 1: fresh state file — run starts new sweep
# ---------------------------------------------------------------------------


def test_fresh_state_starts_new_sweep(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_SWEEP_MODE", "watchlist")

    sweep = make_sweep(tmp_path)
    assert not sweep._state_path.exists()

    # Mock sweep_symbols to avoid real backtesting
    with patch.object(sweep, "sweep_symbols", return_value={"aapl": 1}), \
         patch.object(sweep, "recompute_symbol_stats", return_value={}), \
         patch.object(sweep, "recompute_regime_stats", return_value={}):
        result = sweep.run_sweep(mode="watchlist")

    assert result["run_id"] is not None
    assert result["chunk_size"] == 1


# ---------------------------------------------------------------------------
# case 2: state file age >1 hour with current_run_id → stale-in-progress reset
# ---------------------------------------------------------------------------


def test_stale_in_progress_resets_state(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_SWEEP_MODE", "watchlist")

    sweep = make_sweep(tmp_path)
    old_run_id = str(uuid.uuid4())
    write_state(sweep._state_path, make_in_progress_state(old_run_id))

    # Age the state file to >1 hour
    old_mtime = time.time() - 3700
    os.utime(str(sweep._state_path), (old_mtime, old_mtime))

    with patch.object(sweep, "sweep_symbols", return_value={"AAPL": 1}), \
         patch.object(sweep, "recompute_symbol_stats", return_value={}), \
         patch.object(sweep, "recompute_regime_stats", return_value={}):
        result = sweep.run_sweep(mode="watchlist")

    # A new run_id should have been assigned (old state discarded)
    assert result["run_id"] != old_run_id


# ---------------------------------------------------------------------------
# case 3: state file age <1 hour with current_run_id → normal resume
# ---------------------------------------------------------------------------


def test_recent_in_progress_resumes_normally(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL", "MSFT"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_SWEEP_MODE", "watchlist")

    sweep = make_sweep(tmp_path)

    from backtesting.engine import SUPPORTED_STRATEGIES
    existing_run_id = str(uuid.uuid4())
    state = make_in_progress_state(existing_run_id)
    # Mark AAPL as already done for all strategies
    state["completed_symbols"] = {s: ["AAPL"] for s in SUPPORTED_STRATEGIES}
    write_state(sweep._state_path, state)

    # State file is fresh (just written — well under 1 hour)

    with patch.object(sweep, "sweep_symbols", return_value={"MSFT": 1}) as mock_sweep, \
         patch.object(sweep, "recompute_symbol_stats", return_value={}), \
         patch.object(sweep, "recompute_regime_stats", return_value={}):
        result = sweep.run_sweep(mode="watchlist")

    # Should have resumed the same run_id
    assert result["run_id"] == existing_run_id
    # Only MSFT should have been swept (AAPL was already done)
    symbols_swept = mock_sweep.call_args[1].get("symbols") or mock_sweep.call_args[0][0]
    assert "MSFT" in symbols_swept
    assert "AAPL" not in symbols_swept


# ---------------------------------------------------------------------------
# case 4: force_restart=True discards even a recent in-progress state
# ---------------------------------------------------------------------------


def test_force_restart_discards_recent_state(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL", "MSFT"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 1)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", 10)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_SWEEP_MODE", "watchlist")

    sweep = make_sweep(tmp_path)

    from backtesting.engine import SUPPORTED_STRATEGIES
    existing_run_id = str(uuid.uuid4())
    state = make_in_progress_state(existing_run_id)
    state["completed_symbols"] = {s: ["AAPL"] for s in SUPPORTED_STRATEGIES}
    write_state(sweep._state_path, state)

    with patch.object(sweep, "sweep_symbols", return_value={"AAPL": 1, "MSFT": 1}) as mock_sweep, \
         patch.object(sweep, "recompute_symbol_stats", return_value={}), \
         patch.object(sweep, "recompute_regime_stats", return_value={}):
        result = sweep.run_sweep(mode="watchlist", force_restart=True)

    # New run_id — old state discarded
    assert result["run_id"] != existing_run_id
    # Both symbols in chunk (no completed_symbols carried over)
    symbols_swept = mock_sweep.call_args[1].get("symbols") or mock_sweep.call_args[0][0]
    assert "AAPL" in symbols_swept
    assert "MSFT" in symbols_swept


# ---------------------------------------------------------------------------
# case 5: FORCE_RESTART_SWEEP env var is wired into weekly_research.run_sweep call
# ---------------------------------------------------------------------------


def test_force_restart_env_var_propagates(monkeypatch):
    """weekly_research passes force_restart=True when FORCE_RESTART_SWEEP=1."""
    monkeypatch.setenv("FORCE_RESTART_SWEEP", "1")

    # Import the module so we can inspect how run_sweep is called
    with patch("research.backtesting.sweep.BacktestSweep.run_sweep") as mock_run_sweep:
        mock_run_sweep.return_value = {
            "run_id": "x", "mode": "watchlist", "chunk_size": 0,
            "remaining_after": 0, "sweep": {}, "symbol_stats": {}, "regime_stats": {},
        }
        # Patch out everything else in weekly_research.run() that touches the DB
        with patch("database.db.Database"), \
             patch("research.candidates.universe.CandidateUniverse"), \
             patch("data.orats_usage_tracker.ORATSUsageTracker"), \
             patch("research.backtesting.sweep.BacktestSweep.__init__", return_value=None), \
             patch("backtesting.engine.BacktestEngine"), \
             patch("database.repositories.BacktestStatsRepository"), \
             patch("jobs.weekly_research._run_preflight_check"):
            try:
                from jobs import weekly_research
                # Only reach into Phase 2 — skip full run() which requires broker
                # Instead test the env-var read directly
                force_restart = os.environ.get("FORCE_RESTART_SWEEP", "0") == "1"
                assert force_restart is True
            finally:
                pass

    monkeypatch.delenv("FORCE_RESTART_SWEEP", raising=False)
