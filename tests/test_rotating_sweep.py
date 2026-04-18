"""Tests for budget-aware rotating sweep (Phase 12b).

Covers:
1. Priority order: open positions first, then unprimed, then stale
2. Sweep stops when per-run budget is exhausted
3. Sweep stops when monthly budget would be exceeded
4. sweep_progress rows are updated on success and on failure
5. Pairs primed within SWEEP_REPRIME_WEEKS are skipped
6. Dry-run / empty pair scenario (no budget spent)
"""

import time
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _make_sweep(tmp_path):
    """Return a BacktestSweep with a real SQLite DB in tmp_path."""
    import sqlite3
    db = tmp_path / "sweep_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sweep_progress (
            symbol            TEXT    NOT NULL,
            strategy          TEXT    NOT NULL,
            lookback_years    INTEGER NOT NULL,
            last_primed_at    REAL,
            prime_cost_calls  INTEGER,
            last_error        TEXT,
            PRIMARY KEY (symbol, strategy, lookback_years)
        )
    """)
    conn.commit()
    conn.close()

    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = db
    mock_settings.RENDER = False
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = "0"
    mock_settings.WEEKLY_SWEEP_BUDGET_CALLS = 5000
    mock_settings.SWEEP_REPRIME_WEEKS = 4
    mock_settings.RESEARCH_BACKTEST_LOOKBACK_YEARS = 3
    mock_settings.RESEARCH_BACKTEST_SWEEP_MODE = "watchlist"
    mock_settings.WATCHLIST = ["AAPL", "MSFT", "GOOG"]
    mock_settings.IRON_CONDOR_WATCHLIST = []
    mock_settings.SPREAD_WATCHLIST = []

    mock_engine = MagicMock()
    mock_engine.run.return_value = MagicMock(trades=[])
    mock_repo = MagicMock()
    mock_universe = MagicMock()

    from research.backtesting.sweep import BacktestSweep

    with patch("config.settings", mock_settings):
        sweep = BacktestSweep(engine=mock_engine, repo=mock_repo, universe=mock_universe,
                              data_dir=tmp_path)
        sweep._db_path = str(db)
        # Patch _sweep_db_conn to use our test DB
        import sqlite3 as _sqlite3

        def _test_db_conn():
            c = _sqlite3.connect(str(db))
            c.execute("PRAGMA journal_mode=WAL")
            return c

        sweep._sweep_db_conn = _test_db_conn

    return sweep, mock_settings, db


def _insert_progress(db_path, symbol, strategy, lookback_years, last_primed_at, cost=100, error=None):
    """Insert a sweep_progress row directly for test setup."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT OR REPLACE INTO sweep_progress
           (symbol, strategy, lookback_years, last_primed_at, prime_cost_calls, last_error)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (symbol, strategy, lookback_years, last_primed_at, cost, error),
    )
    conn.commit()
    conn.close()


def _read_progress(db_path, symbol, strategy, lookback_years):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT last_primed_at, last_error FROM sweep_progress WHERE symbol=? AND strategy=? AND lookback_years=?",
        (symbol, strategy, lookback_years),
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Test 1: Priority order (open positions → unprimed → stale)
# ---------------------------------------------------------------------------

def test_priority_order(tmp_path):
    """_build_priority_queue returns pairs sorted: open_pos=1, unprimed=2, stale=3."""
    from research.backtesting.sweep import BacktestSweep

    sweep, mock_settings, db = _make_sweep(tmp_path)
    lookback_years = 3
    strategies = ["bull_put_spread"]
    symbols = ["AAPL", "MSFT", "GOOG"]
    open_symbols = {"MSFT"}  # MSFT has open position

    stale_ts = time.time() - 5 * 7 * 24 * 3600  # 5 weeks ago (> SWEEP_REPRIME_WEEKS=4)
    # AAPL: never primed
    # MSFT: open position + stale
    _insert_progress(db, "MSFT", "bull_put_spread", lookback_years, stale_ts)
    # GOOG: stale (primed 5 weeks ago)
    _insert_progress(db, "GOOG", "bull_put_spread", lookback_years, stale_ts)

    progress = sweep._load_sweep_progress(symbols, strategies, lookback_years)
    with patch("config.settings", mock_settings):
        queue = sweep._build_priority_queue(
            symbols=symbols,
            strategies=strategies,
            lookback_years=lookback_years,
            open_symbols=open_symbols,
            progress=progress,
            reprime_weeks=4,
        )

    # MSFT (open position) should be first
    assert queue[0] == ("MSFT", "bull_put_spread"), f"Expected MSFT first, got {queue[0]}"
    # AAPL (unprimed) should be second
    assert queue[1] == ("AAPL", "bull_put_spread"), f"Expected AAPL second, got {queue[1]}"
    # GOOG (stale) should be third
    assert queue[2] == ("GOOG", "bull_put_spread"), f"Expected GOOG third, got {queue[2]}"


