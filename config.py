"""Loads .env configuration and exposes project settings."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


class Settings:
    def __init__(self):
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

        self.TIMEZONE: str = "America/New_York"

        watchlist_env = os.getenv("WATCHLIST", "")
        self.WATCHLIST: list[str] = (
            [s.strip() for s in watchlist_env.split(",") if s.strip()]
            if watchlist_env
            else ["AAPL", "SPY"]
        )

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
