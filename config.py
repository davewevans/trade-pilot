"""Loads .env configuration and exposes project settings."""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    """Parse a boolean env var with safe defaults.

    Returns ``default`` when the env var is unset, empty, or whitespace-only.
    Explicit-false values: ``"false"``, ``"0"``, ``"no"``, ``"off"`` (case-insensitive).
    Anything else (``"true"``, ``"1"``, ``"yes"``, unrecognized values) returns ``True``.

    Avoids the silent-disable footgun of ``os.getenv("X", "true").lower() == "true"``,
    which returns ``False`` when the env var is set to an empty string.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized == "":
        return default
    return normalized not in ("false", "0", "no", "off")


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
        self.ALPACA_PAPER: bool = _env_bool("ALPACA_PAPER")

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

        # Paper Account 6 credentials (Turnover Wheel)
        self.ALPACA_PAPER6_API_KEY: str = os.getenv("ALPACA_PAPER6_API_KEY", "")
        self.ALPACA_PAPER6_SECRET_KEY: str = os.getenv("ALPACA_PAPER6_SECRET_KEY", "")

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
        self.USE_FRED_FOR_VIX: bool = _env_bool("USE_FRED_FOR_VIX")
        self.USE_ALPACA_FOR_EX_DIVIDEND: bool = _env_bool("USE_ALPACA_FOR_EX_DIVIDEND")

        # ORATS — IV rank, skew, term structure, expected move
        self.ORATS_API_KEY: str = os.getenv("ORATS_API_KEY", "")

        # Finnhub — earnings calendar (free tier: 60 req/min)
        self.FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")
        # When False (default), price_target / upgrade_downgrade / news_sentiment
        # are preemptively disabled at startup — free tier returns 403 on all three.
        # Set True after upgrading the Finnhub plan.
        self.FINNHUB_PAID_TIER: bool = (
            os.getenv("FINNHUB_PAID_TIER", "false").lower() == "true"
        )

        # When True, clamp option-chain strike range to ±OPTION_CHAIN_STRIKE_CLAMP_PCT
        # of spot before fetching. Reduces fan-out from ~1000 contracts to ~100 on
        # high-price names (SPY, AMZN), avoiding Alpaca's 500-contract cap and
        # speeding context builds. Default True — old behaviour is provably wrong
        # (data loss at cap). Set False only to roll back if the clamp cuts something.
        self.OPTION_CHAIN_STRIKE_PRECLAMP_ENABLED: bool = _env_bool("OPTION_CHAIN_STRIKE_PRECLAMP_ENABLED")
        # One-sided clamp width as fraction of spot. 0.25 = ±25%.
        # Do not lower below 0.15 without verifying target delta range stays inside.
        self.OPTION_CHAIN_STRIKE_CLAMP_PCT: float = float(
            os.getenv("OPTION_CHAIN_STRIKE_CLAMP_PCT", "0.25")
        )

        # --- Environment / deployment mode ---
        self.RENDER: bool = os.getenv("RENDER", "false").lower() == "true"

        if self.RENDER:
            self.DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/data")).resolve()
        else:
            self.DATA_DIR: Path = Path("data").resolve()

        self.REPORTS_DIR: Path = self.DATA_DIR / "reports"
        self.JOURNAL_PATH: Path = self.DATA_DIR / "journal.jsonl"
        self.LOG_DIR: Path = self.DATA_DIR / "logs"

        # Structured JSONL log capture to persistent disk. Tees WARNING/ERROR
        # records (with tracebacks + extra fields) to a daily file. Consumed
        # by the daily_bundle endpoint.
        self.STRUCTURED_LOG_CAPTURE_ENABLED: bool = _env_bool("STRUCTURED_LOG_CAPTURE_ENABLED")
        self.STRUCTURED_LOG_DIR: str = os.getenv(
            "STRUCTURED_LOG_DIR",
            str(self.DATA_DIR / "snapshots" / "logs"),
        )
        self.STRUCTURED_LOG_RETENTION_DAYS: int = int(
            os.getenv("STRUCTURED_LOG_RETENTION_DAYS", "30")
        )

        self.SNAPSHOTS_DIR: Path = self.DATA_DIR / "snapshots"
        self.DATABASE_PATH: Path = Path(
            os.getenv("DATABASE_PATH", str(self.DATA_DIR / "trade_pilot.db"))
        )

        self.TIMEZONE: str = "America/New_York"

        self._load_watchlist()

        self.DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"

        # Research layer — liquidity scoring
        self.RESEARCH_SCORE_MULTIPLIER_ENABLED: bool = _env_bool("RESEARCH_SCORE_MULTIPLIER_ENABLED")
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
        self.RESEARCH_WINRATE_MULTIPLIER_ENABLED: bool = _env_bool("RESEARCH_WINRATE_MULTIPLIER_ENABLED")
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

        # When false, iron_butterfly is excluded from the weekly sweep even
        # if SUPPORTED_STRATEGIES allows it. Default false until first
        # production sweep is verified.
        self.RESEARCH_SWEEP_IRON_BUTTERFLY_ENABLED: bool = (
            os.getenv("RESEARCH_SWEEP_IRON_BUTTERFLY_ENABLED", "false").lower() == "true"
        )

        # When false, calendar_spread is excluded from the weekly sweep even
        # if SUPPORTED_STRATEGIES allows it. Stage activation after
        # iron_butterfly is verified.
        self.RESEARCH_SWEEP_CALENDAR_SPREAD_ENABLED: bool = (
            os.getenv("RESEARCH_SWEEP_CALENDAR_SPREAD_ENABLED", "false").lower() == "true"
        )

        # Research layer — watchlist recommendations
        self.RESEARCH_RECOMMENDATIONS_ENABLED: bool = _env_bool("RESEARCH_RECOMMENDATIONS_ENABLED")
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

        # When true, set data.orats_client logger to DEBUG for one cycle.
        # Captures raw ORATS /cores responses before processing. Default false —
        # raw payloads are too verbose for routine operation.
        # Operator: set ORATS_DEBUG_LOGGING=true on Render, run one market_open
        # cycle, inspect logs, then disable.
        self.ORATS_DEBUG_LOGGING: bool = (
            os.getenv("ORATS_DEBUG_LOGGING", "false").lower() == "true"
        )

        # ── Notifications ──────────────────────────────────────
        # ntfy.sh topic name. When unset, ntfy notifications are silently dropped.
        # Set to any unique string (e.g. "trade-pilot-abc123") to enable push alerts.
        self.NTFY_TOPIC: str = os.getenv("NTFY_TOPIC", "")

        # Sentry error monitoring DSN. When unset, Sentry is disabled.
        self.SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")

        # ── Healthchecks.io ────────────────────────────────────
        # Global kill switch. Set HEALTHCHECKS_ENABLED=false to silence all
        # pings without removing individual HC_PING_URL_* vars. Default true.
        self.HEALTHCHECKS_ENABLED: bool = _env_bool("HEALTHCHECKS_ENABLED")
        # ntfy server URL. Defaults to the public ntfy.sh server.
        # Override for self-hosted deployments (e.g. "https://ntfy.example.com").
        self.NTFY_SERVER: str = os.getenv("NTFY_SERVER", "https://ntfy.sh")

        # ── Clock drift fail-safe ──────────────────────────────
        # Set CLOCK_DRIFT_HALT_ENABLED=false to disable the check entirely.
        self.CLOCK_DRIFT_HALT_ENABLED: bool = _env_bool("CLOCK_DRIFT_HALT_ENABLED")
        # Drift exceeding this threshold (in seconds, strict >) triggers a halt.
        self.CLOCK_DRIFT_THRESHOLD_SECONDS: int = int(os.getenv("CLOCK_DRIFT_THRESHOLD_SECONDS", "30"))
        # Per-attempt HTTP timeout when fetching reference time from Alpaca.
        self.CLOCK_DRIFT_FETCH_TIMEOUT_SECONDS: float = float(os.getenv("CLOCK_DRIFT_FETCH_TIMEOUT_SECONDS", "3.0"))
        # Number of fetch attempts before giving up and failing open.
        self.CLOCK_DRIFT_FETCH_MAX_RETRIES: int = int(os.getenv("CLOCK_DRIFT_FETCH_MAX_RETRIES", "3"))
        # Controls severity of fill notifications.
        # "true" (default) → critical (immediate push); "false" → info (digest only).
        self.ALERT_FILLS: bool = _env_bool("ALERT_FILLS")

        # ── Evaluation / scoring ───────────────────────────────
        # Enable the programmatic decision scorer CLI job.
        # Off by default; turn on once the rubric is reviewed and backfill
        # is ready.  When off, --job=score_programmatic logs and exits cleanly.
        self.EVALUATION_SCORER_ENABLED: bool = (
            os.getenv("EVALUATION_SCORER_ENABLED", "false").lower() == "true"
        )

        # ── LLM judge scorer ───────────────────────────────────
        # Off by default; turn on once the judge prompt has been reviewed and
        # ANTHROPIC_API_KEY is confirmed to have Opus access.
        # --job=score_judge logs and exits cleanly when false.
        self.EVALUATION_JUDGE_ENABLED: bool = (
            os.getenv("EVALUATION_JUDGE_ENABLED", "false").lower() == "true"
        )

        # ── Monthly evaluation automation ──────────────────────
        # When true, the monthly_evaluation job runs the full pipeline on the
        # 1st of each month at 05:00 ET and fires an ntfy alert when flags are
        # detected.  Off by default; turn on once scoring flags are stable and
        # the rubric has been reviewed in production.
        self.EVALUATION_AUTOMATION_ENABLED: bool = (
            os.getenv("EVALUATION_AUTOMATION_ENABLED", "false").lower() == "true"
        )
        self.JUDGE_MODEL: str = os.getenv("JUDGE_MODEL", "claude-opus-4-7")
        self.JUDGE_RATE_LIMIT_MS: int = int(os.getenv("JUDGE_RATE_LIMIT_MS", "200"))

        # ── Trading advisor model ──────────────────────────────
        # Model used by ClaudeAdvisor for live trade decisions. Env-overridable
        # so the model can be changed without a code deploy (Render env var).
        self.ADVISOR_MODEL: str = os.getenv("ADVISOR_MODEL", "claude-sonnet-5")

        # Max output tokens for non-thinking advisor calls. 2048 was tuned for
        # claude-sonnet-4-6 (max observed 1790); sonnet-5 overruns it and truncates
        # (stop_reason=max_tokens -> forced SKIP). Env-overridable for tuning without deploy.
        self.ADVISOR_MAX_TOKENS: int = int(os.getenv("ADVISOR_MAX_TOKENS", "4096"))

        # Data source for the /api/claude-costs panel. token_usage is the populated
        # table; "decisions" is the legacy (empty) source, kept as a fallback.
        self.CLAUDE_COSTS_SOURCE: str = os.getenv("CLAUDE_COSTS_SOURCE", "token_usage")

        # ── Hermes report publishing ───────────────────────────
        # When true, the publish_reports job commits the daily evaluation
        # bundle to the private reports repo (GITHUB_REPORTS_REPO) that the
        # Hermes self-improvement agent reads. Off by default; flipped in
        # Render after this and the bundle-enrichment change are deployed.
        # Publishing is data exhaust — failures log and never crash the job.
        self.HERMES_PUBLISH_ENABLED: bool = (
            os.getenv("HERMES_PUBLISH_ENABLED", "false").lower() == "true"
        )
        self.GITHUB_REPORTS_REPO: str = os.getenv("GITHUB_REPORTS_REPO", "")
        self.GITHUB_REPORTS_TOKEN: str = os.getenv("GITHUB_REPORTS_TOKEN", "")

        # ── Self-review extension ──────────────────────────────
        # Master kill switch for the self-review phase of monthly_evaluation.
        # When false (default), the pipeline runs unchanged — no Opus call,
        # no archive append, no notification. Flip true only after reviewing
        # prompts/self_review_v1.md and confirming cost tolerance.
        self.SELF_REVIEW_ENABLED: bool = (
            os.getenv("SELF_REVIEW_ENABLED", "false").lower() == "true"
        )
        # Maximum number of flags self-review will call Opus on per month.
        # If the flag count exceeds this cap, flags are prioritized by
        # severity, then by strategy sample size (larger first). Excess
        # flags are logged and skipped — not an error.
        self.SELF_REVIEW_MAX_FLAGS_PER_MONTH: int = int(
            os.getenv("SELF_REVIEW_MAX_FLAGS_PER_MONTH", "5")
        )
        # Minimum number of supporting decision IDs required for a suggested
        # prompt patch. Enforced in the Opus system prompt AND as a post-call
        # filter. Patches with fewer IDs are dropped and the refusal is logged.
        self.SELF_REVIEW_MIN_SUPPORTING_DECISIONS: int = int(
            os.getenv("SELF_REVIEW_MIN_SUPPORTING_DECISIONS", "3")
        )

        # Circuit breaker thresholds (percentages)
        self.DAILY_LOSS_HALT_PCT: float = float(os.getenv("DAILY_LOSS_HALT_PCT", "3.0"))
        self.DAILY_LOSS_REDUCE_PCT: float = float(os.getenv("DAILY_LOSS_REDUCE_PCT", "1.5"))
        self.WEEKLY_LOSS_HALT_PCT: float = float(os.getenv("WEEKLY_LOSS_HALT_PCT", "5.0"))
        self.DRAWDOWN_HALT_PCT: float = float(os.getenv("DRAWDOWN_HALT_PCT", "10.0"))
        self.DRAWDOWN_LOCK_PCT: float = float(os.getenv("DRAWDOWN_LOCK_PCT", "15.0"))

        # Force-close rules: bypass Claude for catastrophic positions (deep ITM + near expiry).
        # Default true — this is a safety mechanism. Set to "false" to disable.
        self.FORCE_CLOSE_ENABLED: bool = _env_bool("FORCE_CLOSE_ENABLED")

        # Strategy health dashboard page. Read-only; defaults on.
        # Set STRATEGY_HEALTH_PAGE_ENABLED=false to hide the sidebar link
        # and 404 the API endpoint. Does not affect any strategy logic.
        self.STRATEGY_HEALTH_PAGE_ENABLED: bool = _env_bool("STRATEGY_HEALTH_PAGE_ENABLED")

        # Turnover Wheel strategy enable flag.
        # Set TURNOVER_WHEEL_ENABLED=false to exclude it from the active
        # strategy list without touching any other configuration.
        self.TURNOVER_WHEEL_ENABLED: bool = _env_bool("TURNOVER_WHEEL_ENABLED")

        # ── Drop-copy reconciliation ──────────────────────────────────────────
        # Kill switches. Both default to True for paper. Flip either to False
        # to disable without code changes.
        self.STARTUP_RECONCILE_ENABLED: bool = _env_bool("STARTUP_RECONCILE_ENABLED")
        self.DROP_COPY_RECONCILE_ENABLED: bool = _env_bool("DROP_COPY_RECONCILE_ENABLED")

        # Enforcement mode:
        #   "log_only" → compute diffs, write report, NEVER overwrite local state
        #                and NEVER write HALTED.lock. First-week default.
        #   "enforce"  → overwrite local state on mismatch; HALT above threshold.
        # Flip to "enforce" only after one week of clean log-only runs.
        self.DROP_COPY_ENFORCEMENT_MODE: str = os.getenv(
            "DROP_COPY_ENFORCEMENT_MODE", "log_only"
        ).lower()

        # Thresholds — all USD unless suffixed _PCT.
        # Position value mismatch that triggers YELLOW + ntfy (enforce mode).
        self.DROP_COPY_POS_MISMATCH_USD: float = float(
            os.getenv("DROP_COPY_POS_MISMATCH_USD", "100")
        )
        self.DROP_COPY_POS_MISMATCH_PCT: float = float(
            os.getenv("DROP_COPY_POS_MISMATCH_PCT", "1.0")
        )
        # Cash mismatch that triggers YELLOW + ntfy (enforce mode).
        self.DROP_COPY_CASH_MISMATCH_USD: float = float(
            os.getenv("DROP_COPY_CASH_MISMATCH_USD", "100")
        )
        # Startup reconcile HALT threshold. Cumulative abs(position value delta)
        # across all accounts above this → write HALTED.lock (enforce mode).
        self.STARTUP_RECONCILE_HALT_THRESHOLD_USD: float = float(
            os.getenv("STARTUP_RECONCILE_HALT_THRESHOLD_USD", "500")
        )
        # Grace: mismatch must be observed on N consecutive drop-copy cycles
        # before any enforcement action. Tolerates snapshot-write / reconcile-read
        # races. 2 means "seen twice in a row, ~5–10 min apart."
        self.DROP_COPY_GRACE_CYCLES: int = int(
            os.getenv("DROP_COPY_GRACE_CYCLES", "2")
        )

        # ── Cross-account anti-crowding ────────────────────────
        # Blocks net-new entries when the same directional-risk family is already
        # open on the same underlying in another account. Management actions
        # (roll, close) are never blocked. Kill switch: set to "false" to bypass
        # the pre-check while keeping book_exposure visible in Claude context.
        self.CROSS_ACCOUNT_ANTI_CROWDING_ENABLED: bool = _env_bool("CROSS_ACCOUNT_ANTI_CROWDING_ENABLED")

        # ── Structural untradeability filter ──────────────────
        # Pre-Claude check: if the lowest-strike contract in the -0.20 to -0.30
        # delta band has a notional cost (strike × 100) exceeding 10% of
        # options_buying_power, skip the Claude call entirely. Default false —
        # flip true after verifying the expected symbols produce
        # STRUCTURALLY_UNTRADEABLE skip codes on a dry-run.
        self.STRUCTURAL_UNTRADEABLE_FILTER_ENABLED: bool = (
            os.getenv("STRUCTURAL_UNTRADEABLE_FILTER_ENABLED", "false").lower() == "true"
        )

        # ── VIX stale-tolerance ────────────────────────────────
        # When enabled, a cached VIX value is used if the live fetch returns None,
        # provided the cache is within the configured age window.
        # Default false — flip true after verifying the stale flag appears in
        # context["macro"]["vix_stale"] on a dry-run with VIX fetch disabled.
        self.VIX_STALE_TOLERANCE_ENABLED: bool = (
            os.getenv("VIX_STALE_TOLERANCE_ENABLED", "false").lower() == "true"
        )
        # Max cache age during regular trading hours (09:30–16:00 ET). Default 30 min.
        self.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS: int = int(
            os.getenv("VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", "1800")
        )
        # Max cache age outside trading hours (overnight, weekends). Default 4 hours.
        self.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS: int = int(
            os.getenv("VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", "14400")
        )

        # ── Macro event block ─────────────────────────────────
        # Hard-blocks new entries the day of and the trading day before any Tier 1
        # macro event (FOMC, CPI, NFP) listed in data/macro_events.json.
        # Management cycles (rolls, closes) are never blocked.
        self.MACRO_EVENT_BLOCK_ENABLED: bool = _env_bool("MACRO_EVENT_BLOCK_ENABLED")

        # ── Macro event block notifications ───────────────────
        # Edge-triggered ntfy push when the macro block becomes active or clears.
        # Independent of MACRO_EVENT_BLOCK_ENABLED so the operator can mute
        # notifications without disabling the block itself.
        # Default false — flip to true on Render after first deploy verifies
        # the no-op path works. First post-flip transition will fire a real push.
        self.MACRO_BLOCK_NOTIFY_ENABLED: bool = (
            os.getenv("MACRO_BLOCK_NOTIFY_ENABLED", "false").lower() == "true"
        )

        # ── Fill realism / shadow execution ───────────────────
        # Measurement-only NBBO capture around every order submit.
        # When false, all shadow_execution code is a no-op — no DB writes,
        # no ORATS calls, no schedule job activity.
        self.SHADOW_EXECUTION_ENABLED: bool = _env_bool("SHADOW_EXECUTION_ENABLED")

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
        """Load strategy watchlists from data/watchlist.json."""
        watchlist_path = self.DATA_DIR / "watchlist.json"
        if watchlist_path.exists():
            try:
                data = json.loads(watchlist_path.read_text(encoding="utf-8"))
                self.WATCHLIST: list[str] = data.get("wheel", ["AAPL", "SPY"])
                self.TURNOVER_WHEEL_WATCHLIST: list[str] = data.get("turnover_wheel", list(self.WATCHLIST))
                self.SPREAD_WATCHLIST: list[str] = data.get("spreads", list(self.WATCHLIST))
                self.IRON_CONDOR_WATCHLIST: list[str] = data.get("iron_condor", list(self.SPREAD_WATCHLIST))
                self.IRON_BUTTERFLY_WATCHLIST: list[str] = data.get("iron_butterfly", list(self.IRON_CONDOR_WATCHLIST))
                self.CALENDAR_SPREAD_WATCHLIST: list[str] = data.get("calendar_spread", list(self.SPREAD_WATCHLIST))
                log.info(
                    "Loaded watchlist from %s: %d wheel, %d turnover_wheel, %d iron_condor, %d iron_butterfly, %d spreads, %d calendar_spread",
                    watchlist_path, len(self.WATCHLIST), len(self.TURNOVER_WHEEL_WATCHLIST),
                    len(self.IRON_CONDOR_WATCHLIST), len(self.IRON_BUTTERFLY_WATCHLIST),
                    len(self.SPREAD_WATCHLIST), len(self.CALENDAR_SPREAD_WATCHLIST),
                )
                return
            except Exception:
                log.exception("Failed to parse watchlist.json — using built-in defaults")

        # watchlist.json missing or unreadable — seed from built-in defaults
        log.warning("watchlist.json not found at %s; using built-in defaults", watchlist_path)
        self.WATCHLIST = ["AAPL", "SPY", "MSFT", "AMD", "JPM", "XOM"]
        self.TURNOVER_WHEEL_WATCHLIST = list(self.WATCHLIST)
        self.IRON_CONDOR_WATCHLIST = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "GOOGL", "AMZN", "JPM", "XOM", "META", "NVDA"]
        self.IRON_BUTTERFLY_WATCHLIST = list(self.IRON_CONDOR_WATCHLIST)
        self.SPREAD_WATCHLIST = [
            "AAPL", "MSFT", "AMD", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
            "JPM", "GS", "BAC", "XOM", "CVX", "JNJ", "UNH", "PFE",
            "SPY", "QQQ", "IWM", "DIS", "NFLX", "CRM", "ORCL", "ADBE",
            "HD", "LOW", "COST", "BA", "CAT", "DE",
        ]
        self.CALENDAR_SPREAD_WATCHLIST = list(self.SPREAD_WATCHLIST)

    # ETF symbol set. A symbol here is treated as an ETF by get_company_profile(),
    # which applies the SECTOR_ETF_MAP override and skips expecting profile data
    # from Finnhub (Finnhub returns empty {} for all ETFs structurally).
    # Keep in sync with any watchlist.json additions — if a new ETF is added to
    # a watchlist without being listed here, get_company_profile() logs a warning.
    ETF_SYMBOLS: frozenset[str] = frozenset({
        # Broad-market index ETFs
        "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "VEA", "VWO",
        # Sector SPDR ETFs — sector label in SECTOR_ETF_MAP below
        "XLE", "XLF", "XLK", "XLV", "XLP", "XLY", "XLI", "XLB",
        "XLU", "XLRE", "XLC",
        # Commodity ETFs
        "GLD", "SLV", "USO", "UNG",
        # Bond ETFs
        "TLT", "AGG", "LQD", "HYG",
        # Volatility ETFs
        "VIXY", "UVXY",
    })

    # Sector labels for sector-specific ETFs. Broad-market and commodity ETFs
    # are absent intentionally — sector for them is None (SPY is "the market",
    # not a sector). Symbols here must also appear in ETF_SYMBOLS.
    SECTOR_ETF_MAP: dict[str, str] = {
        "XLE": "Energy",
        "XLF": "Financial Services",
        "XLK": "Technology",
        "XLV": "Healthcare",
        "XLP": "Consumer Defensive",
        "XLY": "Consumer Cyclical",
        "XLI": "Industrials",
        "XLB": "Basic Materials",
        "XLU": "Utilities",
        "XLRE": "Real Estate",
        "XLC": "Communication Services",
    }

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
        "turnover_wheel":      ("ALPACA_PAPER6_API_KEY", "ALPACA_PAPER6_SECRET_KEY"),
        "iron_condor":         ("ALPACA_PAPER3_API_KEY", "ALPACA_PAPER3_SECRET_KEY"),
        "bull_put_spread":     ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
        "bear_call_spread":    ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
        "long_call_vertical":  ("ALPACA_PAPER1_API_KEY", "ALPACA_PAPER1_SECRET_KEY"),
        "iron_butterfly":      ("ALPACA_PAPER4_API_KEY", "ALPACA_PAPER4_SECRET_KEY"),
        "calendar_spread":     ("ALPACA_PAPER5_API_KEY", "ALPACA_PAPER5_SECRET_KEY"),
    }

    # Maps each strategy_type to the directional-risk families it occupies.
    # A strategy may occupy multiple families (iron_condor is both short_put AND
    # short_call — its put side and call side are in separate families).
    # `same_underlying_peers` is the list of strategy_types allowed to coexist
    # on the same underlying within that family (e.g. wheel + turnover_wheel by
    # design, to support side-by-side variant comparison on the same watchlist).
    #
    # NOTE: adaptive_spreads is intentionally NOT in this map. At runtime,
    # positions opened by the adaptive_spreads composite strategy carry the
    # concrete sub-strategy name (bull_put_spread / bear_call_spread /
    # long_call_vertical), not "adaptive_spreads". check_anti_crowding() raises
    # ValueError if an unknown strategy_type is ever passed in.
    DIRECTIONAL_FAMILY_MAP: dict[str, dict] = {
        "wheel": {
            "families": ["short_put"],
            "same_underlying_peers": {"short_put": ["turnover_wheel"]},
        },
        "turnover_wheel": {
            "families": ["short_put"],
            "same_underlying_peers": {"short_put": ["wheel"]},
        },
        "bull_put_spread": {
            "families": ["short_put"],
            "same_underlying_peers": {"short_put": []},
        },
        "bear_call_spread": {
            "families": ["short_call"],
            "same_underlying_peers": {"short_call": []},
        },
        "iron_condor": {
            "families": ["short_put", "short_call"],
            "same_underlying_peers": {"short_put": [], "short_call": []},
        },
        "iron_butterfly": {
            "families": ["short_put", "short_call"],
            "same_underlying_peers": {"short_put": [], "short_call": []},
        },
        "long_call_vertical": {
            "families": ["long_directional"],
            "same_underlying_peers": {"long_directional": []},
        },
        # Calendar spread is vega-positive, theta-positive, delta-neutral at
        # entry — fundamentally a vol play, not a directional one. Not
        # classified into any family until live data tells us what crowding
        # looks like for vol plays. Until then: it blocks nothing, nothing
        # blocks it. Revisit after calendar_spread goes active and accumulates
        # decisions.
        "calendar_spread": {
            "families": [],
            "same_underlying_peers": {},
        },
    }

    # Turnover Wheel position sizing (differs from standard wheel)
    TURNOVER_WHEEL_MAX_POSITION_PCT: float = 0.05   # 5% of BP per position
    TURNOVER_WHEEL_MAX_CONCURRENT: int = 10

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
    {
        "job": "publish_reports",
        "type": "weekday",
        "time": "16:45",
        "tz": "America/New_York",
        "label": "Publish daily report",
        "description": (
            "Builds today's daily evaluation bundle and commits it to the private "
            "reports repo that the Hermes self-improvement agent reads. Runs 15 minutes "
            "after post-market so the day's EOD writes have settled. Flag-gated on "
            "HERMES_PUBLISH_ENABLED; a no-op when the flag is off. Publishing failures "
            "are logged and never crash the scheduler."
        ),
    },
    {
        "job": "publish_exposure",
        "type": "weekday",
        "time": "10:15",
        "tz": "America/New_York",
        "label": "Publish exposure snapshot (AM)",
        "description": (
            "Builds the consolidated cross-account exposure snapshot (per-account "
            "positions with sectors + the book-level family view) and commits it to "
            "the reports repo for Hermes's advisory cross-account risk monitor. Runs "
            "after the entry cycle settles. Flag-gated on HERMES_PUBLISH_ENABLED; a "
            "no-op when off. Publishing failures are logged and never crash the scheduler."
        ),
    },
    {
        "job": "publish_exposure",
        "type": "weekday",
        "time": "16:15",
        "tz": "America/New_York",
        "label": "Publish exposure snapshot (PM)",
        "description": (
            "After-close run of the cross-account exposure snapshot publish. Captures "
            "end-of-day positions so Hermes's risk monitor reads the settled book. "
            "Flag-gated on HERMES_PUBLISH_ENABLED; a no-op when off; failures are "
            "logged and never crash the scheduler."
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
    {
        "job": "drop_copy_reconcile",
        "type": "interval",
        "interval_minutes": 5,
        "label": "Drop-copy reconcile",
        "description": (
            "Compares SQLite/local state against Alpaca broker truth every 5 minutes "
            "during market hours. Log-only mode by default; in enforce mode, triggers "
            "YELLOW or RED on mismatches."
        ),
    },
    # ── Weekly research sweep ─────────────────────────────────────────────────
    {
        "job": "weekly_research",
        "type": "weekly",
        "day": "sunday",
        "time": "11:00",
        "tz": "America/New_York",
        "label": "Weekly research sweep",
        "description": (
            "Runs the 4-phase research pipeline: liquidity scan, backtest "
            "sweep, watchlist recommendations, and recommendation outcome "
            "computation. Populates symbol_strategy_stats and "
            "regime_strategy_stats used by the win-rate multiplier."
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
    # ── Shadow execution follow-up capture (interval, weekdays) ─────────────
    {
        "job": "shadow_capture",
        "type": "interval",
        "interval_minutes": 1,
        "label": "Shadow execution capture",
        "description": (
            "Follow-up NBBO capture for pending shadow-execution rows. "
            "Measurement-only; no order side effects."
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
    # ── Monthly evaluation (fires daily at 05:00; runs only on the 1st) ──────
    {
        "job": "monthly_evaluation",
        "type": "daily",
        "time": "05:00",
        "tz": "America/New_York",
        "label": "Monthly evaluation",
        "description": (
            "Runs the full rubric evaluation pipeline for the prior calendar month on "
            "the 1st of each month at 05:00 ET.  Fires on schedule every day but "
            "returns immediately unless today is the 1st (see _monthly_eval_wrapper).  "
            "Requires EVALUATION_AUTOMATION_ENABLED=true."
        ),
    },
]

settings = Settings()


# ── Fill-realism gate thresholds (PROVISIONAL) ─────────────────────────────────
# Empirical, not theoretical. At n=100, the standard error on a true 80% rate is
# ±4% (95% CI ≈ 72–88%), which is tight enough for a go/no-go gate but narrow
# enough that these should be revisited once the first strategy crosses 100
# closed trades and we see what the realised distribution looks like.
FILL_REALISM_GATE_SAMPLE: int = 100
FILL_REALISM_GATE_PCT: float = 80.0


# ── Claude API pricing ────────────────────────────────────────────────────────
# Rates are per million tokens (USD) as of 2026-04.
# IMPORTANT: Never modify existing entries — historical cost rows were written
# using the rates that were in effect at the time of write.  Add new entries for
# new model versions; leave old ones untouched.
CLAUDE_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_read_per_mtok": 0.30,
        # 1-hour ephemeral cache write (the TTL used by claude_advisor.py)
        "cache_write_per_mtok": 6.00,
    },
    # TODO(david): claude-sonnet-5 is now the default ADVISOR_MODEL. Fill in the
    # verified per-million-token rates below and uncomment. Until this entry
    # exists, _build_usage_dict logs a warning and stores cost=None (no crash) —
    # trade decisions run fine on Sonnet 5, but cost tracking stays blank.
    # "claude-sonnet-5": {
    #     "input_per_mtok": 0.00,
    #     "output_per_mtok": 0.00,
    #     "cache_read_per_mtok": 0.00,
    #     "cache_write_per_mtok": 0.00,
    # },
}


def get_pricing(model_version: str) -> dict:
    """Return pricing dict for a model.

    Raises:
        KeyError: if *model_version* is not in CLAUDE_PRICING.
    """
    return CLAUDE_PRICING[model_version]
