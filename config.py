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

        self.ALPACA_API_KEY: str = self._require("ALPACA_API_KEY")
        self.ALPACA_SECRET_KEY: str = self._require("ALPACA_SECRET_KEY")
        self.ALPACA_PAPER: bool = os.getenv("ALPACA_PAPER", "true").lower() == "true"

        # Wheel Strategy account credentials
        self.ALPACA_WHEEL_API_KEY: str = os.getenv("ALPACA_WHEEL_API_KEY", "")
        self.ALPACA_WHEEL_SECRET_KEY: str = os.getenv("ALPACA_WHEEL_SECRET_KEY", "")

        # Iron Condor account credentials
        self.ALPACA_IRON_CONDOR_API_KEY: str = os.getenv("ALPACA_IRON_CONDOR_API_KEY", "")
        self.ALPACA_IRON_CONDOR_SECRET_KEY: str = os.getenv("ALPACA_IRON_CONDOR_SECRET_KEY", "")

        if self.ALPACA_PAPER:
            self.ALPACA_TRADE_URL = "https://paper-api.alpaca.markets"
            self.ALPACA_STREAM_URL = "wss://paper-api.alpaca.markets/stream"
        else:
            self.ALPACA_TRADE_URL = "https://api.alpaca.markets"
            self.ALPACA_STREAM_URL = "wss://api.alpaca.markets/stream"

        self.ALPACA_DATA_URL = "https://data.alpaca.markets"

        self.ANTHROPIC_API_KEY: str = self._require("ANTHROPIC_API_KEY")

        self.FRED_API_KEY: str = self._require("FRED_API_KEY")

        # ORATS — IV rank, skew, term structure, expected move
        self.ORATS_API_KEY: str = os.getenv("ORATS_API_KEY", "")

        # Finnhub — earnings calendar (free tier: 60 req/min)
        self.FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

        # --- Environment / deployment mode ---
        self.RENDER: bool = os.getenv("RENDER", "false").lower() == "true"

        if self.RENDER:
            self.DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/data"))
        else:
            self.DATA_DIR: Path = Path("data")

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
        for sub in ("daily", "weekly", "positions", "strategies"):
            (self.REPORTS_DIR / sub).mkdir(parents=True, exist_ok=True)

        # Log active mode
        if self.RENDER:
            log.info("Running in PRODUCTION (Render)")
        else:
            log.info("Running in LOCAL mode")

    def _load_watchlist(self) -> None:
        """Load WATCHLIST and SPREAD_WATCHLIST from data/watchlist.json."""
        watchlist_path = self.DATA_DIR / "watchlist.json"
        if watchlist_path.exists():
            try:
                data = json.loads(watchlist_path.read_text(encoding="utf-8"))
                self.WATCHLIST: list[str] = data.get("wheel", ["AAPL", "SPY"])
                self.SPREAD_WATCHLIST: list[str] = data.get("spreads", list(self.WATCHLIST))
                log.info(
                    "Loaded watchlist from %s: %d wheel, %d spreads",
                    watchlist_path,
                    len(self.WATCHLIST),
                    len(self.SPREAD_WATCHLIST),
                )
                return
            except Exception:
                log.exception("Failed to parse watchlist.json — using built-in defaults")

        # watchlist.json missing or unreadable — seed from built-in defaults
        log.warning("watchlist.json not found at %s; using built-in defaults", watchlist_path)
        self.WATCHLIST = ["AAPL", "SPY", "MSFT", "AMD", "JPM", "XOM"]
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
    # Three strategies share the default account (ALPACA_API_KEY).
    STRATEGY_ACCOUNT_MAP: dict[str, tuple[str, str]] = {
        "wheel":              ("ALPACA_WHEEL_API_KEY",       "ALPACA_WHEEL_SECRET_KEY"),
        "iron_condor":        ("ALPACA_IRON_CONDOR_API_KEY", "ALPACA_IRON_CONDOR_SECRET_KEY"),
        "bull_put_spread":    ("ALPACA_API_KEY",             "ALPACA_SECRET_KEY"),
        "bear_call_spread":   ("ALPACA_API_KEY",             "ALPACA_SECRET_KEY"),
        "long_call_vertical": ("ALPACA_API_KEY",             "ALPACA_SECRET_KEY"),
    }

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


settings = Settings()
