"""Portfolio-level circuit breakers that track cumulative P&L and can halt trading.

This is a separate layer from per-trade guardrails. It monitors daily, weekly,
and drawdown P&L and progressively reduces or halts new position entry.

Thresholds are read from ``config.py`` (overridable via env vars).
State is persisted to ``data/snapshots/circuit_breaker_state.json``.
A ``data/HALTED.lock`` file acts as a hard kill switch that requires
manual deletion to resume trading.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Accounts whose portfolio_value is aggregated to compute total portfolio equity
# for the circuit breaker. Matches the keys used in startup_snapshot.ACCOUNT_BROKER_MAP.
PORTFOLIO_ACCOUNTS = ("wheel", "iron_condor", "spreads")


@dataclass
class CircuitBreakerStatus:
    """Snapshot returned by :meth:`CircuitBreaker.update`."""

    status: str = "GREEN"  # GREEN / YELLOW / RED
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    weekly_pnl: float = 0.0
    weekly_pnl_pct: float = 0.0
    drawdown_pct: float = 0.0
    active_rules: list[str] = field(default_factory=list)
    halted: bool = False


class CircuitBreaker:
    """Tracks cumulative P&L and enforces portfolio-level risk limits."""

    def __init__(
        self,
        data_dir: Path | None = None,
        snapshots_dir: Path | None = None,
        daily_loss_halt_pct: float | None = None,
        daily_loss_reduce_pct: float | None = None,
        weekly_loss_halt_pct: float | None = None,
        drawdown_halt_pct: float | None = None,
        drawdown_lock_pct: float | None = None,
    ) -> None:
        # Resolve directories — fall back to settings when not supplied.
        if data_dir is not None:
            self._data_dir = data_dir
        else:
            from config import settings
            self._data_dir = settings.DATA_DIR

        if snapshots_dir is not None:
            self._snapshots_dir = snapshots_dir
        else:
            self._snapshots_dir = self._data_dir / "snapshots"

        self._snapshots_dir.mkdir(parents=True, exist_ok=True)

        self._state_path = self._snapshots_dir / "circuit_breaker_state.json"
        self._lock_path = self._data_dir / "HALTED.lock"

        # Thresholds — prefer explicit args, then settings, then defaults.
        def _thresh(explicit, attr_name, default):
            if explicit is not None:
                return explicit
            try:
                from config import settings as _s
                return getattr(_s, attr_name, default)
            except Exception:
                return default

        self.daily_loss_halt_pct = _thresh(daily_loss_halt_pct, "DAILY_LOSS_HALT_PCT", 3.0)
        self.daily_loss_reduce_pct = _thresh(daily_loss_reduce_pct, "DAILY_LOSS_REDUCE_PCT", 1.5)
        self.weekly_loss_halt_pct = _thresh(weekly_loss_halt_pct, "WEEKLY_LOSS_HALT_PCT", 5.0)
        self.drawdown_halt_pct = _thresh(drawdown_halt_pct, "DRAWDOWN_HALT_PCT", 10.0)
        self.drawdown_lock_pct = _thresh(drawdown_lock_pct, "DRAWDOWN_LOCK_PCT", 15.0)

        # Internal state
        self.peak_equity: float = 0.0
        self.day_start_equity: float = 0.0
        self.week_start_equity: float = 0.0
        self.current_equity: float = 0.0
        self.last_updated: str = ""
        self.halted_reason: str = ""

        # The latest status computed by update()
        self._status = CircuitBreakerStatus()

        self._load_state()

    # ── persistence ─────────────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self.peak_equity = float(data.get("peak_equity", 0))
            self.day_start_equity = float(data.get("day_start_equity", 0))
            self.week_start_equity = float(data.get("week_start_equity", 0))
            self.current_equity = float(data.get("current_equity", 0))
            self.last_updated = data.get("last_updated", "")
            self.halted_reason = data.get("halted_reason", "")
        except Exception:
            logger.exception("Failed to load circuit breaker state — starting fresh")

    def _save_state(self) -> None:
        data = {
            "peak_equity": self.peak_equity,
            "day_start_equity": self.day_start_equity,
            "week_start_equity": self.week_start_equity,
            "current_equity": self.current_equity,
            "last_updated": self.last_updated,
            "halted_reason": self.halted_reason,
        }
        from utils.fileio import atomic_json_write
        atomic_json_write(self._state_path, data)

    # ── public API ──────────────────────────────────────────

    def update(self, current_equity: float) -> CircuitBreakerStatus:
        """Recalculate all P&L metrics and evaluate thresholds.

        If ``DRAWDOWN_LOCK_PCT`` is breached, writes ``data/HALTED.lock``.
        """
        self.current_equity = current_equity
        self.last_updated = datetime.now().isoformat(timespec="seconds")

        # Guard against stale/corrupt peak_equity. If the peak is more than
        # 50% above current equity and we have no open positions, the peak
        # is almost certainly stale from a previous session or account reset.
        # Re-seed it rather than triggering a false drawdown lock.
        if self.peak_equity > 0 and current_equity > 0:
            apparent_drawdown = (self.peak_equity - current_equity) / self.peak_equity
            if apparent_drawdown > 0.50:
                logger.warning(
                    "CIRCUIT BREAKER: peak_equity ($%.2f) is >50%% above "
                    "current_equity ($%.2f) — likely stale. Re-seeding peak.",
                    self.peak_equity, current_equity,
                )
                self.peak_equity = current_equity

        # Rolling peak
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        # Seed start-of-period equities on first run
        if self.day_start_equity == 0:
            self.day_start_equity = current_equity
        if self.week_start_equity == 0:
            self.week_start_equity = current_equity

        # P&L calculations
        daily_pnl = current_equity - self.day_start_equity
        weekly_pnl = current_equity - self.week_start_equity

        daily_pnl_pct = (
            (daily_pnl / self.day_start_equity) * 100
            if self.day_start_equity
            else 0.0
        )
        weekly_pnl_pct = (
            (weekly_pnl / self.week_start_equity) * 100
            if self.week_start_equity
            else 0.0
        )
        drawdown_pct = (
            ((self.peak_equity - current_equity) / self.peak_equity) * 100
            if self.peak_equity
            else 0.0
        )

        # In DRY_RUN mode, calculate and log metrics for observability but
        # never trigger YELLOW/RED/HALTED. The bot isn't placing trades, so
        # there's no risk to manage — and blocking decisions defeats the
        # purpose of a dry run.
        try:
            from config import settings as _settings
            _dry_run = bool(getattr(_settings, "DRY_RUN", False))
        except Exception:
            _dry_run = False

        if _dry_run:
            self._status = CircuitBreakerStatus(
                status="GREEN",
                daily_pnl=round(daily_pnl, 2),
                daily_pnl_pct=round(daily_pnl_pct, 2),
                weekly_pnl=round(weekly_pnl, 2),
                weekly_pnl_pct=round(weekly_pnl_pct, 2),
                drawdown_pct=round(drawdown_pct, 2),
                active_rules=[],
                halted=False,
            )
            self._save_state()
            return self._status

        # Evaluate thresholds
        active_rules: list[str] = []

        if abs(daily_pnl_pct) >= self.daily_loss_halt_pct and daily_pnl_pct < 0:
            active_rules.append("daily_loss_halt")
            logger.warning(
                "CIRCUIT BREAKER: Daily loss %.2f%% breaches halt threshold (%.1f%%)",
                daily_pnl_pct, self.daily_loss_halt_pct,
            )
        elif abs(daily_pnl_pct) >= self.daily_loss_reduce_pct and daily_pnl_pct < 0:
            active_rules.append("daily_loss_reduce")
            logger.warning(
                "CIRCUIT BREAKER: Daily loss %.2f%% breaches reduce threshold (%.1f%%)",
                daily_pnl_pct, self.daily_loss_reduce_pct,
            )

        if abs(weekly_pnl_pct) >= self.weekly_loss_halt_pct and weekly_pnl_pct < 0:
            active_rules.append("weekly_loss_halt")
            logger.warning(
                "CIRCUIT BREAKER: Weekly loss %.2f%% breaches halt threshold (%.1f%%)",
                weekly_pnl_pct, self.weekly_loss_halt_pct,
            )

        if drawdown_pct >= self.drawdown_lock_pct:
            active_rules.append("drawdown_lock")
            self._write_halt_lock(
                f"Drawdown {drawdown_pct:.2f}% breaches lock threshold "
                f"({self.drawdown_lock_pct}%). Peak: ${self.peak_equity:,.2f}, "
                f"Current: ${current_equity:,.2f}"
            )
        elif drawdown_pct >= self.drawdown_halt_pct:
            active_rules.append("drawdown_halt")
            logger.warning(
                "CIRCUIT BREAKER: Drawdown %.2f%% breaches halt threshold (%.1f%%)",
                drawdown_pct, self.drawdown_halt_pct,
            )

        # Determine overall status
        halted = self.is_halted()
        halt_rules = {"daily_loss_halt", "weekly_loss_halt", "drawdown_halt", "drawdown_lock"}
        if halted or (set(active_rules) & halt_rules):
            status = "RED"
        elif active_rules:
            status = "YELLOW"
        else:
            status = "GREEN"

        self._status = CircuitBreakerStatus(
            status=status,
            daily_pnl=round(daily_pnl, 2),
            daily_pnl_pct=round(daily_pnl_pct, 2),
            weekly_pnl=round(weekly_pnl, 2),
            weekly_pnl_pct=round(weekly_pnl_pct, 2),
            drawdown_pct=round(drawdown_pct, 2),
            active_rules=active_rules,
            halted=halted,
        )

        # Fire notifications on state transitions (non-blocking, swallowed).
        prev_status = getattr(self, "_prev_status_str", None)
        if status != prev_status:
            try:
                from notifications import notify
                if status == "RED":
                    notify(
                        "critical",
                        "Circuit breaker RED",
                        f"Daily P&L: {round(daily_pnl_pct, 1):.1f}%, "
                        f"Drawdown: {round(drawdown_pct, 1):.1f}%",
                        tags=["circuit_breaker"],
                    )
                elif status == "YELLOW":
                    notify(
                        "warning",
                        "Circuit breaker YELLOW",
                        f"Daily P&L: {round(daily_pnl_pct, 1):.1f}%",
                        tags=["circuit_breaker"],
                    )
            except Exception:
                pass
        self._prev_status_str = status

        self._save_state()
        return self._status

    def is_halted(self) -> bool:
        """Return True if ``HALTED.lock`` exists (manual delete to resume).

        In DRY_RUN mode this always returns False — the lock file may still
        exist (and be written by update() if a threshold is crossed), but it
        does not gate decisions. That way the lock's presence when switching
        to live trading is a signal to investigate before going live.
        """
        try:
            from config import settings as _settings
            if getattr(_settings, "DRY_RUN", False):
                return False
        except Exception:
            pass
        if self._lock_path.exists():
            logger.warning(
                "TRADING HALTED — lock file exists at %s. "
                "Delete this file manually to resume trading.",
                self._lock_path,
            )
            return True
        return False

    def get_position_size_multiplier(self) -> float:
        """Return a sizing multiplier: 1.0 (normal), 0.5 (reduced), 0.0 (halted)."""
        if self.is_halted():
            return 0.0

        rules = set(self._status.active_rules)
        halt_rules = {"daily_loss_halt", "weekly_loss_halt", "drawdown_halt", "drawdown_lock"}
        if rules & halt_rules:
            return 0.0

        if "daily_loss_reduce" in rules:
            return 0.5

        return 1.0

    def reset_daily(self) -> None:
        """Reset day-start equity to current. Called at market open."""
        self.day_start_equity = self.current_equity
        logger.info(
            "Circuit breaker daily reset: day_start_equity=$%s",
            f"{self.day_start_equity:,.2f}",
        )
        self._save_state()

    def reset_weekly(self) -> None:
        """Reset week-start equity to current. Called on Monday market open."""
        self.week_start_equity = self.current_equity
        logger.info(
            "Circuit breaker weekly reset: week_start_equity=$%s",
            f"{self.week_start_equity:,.2f}",
        )
        self._save_state()

    # ── static helpers ───────────────────────────────────────

    @staticmethod
    def calculate_portfolio_equity(make_broker_fn) -> float | None:
        """Aggregate portfolio equity across all trading accounts.

        Returns sum of portfolio_value across all accounts in PORTFOLIO_ACCOUNTS.
        Returns None if ANY account call fails — callers must treat None as
        "do not update the circuit breaker this cycle."

        Intentionally strict: partial equity is worse than no equity. A partial
        value causes the CB to see artificially healthy balances.
        """
        # Mapping from logical account name to strategy key used by make_broker.
        # Mirrors startup_snapshot.ACCOUNT_BROKER_MAP.
        _ACCOUNT_TO_STRATEGY = {
            "wheel": "wheel",
            "iron_condor": "iron_condor",
            "spreads": "bull_put_spread",
        }
        total = 0.0
        for acct_name in PORTFOLIO_ACCOUNTS:
            strategy_key = _ACCOUNT_TO_STRATEGY[acct_name]
            try:
                broker = make_broker_fn(strategy_key)
            except Exception as exc:
                logger.error(
                    "calculate_portfolio_equity: failed to create broker for %s account: %s",
                    acct_name, exc,
                )
                return None
            try:
                acct = broker.get_account()
            except Exception as exc:
                logger.error(
                    "calculate_portfolio_equity: get_account() failed for %s account: %s",
                    acct_name, exc,
                )
                return None
            if "portfolio_value" not in acct:
                logger.error(
                    "calculate_portfolio_equity: portfolio_value missing from %s account response",
                    acct_name,
                )
                return None
            # portfolio_value == 0 is valid; do NOT treat as failure
            total += float(acct["portfolio_value"])
        return total

    # ── private helpers ─────────────────────────────────────

    def _write_halt_lock(self, reason: str) -> None:
        """Write the HALTED.lock file. This requires manual deletion to resume."""
        if self._lock_path.exists():
            return  # already locked
        self.halted_reason = reason
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.write_text(
            json.dumps({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "reason": reason,
                "equity_at_halt": self.current_equity,
                "peak_equity": self.peak_equity,
            }, indent=2),
            encoding="utf-8",
        )
        logger.critical(
            "TRADING HALTED — lock file written: %s. Reason: %s",
            self._lock_path, reason,
        )
        try:
            from notifications import notify
            notify(
                "critical",
                "Trading HALTED",
                f"Reason: {reason}. Manual deletion of HALTED.lock required to resume.",
                tags=["halt"],
            )
        except Exception:
            pass
