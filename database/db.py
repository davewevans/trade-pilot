"""SQLite connection management and schema initialization for trade-pilot."""

import functools
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from config import settings

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])


def db_retry(max_attempts: int = 5, base_delay: float = 0.05) -> Callable[[_F], _F]:
    """Retry a function on SQLite 'database is locked' with exponential backoff.

    Suitable for wrapping repository write methods that may contend when the
    scheduler and API server write to the same database simultaneously.

    Args:
        max_attempts: Total attempts before re-raising (default 5).
        base_delay: Initial sleep in seconds; doubles each retry (default 0.05).
    """
    def decorator(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except sqlite3.OperationalError as exc:
                    if "database is locked" not in str(exc).lower() or attempt == max_attempts - 1:
                        raise
                    logger.warning(
                        "SQLite locked, retrying in %.3fs (attempt %d/%d): %s",
                        delay,
                        attempt + 1,
                        max_attempts,
                        exc,
                    )
                    time.sleep(delay)
                    delay *= 2
        return wrapper  # type: ignore[return-value]
    return decorator


# ── Schema ─────────────────────────────────────────────────────────
# Idempotent: every CREATE uses IF NOT EXISTS so init_schema() is
# safe to run on every startup.

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    # ── strategy_states ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS strategy_states (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_type   TEXT NOT NULL,
        underlying      TEXT NOT NULL,
        state           TEXT NOT NULL,
        active_cycle_id TEXT,
        updated_at      TEXT NOT NULL,
        payload_json    TEXT,
        UNIQUE(strategy_type, underlying)
    )
    """,
    # ── cycles ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS cycles (
        cycle_id        TEXT PRIMARY KEY,
        strategy_type   TEXT NOT NULL,
        underlying      TEXT NOT NULL,
        opened_at       TEXT NOT NULL,
        closed_at       TEXT,
        status          TEXT NOT NULL DEFAULT 'ACTIVE',
        outcome         TEXT,
        total_premium   REAL,
        notes           TEXT
    )
    """,
    # ── decisions ──────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp       TEXT NOT NULL,
        strategy_type   TEXT NOT NULL,
        underlying      TEXT NOT NULL,
        cycle_id        TEXT,
        wheel_state     TEXT,
        action          TEXT NOT NULL,
        reasoning       TEXT,
        confidence      REAL,
        alpaca_order_id TEXT,
        prompt_version  TEXT,
        context_json    TEXT
    )
    """,
    # ── trades ─────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS trades (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        cycle_id         TEXT NOT NULL,
        decision_id      INTEGER,
        alpaca_order_id  TEXT NOT NULL UNIQUE,
        underlying       TEXT NOT NULL,
        strategy_type    TEXT NOT NULL,
        trade_type       TEXT NOT NULL,
        symbol           TEXT NOT NULL,
        strike           REAL,
        expiration       TEXT,
        dte_at_entry     INTEGER,
        contracts        INTEGER NOT NULL DEFAULT 1,
        limit_price      REAL NOT NULL,
        fill_price       REAL,
        fill_status      TEXT NOT NULL DEFAULT 'pending',
        submitted_at     TEXT NOT NULL,
        filled_at        TEXT,
        filled_qty       INTEGER,
        premium_credit   REAL,
        delta_at_entry   REAL,
        iv_rank_at_entry REAL
    )
    """,
    # ── daily_summaries ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS daily_summaries (
        date              TEXT PRIMARY KEY,
        decisions_total   INTEGER NOT NULL DEFAULT 0,
        skips             INTEGER NOT NULL DEFAULT 0,
        trades_executed   INTEGER NOT NULL DEFAULT 0,
        premium_collected REAL NOT NULL DEFAULT 0.0,
        skip_reasons_json TEXT
    )
    """,
    # ── indexes ────────────────────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_decisions_underlying_ts ON decisions(underlying, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON decisions(cycle_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_cycle ON trades(cycle_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_pending ON trades(fill_status) WHERE fill_status = 'pending'",
    "CREATE INDEX IF NOT EXISTS idx_cycles_underlying ON cycles(underlying, strategy_type)",
    # ── orats_cache ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS orats_cache (
        endpoint    TEXT NOT NULL,
        cache_key   TEXT NOT NULL,
        data_json   TEXT NOT NULL,
        fetched_at  REAL NOT NULL,
        ttl_seconds REAL NOT NULL,
        PRIMARY KEY (endpoint, cache_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orats_cache_endpoint ON orats_cache(endpoint)",
    # ── claude_api_calls ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS claude_api_calls (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at          TEXT NOT NULL,
        strategy            TEXT,
        phase               TEXT,
        model               TEXT NOT NULL,
        input_tokens        INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens       INTEGER NOT NULL DEFAULT 0,
        latency_ms          INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_api_calls_created ON claude_api_calls(created_at)",
    # ── token_usage ──────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS token_usage (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp               TEXT NOT NULL,
        strategy_type           TEXT NOT NULL,
        underlying              TEXT,
        model                   TEXT NOT NULL,
        input_tokens            INTEGER NOT NULL DEFAULT 0,
        output_tokens           INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens       INTEGER NOT NULL DEFAULT 0,
        cache_creation_tokens   INTEGER NOT NULL DEFAULT 0,
        response_time_ms        INTEGER,
        estimated_cost_usd      REAL,
        decision_action         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage(timestamp)",
    # ── symbol_liquidity_snapshots ────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS symbol_liquidity_snapshots (
        snapshot_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol             TEXT NOT NULL,
        strategy_type      TEXT NOT NULL,
        snapshot_date      TEXT NOT NULL,
        source             TEXT NOT NULL,
        avg_ba_spread_pct  REAL,
        avg_oi_at_strikes  INTEGER,
        volume_to_oi_ratio REAL,
        slippage_estimate  REAL,
        sample_count       INTEGER,
        raw_metrics_json   TEXT,
        created_at         TEXT NOT NULL,
        UNIQUE(symbol, strategy_type, snapshot_date, source)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_liq_snap_sym_strat ON symbol_liquidity_snapshots(symbol, strategy_type)",
    "CREATE INDEX IF NOT EXISTS idx_liq_snap_date ON symbol_liquidity_snapshots(snapshot_date)",
    # ── symbol_liquidity_scores ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS symbol_liquidity_scores (
        symbol            TEXT NOT NULL,
        strategy_type     TEXT NOT NULL,
        composite_score   REAL NOT NULL,
        tier              TEXT NOT NULL,
        lookback_days     INTEGER NOT NULL,
        snapshot_count    INTEGER NOT NULL,
        below_floor       INTEGER NOT NULL DEFAULT 0,
        confidence        TEXT NOT NULL,
        last_updated      TEXT NOT NULL,
        sub_metrics_json  TEXT,
        PRIMARY KEY (symbol, strategy_type)
    )
    """,
    # ── backtest_trades ───────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS backtest_trades (
        trade_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        sweep_run_id     TEXT NOT NULL,
        symbol           TEXT NOT NULL,
        strategy_type    TEXT NOT NULL,
        entry_date       TEXT NOT NULL,
        exit_date        TEXT,
        entry_credit     REAL,
        exit_debit       REAL,
        pnl              REAL,
        exit_reason      TEXT,
        entry_delta      REAL,
        entry_ivr        REAL,
        entry_regime     TEXT,
        entry_iv_env     TEXT,
        holding_days     INTEGER,
        contracts        INTEGER NOT NULL DEFAULT 1,
        trade_json       TEXT,
        inserted_at      TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bt_trades_sym_strat ON backtest_trades(symbol, strategy_type)",
    "CREATE INDEX IF NOT EXISTS idx_bt_trades_regime ON backtest_trades(entry_regime, strategy_type)",
    "CREATE INDEX IF NOT EXISTS idx_bt_trades_run ON backtest_trades(sweep_run_id)",
    # ── symbol_strategy_stats ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS symbol_strategy_stats (
        symbol            TEXT NOT NULL,
        strategy_type     TEXT NOT NULL,
        trade_count       INTEGER NOT NULL,
        win_count         INTEGER NOT NULL,
        win_rate          REAL NOT NULL,
        avg_pnl_per_trade REAL NOT NULL,
        total_pnl         REAL NOT NULL,
        max_drawdown      REAL NOT NULL,
        sharpe_ratio      REAL,
        confidence        TEXT NOT NULL,
        last_updated      TEXT NOT NULL,
        date_range_start  TEXT NOT NULL,
        date_range_end    TEXT NOT NULL,
        PRIMARY KEY (symbol, strategy_type)
    )
    """,
    # ── regime_strategy_stats ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS regime_strategy_stats (
        entry_regime      TEXT NOT NULL,
        strategy_type     TEXT NOT NULL,
        trade_count       INTEGER NOT NULL,
        win_count         INTEGER NOT NULL,
        win_rate          REAL NOT NULL,
        avg_pnl_per_trade REAL NOT NULL,
        total_pnl         REAL NOT NULL,
        max_drawdown      REAL NOT NULL,
        confidence        TEXT NOT NULL,
        last_updated      TEXT NOT NULL,
        PRIMARY KEY (entry_regime, strategy_type)
    )
    """,
    # ── watchlist_recommendations ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS watchlist_recommendations (
        recommendation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        generated_at        TEXT NOT NULL,
        watchlist_name      TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        action              TEXT NOT NULL,
        score               REAL NOT NULL,
        reasoning           TEXT NOT NULL,
        data_confidence     TEXT NOT NULL,
        operator_decision   TEXT,
        operator_decided_at TEXT,
        sub_scores_json     TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wl_rec_generated ON watchlist_recommendations(generated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wl_rec_action ON watchlist_recommendations(watchlist_name, action)",
    "CREATE INDEX IF NOT EXISTS idx_wl_rec_pending ON watchlist_recommendations(operator_decision) WHERE operator_decision IS NULL",
    # ── recommendation_outcomes ───────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS recommendation_outcomes (
        recommendation_id INTEGER NOT NULL REFERENCES watchlist_recommendations(recommendation_id),
        outcome_type TEXT NOT NULL,
        window_days INTEGER NOT NULL,
        window_end_date TEXT NOT NULL,
        trade_count INTEGER,
        pnl_total REAL,
        pnl_per_trade REAL,
        win_rate REAL,
        proxy_params_json TEXT,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (recommendation_id, outcome_type, window_days)
    )
    """,
    # ── api_usage_ledger ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS api_usage_ledger (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        api           TEXT    NOT NULL,
        endpoint      TEXT    NOT NULL,
        ts            REAL    NOT NULL,
        symbol        TEXT,
        cache_hit     INTEGER NOT NULL,
        status_code   INTEGER,
        duration_ms   INTEGER,
        process_id    INTEGER NOT NULL,
        job_name      TEXT,
        blocked_reason TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ledger_api_ts ON api_usage_ledger(api, ts)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_ts ON api_usage_ledger(ts)",
    # ── sweep_progress ────────────────────────────────────────────────────
    # Tracks which (symbol, strategy) pairs have been primed by the rotating
    # weekly backtest sweep.  Used by BacktestSweep.run_sweep() to prioritise
    # unprimed pairs and skip recently-primed ones.
    """
    CREATE TABLE IF NOT EXISTS sweep_progress (
        symbol            TEXT    NOT NULL,
        strategy          TEXT    NOT NULL,
        lookback_years    INTEGER NOT NULL,
        last_primed_at    REAL,        -- unix epoch; NULL if never primed
        prime_cost_calls  INTEGER,     -- estimated ORATS calls used during last prime
        last_error        TEXT,        -- error message if last prime failed; NULL on success
        PRIMARY KEY (symbol, strategy, lookback_years)
    )
    """,
)


# Idempotent additive migrations applied on every startup. Each is run
# inside its own try/except so that re-applying after the column already
# exists is a no-op (SQLite raises OperationalError "duplicate column").
#
# These columns extend the trades table to support multi-leg spreads:
# wheel rows leave them NULL and behave exactly as before.
_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE trades ADD COLUMN closed_at TEXT",
    "ALTER TABLE trades ADD COLUMN close_fill_price REAL",
    "ALTER TABLE trades ADD COLUMN realized_pnl REAL",
    "ALTER TABLE trades ADD COLUMN outcome TEXT",
    "ALTER TABLE trades ADD COLUMN cb_status_at_entry TEXT",
    "ALTER TABLE decisions ADD COLUMN research_metadata_json TEXT",
    "ALTER TABLE decisions ADD COLUMN skip_gate TEXT",
    "ALTER TABLE decisions ADD COLUMN skip_reason_code TEXT",
    "ALTER TABLE trades ADD COLUMN is_recovery_order INTEGER NOT NULL DEFAULT 0",
    # ── Cost tracking on decisions (added Phase A, 2026-04) ─────────────
    # New columns record token counts and cost for every row where Claude
    # was actually invoked.  Pre-check skips (no Claude call) leave these
    # NULL.  estimated_cost_usd is frozen at write time using the rate in
    # config.CLAUDE_PRICING; it is NEVER recomputed on read.
    "ALTER TABLE decisions ADD COLUMN model_version TEXT",
    "ALTER TABLE decisions ADD COLUMN input_tokens INTEGER",
    "ALTER TABLE decisions ADD COLUMN output_tokens INTEGER",
    "ALTER TABLE decisions ADD COLUMN cache_read_tokens INTEGER",
    "ALTER TABLE decisions ADD COLUMN cache_creation_tokens INTEGER",
    "ALTER TABLE decisions ADD COLUMN estimated_cost_usd REAL",
    """UPDATE watchlist_recommendations
   SET operator_decision = 'expired'
   WHERE operator_decision IS NULL
   AND generated_at < datetime('now', '-14 days')""",
    "ALTER TABLE decisions ADD COLUMN job_run_id TEXT",
    "ALTER TABLE decisions ADD COLUMN pre_check_verdict TEXT",
    "CREATE INDEX IF NOT EXISTS idx_decisions_job_run ON decisions(job_run_id) WHERE job_run_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_decisions_pcv ON decisions(pre_check_verdict, timestamp)",
)


class Database:
    """Owns a single sqlite3.Connection with WAL + foreign keys enabled.

    Connection-per-process is the intended model: the scheduler and
    API server are separate processes on Render. WAL mode lets the
    API server read concurrently while the scheduler writes.
    """

    def __init__(self, path: str | Path | None = None):
        self.path: Path = Path(path) if path is not None else Path(settings.DATABASE_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=True (default) is intentional. If threading
        # is needed later, that decision should be explicit.
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row

        # WAL mode is required for concurrent reader/writer processes.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.commit()

        logger.info("SQLite database opened at %s (WAL mode)", self.path)

    def init_schema(self) -> None:
        """Create all tables and indexes if they do not already exist."""
        cur = self._conn.cursor()
        for stmt in _SCHEMA_STATEMENTS:
            cur.execute(stmt)
        self._conn.commit()
        logger.info("Schema initialized: %d statements applied", len(_SCHEMA_STATEMENTS))
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        """Apply additive ALTER TABLE migrations idempotently.

        SQLite has no ``IF NOT EXISTS`` for ADD COLUMN, so we attempt
        each and swallow the "duplicate column" error that fires once
        the migration has been applied.
        """
        applied = 0
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
                self._conn.commit()
                applied += 1
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" in msg:
                    continue
                logger.warning("Migration failed (%s): %s", stmt, e)
        if applied:
            logger.info("Migrations applied: %d", applied)

    def get_connection(self) -> sqlite3.Connection:
        """Return the underlying sqlite3.Connection for repository injection."""
        return self._conn

    def close(self) -> None:
        """Close the connection. Safe to call multiple times."""
        try:
            self._conn.close()
        except Exception:
            logger.warning("Error while closing database connection", exc_info=True)