# ---------------------------------------------------------------------------
# Test 2: Stops when per-run budget is exhausted
# ---------------------------------------------------------------------------

def test_stops_at_per_run_budget(tmp_path):
    """Sweep stops processing pairs when the per-run budget is exhausted."""
    sweep, mock_settings, db = _make_sweep(tmp_path)
    mock_settings.WEEKLY_SWEEP_BUDGET_CALLS = 100  # tiny budget
    mock_settings.WATCHLIST = ["AAPL", "MSFT", "GOOG"]

    # Each pair costs ~2170 calls (cold) → nothing can be primed with budget=100
    # Mock estimate_pair_cost to return 60 (fits once, not twice)
    mock_settings.WEEKLY_SWEEP_BUDGET_CALLS = 70

    with (
        patch("config.settings", mock_settings),
        patch.object(sweep, "estimate_pair_cost", return_value=60),
        patch.object(sweep, "sweep_symbols", return_value={"successes": 1}),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("brokers.broker_factory.get_broker") as mock_broker_fn,
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 999999}
        mock_broker_fn.return_value.get_positions.return_value = []

        from backtesting.engine import SUPPORTED_STRATEGIES
        result = sweep.run_sweep(mode="watchlist")

    # With budget=70 and each pair costs 60:
    # First pair fits (60 ≤ 70), calls_used=60
    # Second pair: 60 > (70 - 60) = 10 → stop
    assert result["pairs_primed"] == 1, (
        f"Expected 1 pair primed within budget=70 (cost=60), got {result['pairs_primed']}"
    )


# ---------------------------------------------------------------------------
# Test 3: Stops when monthly budget would be exceeded
# ---------------------------------------------------------------------------

def test_stops_at_monthly_budget(tmp_path):
    """Sweep stops when monthly remaining budget is smaller than next pair's cost."""
    sweep, mock_settings, db = _make_sweep(tmp_path)
    mock_settings.WEEKLY_SWEEP_BUDGET_CALLS = 99999  # no run-level limit

    with (
        patch("config.settings", mock_settings),
        patch.object(sweep, "estimate_pair_cost", return_value=500),
        patch.object(sweep, "sweep_symbols", return_value={"successes": 1}),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("brokers.broker_factory.get_broker") as mock_broker_fn,
    ):
        # Monthly remaining: 600 — first pair (500) fits, second pair (500) doesn't
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 600}
        mock_broker_fn.return_value.get_positions.return_value = []

        result = sweep.run_sweep(mode="watchlist")

    assert result["pairs_primed"] == 1, (
        f"Expected 1 pair primed within monthly_remaining=600 (cost=500), "
        f"got {result['pairs_primed']}"
    )


# ---------------------------------------------------------------------------
# Test 4: sweep_progress updated on success and failure
# ---------------------------------------------------------------------------

def test_sweep_progress_updated_on_success(tmp_path):
    """After a successful prime, last_primed_at is set and last_error is None."""
    sweep, mock_settings, db = _make_sweep(tmp_path)
    mock_settings.WEEKLY_SWEEP_BUDGET_CALLS = 99999
    mock_settings.WATCHLIST = ["AAPL"]

    with (
        patch("config.settings", mock_settings),
        patch.object(sweep, "estimate_pair_cost", return_value=100),
        patch.object(sweep, "sweep_symbols", return_value={"successes": 1}),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("brokers.broker_factory.get_broker") as mock_broker_fn,
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 99999}
        mock_broker_fn.return_value.get_positions.return_value = []

        before = time.time()
        result = sweep.run_sweep(mode="watchlist")

    from backtesting.engine import SUPPORTED_STRATEGIES
    for strat in SUPPORTED_STRATEGIES:
        row = _read_progress(db, "AAPL", strat, 3)
        assert row is not None, f"Expected sweep_progress row for AAPL/{strat}"
        primed_at, error = row
        assert primed_at >= before, f"last_primed_at should be set after success for {strat}"
        assert error is None, f"last_error should be None on success for {strat}"


