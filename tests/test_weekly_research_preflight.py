"""Tests for the weekly_research pre-flight budget check and BacktestSweep.estimate_cost."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from data.api_ledger import ApiLedger
from jobs.weekly_research import _run_preflight_check


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_ledger(tmp_path: Path) -> ApiLedger:
    from database.db import Database
    db = Database(path=tmp_path / "test.db")
    db.init_schema()
    return ApiLedger(db.get_connection())


def make_sweep_with_estimate(cold: int, warm: int) -> MagicMock:
    """Return a mock BacktestSweep with estimate_cost() returning given values."""
    sweep = MagicMock()
    sweep.estimate_cost.return_value = {
        "cold_estimate": cold,
        "warm_estimate": warm,
        "by_symbol": {},
    }
    return sweep


# ---------------------------------------------------------------------------
# case 1: preflight passes when estimate is well under remaining budget
# ---------------------------------------------------------------------------


def test_preflight_passes_under_budget(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 14000)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 14000)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 600)
    monkeypatch.setattr(settings, "ORATS_ALLOW_BUDGET_HEAVY", 0)

    ledger = make_ledger(tmp_path)
    sweep = make_sweep_with_estimate(cold=10000, warm=5000)

    # Should not raise or exit
    _run_preflight_check(sweep, ledger)


# ---------------------------------------------------------------------------
# case 2: preflight aborts (exit code 3) when estimate > remaining
# ---------------------------------------------------------------------------


def test_preflight_aborts_when_estimate_exceeds_remaining(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 100)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_ALLOW_BUDGET_HEAVY", 0)

    ledger = make_ledger(tmp_path)
    # Use up 90 of the 100-call budget
    for _ in range(90):
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    # warm_estimate=50 > month_remaining=10
    sweep = make_sweep_with_estimate(cold=50, warm=50)

    with pytest.raises(SystemExit) as exc_info:
        _run_preflight_check(sweep, ledger)

    assert exc_info.value.code == 3


# ---------------------------------------------------------------------------
# case 2b: preflight abort writes research_last_run.json with status="aborted"
# ---------------------------------------------------------------------------


def test_preflight_abort_writes_run_status(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 100)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_ALLOW_BUDGET_HEAVY", 0)

    ledger = make_ledger(tmp_path)
    # Use up 90 of the 100-call budget
    for _ in range(90):
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    # warm_estimate=50 > month_remaining=10
    sweep = make_sweep_with_estimate(cold=50, warm=50)

    with pytest.raises(SystemExit) as exc_info:
        _run_preflight_check(sweep, ledger)

    assert exc_info.value.code == 3

    status_path = tmp_path / "research_last_run.json"
    assert status_path.exists()
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert data["status"] == "aborted"
    assert data["abort_reason"] == "orats_budget"


# ---------------------------------------------------------------------------
# case 3: preflight aborts when estimate > 80% and ORATS_ALLOW_BUDGET_HEAVY unset
# ---------------------------------------------------------------------------


def test_preflight_aborts_at_80_pct_without_override(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 1000)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_ALLOW_BUDGET_HEAVY", 0)

    ledger = make_ledger(tmp_path)
    # month_remaining = 1000, warm_estimate = 850 → 85% > 80% threshold
    sweep = make_sweep_with_estimate(cold=900, warm=850)

    with pytest.raises(SystemExit) as exc_info:
        _run_preflight_check(sweep, ledger)

    assert exc_info.value.code == 3


# ---------------------------------------------------------------------------
# case 4: preflight proceeds when estimate > 80% and ORATS_ALLOW_BUDGET_HEAVY=1
# ---------------------------------------------------------------------------


def test_preflight_proceeds_with_budget_heavy_override(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 1000)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_ALLOW_BUDGET_HEAVY", 1)

    ledger = make_ledger(tmp_path)
    # warm_estimate = 850 > 80% of 1000 — but override is set
    sweep = make_sweep_with_estimate(cold=900, warm=850)

    # Should not exit
    _run_preflight_check(sweep, ledger)


# ---------------------------------------------------------------------------
# case 5: estimate_cost() reflects cache state — seeding reduces warm estimate
# ---------------------------------------------------------------------------


def test_estimate_cost_reflects_cache_state(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_LOOKBACK_YEARS", 3)
    monkeypatch.setattr(settings, "RESEARCH_BACKTEST_SWEEP_MODE", "watchlist")
    monkeypatch.setattr(settings, "WATCHLIST", ["AAPL"])
    monkeypatch.setattr(settings, "IRON_CONDOR_WATCHLIST", [])
    monkeypatch.setattr(settings, "SPREAD_WATCHLIST", [])

    from data.orats_cache import ORATSCache
    from database.db import Database
    from research.backtesting.sweep import BacktestSweep, _count_hist_summaries_cached

    # Create the schema (including orats_cache table) in the test DB
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.init_schema()

    cache = ORATSCache(conn=db.get_connection())

    # Create a minimal BacktestSweep
    sweep = BacktestSweep(
        engine=MagicMock(),
        repo=MagicMock(),
        universe=MagicMock(),
        data_dir=tmp_path,
    )

    # Cold estimate before seeding
    with patch("data.orats_cache.ORATSCache", return_value=cache):
        estimate_cold = sweep.estimate_cost()

    warm_before = estimate_cold["warm_estimate"]

    # Seed 100 hist/summaries entries for AAPL
    now = time.time()
    _hist_ttl = 7 * 24 * 3600
    for i in range(100):
        cache_key = f"AAPL|2024-01-{i + 1:02d}" if i < 31 else f"AAPL|2024-02-{i - 30:02d}"
        cache.set("hist/summaries", f"AAPL|day{i}", [{"tradeDate": f"day{i}"}], _hist_ttl)

    # Warm estimate after seeding should be lower
    with patch("data.orats_cache.ORATSCache", return_value=cache):
        estimate_warm = sweep.estimate_cost()

    warm_after = estimate_warm["warm_estimate"]
    assert warm_after < warm_before, (
        f"Expected warm estimate to decrease after seeding cache; "
        f"before={warm_before} after={warm_after}"
    )
    assert estimate_warm["by_symbol"]["AAPL"]["cached_summary_days"] == 100
