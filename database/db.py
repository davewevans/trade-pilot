"""SQLite connection management and schema initialization for trade-pilot."""

import logging
import sqlite3
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


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