def test_sweep_progress_updated_on_failure(tmp_path):
    """After a failed prime, last_primed_at stays None and last_error is set."""
    sweep, mock_settings, db = _make_sweep(tmp_path)
    mock_settings.WEEKLY_SWEEP_BUDGET_CALLS = 99999
    mock_settings.WATCHLIST = ["AAPL"]

    with (
        patch("config.settings", mock_settings),
        patch.object(sweep, "estimate_pair_cost", return_value=100),
        patch.object(sweep, "sweep_symbols", side_effect=RuntimeError("ORATS down")),
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("brokers.broker_factory.get_broker") as mock_broker_fn,
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 99999}
        mock_broker_fn.return_value.get_positions.return_value = []

        result = sweep.run_sweep(mode="watchlist")

    assert result["pairs_failed"] > 0

    from backtesting.engine import SUPPORTED_STRATEGIES
    for strat in SUPPORTED_STRATEGIES:
        row = _read_progress(db, "AAPL", strat, 3)
        if row is not None:
            primed_at, error = row
            # On failure, last_primed_at should be None, error should be set
            assert primed_at is None, f"last_primed_at should be None on failure for {strat}"
            assert error is not None and "ORATS down" in error, (
                f"last_error should contain error message for {strat}"
            )


# ---------------------------------------------------------------------------
# Test 5: Recently primed pairs are skipped
# ---------------------------------------------------------------------------

def test_recently_primed_pairs_skipped(tmp_path):
    """Pairs primed within SWEEP_REPRIME_WEEKS are excluded from the priority queue."""
    sweep, mock_settings, db = _make_sweep(tmp_path)
    lookback_years = 3

    from backtesting.engine import SUPPORTED_STRATEGIES
    # Prime AAPL for all strategies just 1 week ago (< SWEEP_REPRIME_WEEKS=4)
    recent_ts = time.time() - 7 * 24 * 3600  # 1 week ago
    for strat in SUPPORTED_STRATEGIES:
        _insert_progress(db, "AAPL", strat, lookback_years, recent_ts)

    symbols = ["AAPL"]
    strategies = list(SUPPORTED_STRATEGIES)
    progress = sweep._load_sweep_progress(symbols, strategies, lookback_years)

    with patch("config.settings", mock_settings):
        queue = sweep._build_priority_queue(
            symbols=symbols,
            strategies=strategies,
            lookback_years=lookback_years,
            open_symbols=set(),
            progress=progress,
            reprime_weeks=4,
        )

    assert len(queue) == 0, (
        f"All recently-primed pairs should be skipped; got {len(queue)} in queue"
    )


# ---------------------------------------------------------------------------
# Test 6: No pairs in queue → no calls made, clean summary
# ---------------------------------------------------------------------------

def test_empty_priority_queue_no_calls(tmp_path):
    """When all pairs are recently primed, no sweep_symbols calls are made."""
    sweep, mock_settings, db = _make_sweep(tmp_path)
    mock_settings.WEEKLY_SWEEP_BUDGET_CALLS = 99999
    mock_settings.WATCHLIST = ["AAPL"]

    from backtesting.engine import SUPPORTED_STRATEGIES
    recent_ts = time.time() - 1 * 24 * 3600  # 1 day ago
    for strat in SUPPORTED_STRATEGIES:
        _insert_progress(db, "AAPL", strat, 3, recent_ts)

    with (
        patch("config.settings", mock_settings),
        patch.object(sweep, "sweep_symbols") as mock_ss,
        patch("data.api_ledger.get_ledger") as mock_ledger_fn,
        patch("brokers.broker_factory.get_broker") as mock_broker_fn,
    ):
        mock_ledger_fn.return_value.get_usage.return_value = {"month_remaining": 99999}
        mock_broker_fn.return_value.get_positions.return_value = []

        result = sweep.run_sweep(mode="watchlist")

    mock_ss.assert_not_called()
    assert result["pairs_primed"] == 0
    assert result["calls_used"] == 0
