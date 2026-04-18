"""Loads .env configuration and exposes project settings."""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


class Settings:
    def __init__(self):
        # Re-exported from version.py so callers reference settings.VERSION
        # rather than importing the version module everywhere.
        from version import VERSION, VERSION_DATE, VERSION_NOTES
        self.VERSION: str = VERSION
        self.VERSION_DATE: str = VERSION_DATE
        self.VERSION_NOTES: str = VERSION_NOTES

        self.BROKER: str = os.getenv("BROKER", "alpaca")

        self.ALPACA_PAPER1_API_KEY: str = self._require("ALPACA_PAPER1_API_KEY")
        self.ALPACA_PAPER1_SECRET_KEY: str = self._require("ALPACA_PAPER1_SECRET_KEY")
        self.ALPACA_PAPER: bool = os.getenv("ALPACA_PAPER", "true").lower() == "true"

        # Paper Account 2 credentials
        self.ALPACA_PAPER2_API_KEY: str = os.getenv("ALPACA_PAPER2_API_KEY", "")
        self.ALPACA_PAPER2_SECRET_KEY: str = os.getenv("ALPACA_PAPER2_SECRET_KEY", "")

        # Paper Account 3 credentials
        self.ALPACA_PAPER3_API_KEY: str = os.getenv("ALPACA_PAPER3_API_KEY", "")
        self.ALPACA_PAPER3_SECRET_KEY: str = os.getenv("ALPACA_PAPER3_SECRET_KEY", "")

        # Paper Account 4 credentials
        self.ALPACA_PAPER4_API_KEY: str = os.getenv("ALPACA_PAPER4_API_KEY", "")
        self.ALPACA_PAPER4_SECRET_KEY: str = os.getenv("ALPACA_PAPER4_SECRET_KEY", "")

        # Paper Account 5 credentials
        self.ALPACA_PAPER5_API_KEY: str = os.getenv("ALPACA_PAPER5_API_KEY", "")
        self.ALPACA_PAPER5_SECRET_KEY: str = os.getenv("ALPACA_PAPER5_SECRET_KEY", "")

        # Paper Account 6 credentials
        self.ALPACA_PAPER6_API_KEY: str = os.getenv("ALPACA_PAPER6_API_KEY", "")
        self.ALPACA_PAPER6_SECRET_KEY: str = os.getenv("ALPACA_PAPER6_SECRET_KEY", "")

        # Conservative Wheel account credentials (fourth dedicated paper account)
        self.ALPACA_CONSERVATIVE_WHEEL_API_KEY: str = os.getenv("ALPACA_CONSERVATIVE_WHEEL_API_KEY", "")
        self.ALPACA_CONSERVATIVE_WHEEL_SECRET_KEY: str = os.getenv("ALPACA_CONSERVATIVE_WHEEL_SECRET_KEY", "")

        if self.ALPACA_PAPER:
            self.ALPACA_TRADE_URL = "https://paper-api.alpaca.markets"
            self.ALPACA_STREAM_URL = "wss://paper-api.alpaca.markets/stream"
        else:
            self.ALPACA_TRADE_URL = "https://api.alpaca.markets"
            self.ALPACA_STREAM_URL = "wss://api.alpaca.markets/stream"

        self.ALPACA_DATA_URL = "https://data.alpaca.markets"

        self.ANTHROPIC_API_KEY: str = self._require("ANTHROPIC_API_KEY")
        # Prompt cache TTL passed as cache_control.ttl on ephemeral blocks.
        # Default "5m"; set PROMPT_CACHE_TTL=1h to extend after Story 1 data
        # shows the cache is warming correctly.
        self.PROMPT_CACHE_TTL: str = os.getenv("PROMPT_CACHE_TTL", "5m")
        # Adaptive thinking mode for ClaudeAdvisor.
        # "off" = no thinking (default, current behavior).
        # "adaptive_medium" / "adaptive_high" = enable via output_config.effort.
        # Do NOT enable in production until the A/B harness (Story 3) shows
        # clear decision improvement — thinking tokens are billed at output rates.
        self.THINKING_MODE: str = os.getenv("THINKING_MODE", "off")

        self.FRED_API_KEY: str = self._require("FRED_API_KEY")

        # ORATS — IV rank, skew, term structure, expected move
        self.ORATS_API_KEY: str = os.getenv("ORATS_API_KEY", "")

        # Finnhub — earnings calendar (free tier: 60 req/min)
        self.FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

        # --- Environment / deployment mode ---
        self.RENDER: bool = os.getenv("RENDER", "false").lower() == "true"

        if self.RENDER:
            self.DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/data")).resolve()
        else:
            self.DATA_DIR: Path = Path("data").resolve()

        self.REPORTS_DIR: Path = self.DATA_DIR / "reports"
        self.JOURNAL_PATH: Path = self.DATA_DIR / "journal.jsonl"
        self.LOG_DIR: Path = self.DATA_DIR / "logs"

        self.SNAPSHOTS_DIR: Path = self.DATA_DIR / "snapshots"
        self.DATABASE_PATH: Path = Path(
            os.getenv("DATABASE_PATH", str(self.DATA_DIR / "trade_pilot.db"))
        )

        self.TIMEZONE: str = "America/New_York"

        self._load_watchlist()

        self.DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"

        # Research layer — liquidity scoring
        self.RESEARCH_SCORE_MULTIPLIER_ENABLED: bool = (
            os.getenv("RESEARCH_SCORE_MULTIPLIER_ENABLED", "true").lower() == "true"
        )
        self.RESEARCH_MIN_SNAPSHOTS_FOR_SCORING: int = int(
            os.getenv("RESEARCH_MIN_SNAPSHOTS_FOR_SCORING", "30")
        )
        self.RESEARCH_LOOKBACK_DAYS: int = int(
            os.getenv("RESEARCH_LOOKBACK_DAYS", "30")
        )
        self.RESEARCH_SCAN_BELOW_FLOOR: bool = (
            os.getenv("RESEARCH_SCAN_BELOW_FLOOR", "false").lower() == "true"
        )

        # Research layer — backtest stats
        self.RESEARCH_WINRATE_MULTIPLIER_ENABLED: bool = (
            os.getenv("RESEARCH_WINRATE_MULTIPLIER_ENABLED", "true").lower() == "true"
        )
        self.RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE: int = int(
            os.getenv("RESEARCH_BACKTEST_MIN_TRADES_HIGH_CONFIDENCE", "30")
        )
        self.RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE: int = int(
            os.getenv("RESEARCH_BACKTEST_MIN_TRADES_LOW_CONFIDENCE", "10")
        )
        self.RESEARCH_BACKTEST_REGIME_MIN_TRADES: int = int(
            os.getenv("RESEARCH_BACKTEST_REGIME_MIN_TRADES", "100")
        )
        self.RESEARCH_BACKTEST_LOOKBACK_YEARS: int = int(
            os.getenv("RESEARCH_BACKTEST_LOOKBACK_YEARS", "3")
        )
        self.RESEARCH_BACKTEST_SWEEP_MODE: str = os.getenv(
            "RESEARCH_BACKTEST_SWEEP_MODE", "watchlist"
        )
        self.RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN: int = int(
            os.getenv("RESEARCH_BACKTEST_MAX_SYMBOLS_PER_RUN", "50")
        )
        # Max ORATS historical calls consumed by a single weekly_research sweep run.
        # The rotating sweep stops when this budget or the monthly cap is exhausted.
        self.WEEKLY_SWEEP_BUDGET_CALLS: int = int(
            os.getenv("WEEKLY_SWEEP_BUDGET_CALLS", "3000")
        )
        # Weeks after which a primed (symbol, strategy) pair becomes eligible for
        # re-priming to pick up recent backtest data.
        self.SWEEP_REPRIME_WEEKS: int = int(
            os.getenv("SWEEP_REPRIME_WEEKS", "4")
        )

        # Research layer — watchlist recommendations
        self.RESEARCH_RECOMMENDATIONS_ENABLED: bool = (
            os.getenv("RESEARCH_RECOMMENDATIONS_ENABLED", "true").lower() == "true"
        )
        self.RESEARCH_MAX_RECOMMENDATIONS_PER_LIST: int = int(
            os.getenv("RESEARCH_MAX_RECOMMENDATIONS_PER_LIST", "5")
        )
        self.RESEARCH_REMOVE_MIN_WEEKS_OBSERVED: int = int(
            os.getenv("RESEARCH_REMOVE_MIN_WEEKS_OBSERVED", "12")
        )

        # ── ORATS quota protection ─────────────────────────────
        # Monthly caps (ORATS plan: 20,000/month; 2,000-call buffer reserved)
        self.ORATS_HISTORICAL_MONTHLY_CAP: int = int(
            os.getenv("ORATS_HISTORICAL_MONTHLY_CAP", "14000")
        )
        self.ORATS_LIVE_MONTHLY_CAP: int = int(
            os.getenv("ORATS_LIVE_MONTHLY_CAP", "4000")
        )
        # Daily caps (historical is bursty so the whole monthly budget is
        # usable in one day; live is capped at ~2× baseline ~350-525/day)
        self.ORATS_HISTORICAL_DAILY_CAP: int = int(
            os.getenv("ORATS_HISTORICAL_DAILY_CAP", "14000")
        )
        self.ORATS_LIVE_DAILY_CAP: int = int(
            os.getenv("ORATS_LIVE_DAILY_CAP", "700")
        )
        # Per-minute caps (reduced from historical 900 to leave headroom)
        self.ORATS_HISTORICAL_MINUTE_CAP: int = int(
            os.getenv("ORATS_HISTORICAL_MINUTE_CAP", "600")
        )
        self.ORATS_LIVE_MINUTE_CAP: int = int(
            os.getenv("ORATS_LIVE_MINUTE_CAP", "120")
        )
        # Set to 1 to override pre-flight abort when sweep estimate > 80% of
        # remaining monthly budget (see jobs/weekly_research.py pre-flight check).
        self.ORATS_ALLOW_BUDGET_HEAVY: int = int(
            os.getenv("ORATS_ALLOW_BUDGET_HEAVY", "0")
        )
        # Set to "1" to allow ORATSCache to silently fall back to in-memory when
        # SQLite init fails.  In production (RENDER=true) the default is to raise
        # rather than degrade invisibly.  Dev always falls back with a CRITICAL log.
        self.ORATS_CACHE_ALLOW_FALLBACK: str = os.getenv("ORATS_CACHE_ALLOW_FALLBACK", "0")

        # ── Notifications ──────────────────────────────────────
        # ntfy.sh topic name. When unset, ntfy notifications are silently dropped.
        # Set to any unique string (e.g. "trade-pilot-abc123") to enable push alerts.
        self.NTFY_TOPIC: str = os.getenv("NTFY_TOPIC", "")
        # ntfy server URL. Defaults to the public ntfy.sh server.
        # Override for self-hosted deployments (e.g. "https://ntfy.example.com").
        self.NTFY_SERVER: str = os.getenv("NTFY_SERVER", "https://ntfy.sh")
        # Controls severity of fill notifications.
        # "true" (default) → critical (immediate push); "false" → info (digest only).
        self.ALERT_FILLS: bool = os.getenv("ALERT_FILLS", "true").lower() == "true"

        # Circuit breaker thresholds (percentages)
        self.DAILY_LOSS_HALT_PCT: float = float(os.getenv("DAILY_LOSS_HALT_PCT", "3.0"))
        self.DAILY_LOSS_REDUCE_PCT: float = float(os.getenv("DAILY_LOSS_REDUCE_PCT", "1.5"))
        self.WEEKLY_LOSS_HALT_PCT: float = float(os.getenv("WEEKLY_LOSS_HALT_PCT", "5.0"))
        self.DRAWDOWN_HALT_PCT: float = float(os.getenv("DRAWDOWN_HALT_PCT", "10.0"))
        self.DRAWDOWN_LOCK_PCT: float = float(os.getenv("DRAWDOWN_LOCK_PCT", "15.0"))

        # Create required directories
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        (self.DATA_DIR / "locks").mkdir(parents=True, exist_ok=True)
        for sub in ("daily", "weekly", "positions", "strategies"):
            (self.REPORTS_DIR / sub).mkdir(parents=True, exist_ok=True)

        # Log active mode
        if self.RENDER:
            log.info("Running in PRODUCTION (Render)")
        else:
            log.info("Running in LOCAL mode")

    def get_account_manager(self):
        """Return the AccountManager singleton (lazy init).

        Prefer this over direct AccountManager imports to share a single instance.
        """
        if not hasattr(self, "_account_manager"):
            from data.account_manager import AccountManager
            self._account_manager = AccountManager()
        return self._account_manager

    def _load_watchlist(self) -> None:
        """Load WATCHLIST and SPREAD_WATCHLIST from data/watchlist.json.

        # DEPRECATED: Watchlists now live in account_config.json per account.
        """
        watchlist_path = self.DATA_DIR / "watchlist.json"
        if watchlist_path.exists():
            try:
                data = json.loads(watchlist_path.read_text(encoding="utf-8"))
                self.WATCHLIST: list[str] = data.get("wheel", ["AAPL", "SPY"])
                self.CONSERVATIVE_WHEEL_WATCHLIST: list[str] = data.get("conservative_wheel", list(self.WATCHLIST))
                self.SPREAD_WATCHLIST: list[str] = data.get("spreads", list(self.WATCHLIST))
                self.IRON_CONDOR_WATCHLIST: list[str] = data.get("iron_condor", list(self.SPREAD_WATCHLIST))
                log.info(
                    "Loaded watchlist from %s: %d wheel, %d conservative_wheel, %d iron_condor, %d spreads",
                    watchlist_path, len(self.WATCHLIST), len(self.CONSERVATIVE_WHEEL_WATCHLIST),
                    len(self.IRON_CONDOR_WATCHLIST), len(self.SPREAD_WATCHLIST),
                )
                return
            except Exception:
                log.exception("Failed to parse watchlist.json — using built-in defaults")

        # watchlist.json missing or unreadable — seed from built-in defaults
        log.warning("watchlist.json not found at %s; using built-in defaults", watchlist_path)
        self.WATCHLIST = ["AAPL", "SPY", "MSFT", "AMD", "JPM", "XOM"]
        self.CONSERVATIVE_WHEEL_WATCHLIST = list(self.WATCHLIST)
        self.IRON_CONDOR_WATCHLIST = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN", "JPM", "XOM", "META", "NVDA"]
        self.SPREAD_WATCHLIST = [
            "AAPL", "MSFT", "AMD", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
            "JPM", "GS", "BAC", "XOM", "CVX", "JNJ", "UNH", "PFE",
            "SPY", "QQQ", "IWM", "DIS", "NFLX", "CRM", "ORCL", "ADBE",
            "HD", "LOW", "COST", "BA", "CAT", "DE",
        ]

    # Sector mapping for correlation awareness.
    # Used by the guardrails (sector concentration) and reporting to flag
    # sector concentration across wheel positions.
    SYMBOL_SECTORS: dict[str, str] = {
        "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
        "AMZN": "Technology", "META": "Technology", "NVDA": "Technology",
        "AMD": "Technology", "QQQ": "Index",
        "SPY": "Index", "IWM": "Index",
        "JPM": "Financials", "GS": "Financials", "BAC": "Financials",
        "XOM": "Energy", "CVX": "Energy",
        "JNJ": "Healthcare", "UNH": "Healthcare",
        "PG": "Consumer Staples", "KO": "Consumer Staples",
        "TSLA": "Consumer Discretionary",
    }

    # Maps each strategy to its account's env var names.
    # Three strategies share Paper Account 1 (ALPACA_PAPER1_API_KEY).
    # DEPRECATED: Use AccountManager instead. Will be removed in v1.1.
    STRATEGY_ACCOUNT_MAP: dict[str, tuple[str, str]] = {
        "wheel":               ("ALPACA_PAPER2_API_KEY", "ALPACA_PAPER2_SECRET_KEY"),
        "conservative_wheel":  ("ALPACA_CONSERVATIVE_WHEEL_API_KEY", "ALPACA_CONSERVATIVE_WHEEL_SECRET_KEY"),
        "iron_condor":         ("ALPACA_PAPER3_API_KEY", "ALPACA_PAPER3_SECRET_KEY"),
        "bull_put_spread":     ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
        "bear_call_spread":    ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
        "long_call_vertical":  ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
        "iron_butterfly":      ("ALPACA_PAPER4_API_KEY", "ALPACA_PAPER4_SECRET_KEY"),
        "calendar_spread":     ("ALPACA_PAPER5_API_KEY", "ALPACA_PAPER5_SECRET_KEY"),
    }

    # Conservative Wheel position sizing (differs from standard wheel)
    CONSERVATIVE_WHEEL_MAX_POSITION_PCT: float = 0.05   # 5% of BP per position
    CONSERVATIVE_WHEEL_MAX_CONCURRENT: int = 10

    def get_broker_credentials(self, strategy_name: str) -> tuple[str, str]:
        """Return (api_key, secret_key) for the account assigned to *strategy_name*."""
        if strategy_name not in self.STRATEGY_ACCOUNT_MAP:
            raise ValueError(
                f"Unknown strategy '{strategy_name}'. "
                f"Valid strategies: {list(self.STRATEGY_ACCOUNT_MAP.keys())}"
            )
        key_var, secret_var = self.STRATEGY_ACCOUNT_MAP[strategy_name]
        api_key = getattr(self, key_var, "") or os.getenv(key_var, "")
        secret_key = getattr(self, secret_var, "") or os.getenv(secret_var, "")
        if not api_key or not secret_key:
            raise ValueError(
                f"Credentials for strategy '{strategy_name}' not set. "
                f"Expected env vars: {key_var}, {secret_var}"
            )
        return api_key, secret_key

    def get_all_broker_credentials(self) -> list[tuple[str, str]]:
        """Return a deduplicated list of (api_key, secret_key) for all distinct accounts."""
        seen: set[tuple[str, str]] = set()
        result: list[tuple[str, str]] = []
        for key_var, secret_var in self.STRATEGY_ACCOUNT_MAP.values():
            api_key = getattr(self, key_var, "") or os.getenv(key_var, "")
            secret_key = getattr(self, secret_var, "") or os.getenv(secret_var, "")
            pair = (api_key, secret_key)
            if pair not in seen and api_key:
                seen.add(pair)
                result.append(pair)
        return result

    @staticmethod
    def _require(name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise ValueError(f"Missing required environment variable: {name}")
        return value


# ── Canonical schedule — single source of truth ───────────────────────────────
# scheduler.py iterates over this to register jobs with the ``schedule`` library.
# The API exposes this via GET /api/schedule so the frontend can render it
# dynamically without duplicating times in component code.
#
# Fields per entry:
#   job              — matches the key in scheduler.py's _JOB_FN mapping
#   type             — "weekday" | "interval" | "weekly" | "daily"
#   time             — HH:MM in the tz timezone (omitted for interval type)
#   tz               — IANA timezone string (all non-interval entries use ET)
#   interval_minutes — minutes between runs (interval type only)
#   day              — day name, e.g. "sunday" (weekly type only)
#   label            — short title shown in the dashboard schedule section
#   description      — full prose description of what the job does
SCHEDULE: list[dict] = [
    # ── Weekday jobs (Monday–Friday, America/New_York) ──────────────────────
    {
        "job": "pre_market",
        "type": "weekday",
        "time": "06:00",
        "tz": "America/New_York",
        "label": "Pre-market data fetch",
        "description": (
            "FRED macro data, VIX, Fear & Greed index, and the Finnhub earnings calendar. "
            "Regime classification runs here so the rest of the day's jobs see current conditions."
        ),
    },
    {
        "job": "market_open",
        "type": "weekday",
        "time": "10:00",
        "tz": "America/New_York",
        "label": "Entry evaluation",
        "description": (
            "The bot scans the market, builds context for each watchlist symbol, and decides "
            "whether to open new positions. This is the only time new trades are opened. "
            "Runs at 10:00 rather than 9:30 — options bid-ask spreads are 2-3x wider and "
            "quoted Greeks are unreliable in the first 30 minutes after the equity open."
        ),
    },
    {
        "job": "position_check",
        "type": "weekday",
        "time": "10:45",
        "tz": "America/New_York",
        "label": "First position check",
        "description": (
            "For every open position, the bot fetches current option prices and evaluates: "
            "has the profit target been hit? Has delta doubled? Is there a risk that needs attention?"
        ),
    },
    {
        "job": "position_check",
        "type": "weekday",
        "time": "11:30",
        "tz": "America/New_York",
        "label": "Late-morning check",
        "description": (
            "Same evaluation as 10:45 — re-checks all open positions with updated prices."
        ),
    },
    {
        "job": "position_check",
        "type": "weekday",
        "time": "12:30",
        "tz": "America/New_York",
        "label": "Midday check",
        "description": (
            "Midday management pass. Same evaluation as earlier checks with updated prices."
        ),
    },
    {
        "job": "position_check",
        "type": "weekday",
        "time": "14:00",
        "tz": "America/New_York",
        "label": "Afternoon check",
        "description": (
            "Last management pass before the end-of-day sequence begins."
        ),
    },
    {
        "job": "expiry_guard",
        "type": "weekday",
        "time": "15:00",
        "tz": "America/New_York",
        "label": "Expiry guard",
        "description": (
            "Safety sweep specifically for positions expiring today. Any short option that is "
            "in the money gets closed immediately to avoid surprise assignment."
        ),
    },
    {
        "job": "pre_close",
        "type": "weekday",
        "time": "15:15",
        "tz": "America/New_York",
        "label": "Pre-close observation",
        "description": (
            "Scans for positions within 7 DTE and logs warnings. "
            "No new orders are placed this close to market close."
        ),
    },
    {
        "job": "market_close",
        "type": "weekday",
        "time": "16:00",
        "tz": "America/New_York",
        "label": "Market close",
        "description": (
            "End-of-day sequence: reconcile orders, update position states, "
            "write the daily report."
        ),
    },
    {
        "job": "post_market",
        "type": "weekday",
        "time": "16:30",
        "tz": "America/New_York",
        "label": "Post-market cleanup",
        "description": (
            "Post-market cleanup: snapshot archival, NTA event processing, fill quality logging."
        ),
    },
    # ── Interval job (weekdays only, checked every 5 minutes) ───────────────
    {
        "job": "portfolio_refresh",
        "type": "interval",
        "interval_minutes": 5,
        "label": "Portfolio refresh",
        "description": (
            "Refreshes dashboard data every 5 minutes during market hours: "
            "equity balances, open positions, circuit breaker status, and spread reconciliation."
        ),
    },
    # ── Weekly job ───────────────────────────────────────────────────────────
    {
        "job": "weekly_report",
        "type": "weekly",
        "day": "sunday",
        "time": "18:00",
        "tz": "America/New_York",
        "label": "Weekly report",
        "description": (
            "Generates and emails the weekly performance summary: "
            "P&L, win rate, strategy breakdown, and circuit breaker events."
        ),
    },
    # ── Daily maintenance job ────────────────────────────────────────────────
    {
        "job": "orats_cache_cleanup",
        "type": "daily",
        "time": "05:00",
        "tz": "America/New_York",
        "label": "ORATS cache cleanup",
        "description": (
            "Expires stale ORATS cache entries from SQLite to keep the cache within quota limits."
        ),
    },
]

settings = Settings()
