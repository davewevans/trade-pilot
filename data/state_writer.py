"""Writes bot state to JSON snapshot files on disk for the FastAPI dashboard.

Every write is atomic: data is written to a temporary file in the same
directory and then renamed over the target, so readers never see partial
content.
"""

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _dry_run_active() -> bool:
    """Check DRY_RUN via settings, falling back to env var."""
    try:
        from config import settings
        return bool(getattr(settings, "DRY_RUN", False))
    except Exception:
        return os.getenv("DRY_RUN", "false").lower() == "true"


def _tag_strategy_type(
    symbol: str,
    wheel_symbols: set[str],
    spread_leg_symbols: set[str],
) -> str:
    """Best-effort attribution of an OCC option symbol to its strategy.

    - Symbols that appear in the SpreadTracker's open spread legs → 'spread'
    - Symbols whose extracted underlying matches a watchlist wheel ticker
      → 'wheel'
    - Otherwise → 'unknown'
    """
    if not symbol:
        return "unknown"
    sym_upper = symbol.upper()
    if sym_upper in spread_leg_symbols:
        return "spread"
    # Extract the underlying root from the OCC symbol — leading alpha chars
    # before the YYMMDD expiration. Strip any trailing P/C just in case.
    root = ""
    for ch in sym_upper:
        if ch.isalpha():
            root += ch
        else:
            break
    if root in wheel_symbols:
        return "wheel"
    return "unknown"


def get_snapshot_dir() -> Path:
    """Return the snapshot directory, preferring ``settings.SNAPSHOTS_DIR``."""
    try:
        from config import settings
        return settings.SNAPSHOTS_DIR
    except Exception:
        return Path("data/snapshots")


class StateWriter:
    """Writes structured JSON snapshots consumed by the FastAPI dashboard."""

    def __init__(self, snapshot_dir: Path | None = None) -> None:
        self.dir = snapshot_dir or get_snapshot_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    # ── internal helpers ────────────────────────────────────

    def _atomic_write(self, path: Path, data: str) -> None:
        """Write *data* to *path* atomically via temp-file + rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".snap_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            # On Windows, os.replace is atomic within the same volume.
            os.replace(tmp, str(path))
        except BaseException:
            # Clean up the temp file if the rename failed.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _now_iso(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _safe_float(value, default=None) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # ── public methods ──────────────────────────────────────

    def write_portfolio_snapshot(
        self,
        account_data: dict,
        positions: list[dict],
        wheel_states: dict[str, str],
        open_spreads: list[dict] | None = None,
        wheel_symbols: list[str] | None = None,
        spread_leg_symbols: set[str] | None = None,
    ) -> None:
        """Write ``snapshots/portfolio.json``.

        ``wheel_symbols`` and ``spread_leg_symbols`` are used to tag each
        position with a ``strategy_type`` (Alpaca itself doesn't know which
        strategy owns a position). When neither is provided, positions
        whose dict already has a ``strategy_type`` keep it; otherwise
        the field is set to ``"unknown"``.
        """
        try:
            equity = self._safe_float(account_data.get("portfolio_value"), 0)
            buying_power = self._safe_float(account_data.get("buying_power"), 0)
            bp_used_pct = (
                round((1 - buying_power / equity) * 100, 2)
                if equity
                else 0.0
            )

            wheel_set = (
                {w.upper() for w in wheel_symbols} if wheel_symbols else set()
            )
            spread_set = (
                {s.upper() for s in spread_leg_symbols}
                if spread_leg_symbols else set()
            )

            pos_list = []
            for p in positions:
                symbol = p.get("symbol", "") or ""
                tagged = _tag_strategy_type(symbol, wheel_set, spread_set)
                # Respect any pre-existing tag on the position dict, but
                # only if it's a non-empty string.
                existing = p.get("strategy_type") or ""
                strategy_type = existing if existing else tagged
                pos_list.append({
                    "underlying": p.get("underlying", p.get("root_symbol", "")),
                    "strategy_type": strategy_type,
                    "symbol": symbol,
                    "strike": self._safe_float(p.get("strike_price")),
                    "expiration": p.get("expiration_date"),
                    "dte": p.get("dte"),
                    "quantity": p.get("qty"),
                    "entry_credit": self._safe_float(p.get("avg_entry_price")),
                    "current_value": self._safe_float(p.get("current_price", p.get("market_value"))),
                    "unrealized_pnl": self._safe_float(p.get("unrealized_pl")),
                    "delta": self._safe_float(p.get("delta")),
                    "theta": self._safe_float(p.get("theta")),
                })

            last_equity = self._safe_float(account_data.get("last_equity"), 0)
            today_pnl = round(equity - last_equity, 2) if last_equity else 0.0
            today_pnl_pct = (
                round((today_pnl / last_equity) * 100, 3) if last_equity else 0.0
            )

            snapshot = {
                "timestamp": self._now_iso(),
                "account": {
                    "total_equity": equity,
                    "last_equity": last_equity,
                    "buying_power": buying_power,
                    "buying_power_used_pct": bp_used_pct,
                    "today_pnl": today_pnl,
                    "today_pnl_pct": today_pnl_pct,
                },
                "positions": pos_list,
                "wheel_states": wheel_states,
                "open_spreads": open_spreads or [],
            }

            path = self.dir / "portfolio.json"
            self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))
            logger.debug("Wrote portfolio snapshot: %s", path)
        except Exception:
            logger.exception("Failed to write portfolio snapshot")

    def write_equity_history(self, history: dict) -> None:
        """Write snapshots/equity_history.json.

        Transforms Alpaca's /v2/account/portfolio/history response into a
        clean list of {date, equity, pnl, pnl_pct} dicts for the chart.
        Null equity values (non-trading days) are skipped.
        """
        try:
            timestamps = history.get("timestamp") or []
            equities   = history.get("equity") or []
            pnl        = history.get("profit_loss") or []
            pnl_pct    = history.get("profit_loss_pct") or []

            if not timestamps:
                logger.debug("No equity history data to write")
                return

            points = []
            for i, ts in enumerate(timestamps):
                eq = equities[i] if i < len(equities) else None
                if eq is None:
                    continue
                points.append({
                    "date":    datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                    "equity":  round(float(eq), 2),
                    "pnl":     round(float(pnl[i]), 2)
                               if i < len(pnl) and pnl[i] is not None else 0.0,
                    "pnl_pct": round(float(pnl_pct[i]) * 100, 3)
                               if i < len(pnl_pct) and pnl_pct[i] is not None else 0.0,
                })

            snapshot = {
                "timestamp":  self._now_iso(),
                "base_value": self._safe_float(history.get("base_value"), 0),
                "timeframe":  history.get("timeframe", "1D"),
                "points":     points,
            }

            path = self.dir / "equity_history.json"
            self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))
            logger.debug("Wrote equity history: %d points", len(points))
        except Exception:
            logger.exception("Failed to write equity history snapshot")

    def write_account_snapshot(
        self,
        account_name: str,
        account_data: dict,
        positions: list[dict],
    ) -> None:
        """Write a per-account portfolio snapshot.

        Writes to snapshots/portfolio_{account_name}.json.
        Used by the startup snapshot job and portfolio_refresh.

        Args:
            account_name: One of "wheel", "iron_condor", "spreads".
            account_data: Raw account dict from broker.get_account().
            positions:    Raw positions list from broker.get_positions().
        """
        try:
            equity = self._safe_float(account_data.get("portfolio_value"), 0)
            buying_power = self._safe_float(account_data.get("buying_power"), 0)
            last_equity = self._safe_float(account_data.get("last_equity"), 0)
            today_pnl = round(equity - last_equity, 2) if last_equity else 0.0
            today_pnl_pct = (
                round((today_pnl / last_equity) * 100, 3) if last_equity else 0.0
            )
            bp_used_pct = (
                round((1 - buying_power / equity) * 100, 2) if equity else 0.0
            )

            snapshot = {
                "timestamp":    self._now_iso(),
                "account_name": account_name,
                "account": {
                    "total_equity":          equity,
                    "last_equity":           last_equity,
                    "buying_power":          buying_power,
                    "buying_power_used_pct": bp_used_pct,
                    "today_pnl":             today_pnl,
                    "today_pnl_pct":         today_pnl_pct,
                },
                "positions": [
                    {
                        "symbol":        p.get("symbol", ""),
                        "underlying":    p.get("underlying", p.get("root_symbol", "")),
                        "strategy_type": p.get("strategy_type", ""),
                        "strike":        self._safe_float(p.get("strike_price")),
                        "expiration":    p.get("expiration_date"),
                        "dte":           p.get("dte"),
                        "quantity":      p.get("qty"),
                        "entry_credit":  self._safe_float(p.get("avg_entry_price")),
                        "current_value": self._safe_float(
                            p.get("current_price", p.get("market_value"))
                        ),
                        "unrealized_pnl": self._safe_float(p.get("unrealized_pl")),
                        "delta":          self._safe_float(p.get("delta")),
                        "theta":          self._safe_float(p.get("theta")),
                    }
                    for p in positions
                ],
            }

            filename = f"portfolio_{account_name}.json"
            path = self.dir / filename
            self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))
            logger.debug("Wrote account snapshot: %s", path)
        except Exception:
            logger.exception(
                "Failed to write account snapshot for %s", account_name
            )

    def write_context_snapshot(self, context_dict: dict) -> None:
        """Write ``snapshots/context.json``."""
        try:
            snapshot = {
                "timestamp": self._now_iso(),
                **context_dict,
            }
            path = self.dir / "context.json"
            self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))
            logger.debug("Wrote context snapshot: %s", path)
        except Exception:
            logger.exception("Failed to write context snapshot")

    def write_decision(
        self,
        decision_dict: dict,
        reasoning: str,
        action_taken: bool,
        underlying: str,
        guardrail_rejection: str | None = None,
        pre_check_would_have: str | None = None,
    ) -> None:
        """Append one record to ``snapshots/decisions.jsonl``.

        ``pre_check_would_have`` is the action the rules-only pre-check
        would have taken (e.g. "OPEN") before Claude was consulted.
        Together with the real ``action`` this lets us measure how often
        Claude overrides the deterministic pre-check (S11 instrumentation).
        """
        try:
            claude_action = decision_dict.get("action", "")
            record = {
                "timestamp": self._now_iso(),
                "underlying": underlying,
                "action": claude_action,
                "action_taken": action_taken,
                "reasoning": reasoning,
                "key_inputs": {
                    "iv_rank": decision_dict.get("iv_rank"),
                    "dte": decision_dict.get("dte"),
                    "delta": self._safe_float(decision_dict.get("delta")),
                    "premium": self._safe_float(decision_dict.get("limit_price")),
                    "skip_reason": decision_dict.get("skip_reason"),
                },
                "guardrail_rejection": guardrail_rejection,
                "pre_check_result": pre_check_would_have,
                "claude_override": (
                    pre_check_would_have is not None
                    and pre_check_would_have != claude_action
                ),
            }

            path = self.dir / "decisions.jsonl"
            # Append is not atomic per-file, but each line is written in
            # a single call so readers get complete JSON lines.
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            logger.debug("Appended decision for %s: %s", underlying, record["action"])
        except Exception:
            logger.exception("Failed to write decision for %s", underlying)

    def write_option_events(self, events: list[dict]) -> None:
        """Write ``snapshots/option_events.json`` for the dashboard.

        Overwrites with the latest batch (typically overnight events
        polled at pre-market). A no-op for an empty list.
        """
        if not events:
            return
        try:
            payload = {
                "timestamp": self._now_iso(),
                "count": len(events),
                "events": events,
            }
            path = self.dir / "option_events.json"
            self._atomic_write(path, json.dumps(payload, indent=2, default=str))
            logger.debug("Wrote %d option events to %s", len(events), path)
        except Exception:
            logger.exception("Failed to write option events")

    def write_circuit_breaker_status(self, status_dict: dict) -> None:
        """Write ``snapshots/circuit_breakers.json``."""
        try:
            snapshot = {
                "timestamp": self._now_iso(),
                "daily_pnl": self._safe_float(status_dict.get("daily_pnl")),
                "daily_pnl_pct": self._safe_float(status_dict.get("daily_pnl_pct")),
                "weekly_pnl": self._safe_float(status_dict.get("weekly_pnl")),
                "weekly_pnl_pct": self._safe_float(status_dict.get("weekly_pnl_pct")),
                "peak_equity": self._safe_float(status_dict.get("peak_equity")),
                "current_drawdown_pct": self._safe_float(status_dict.get("current_drawdown_pct")),
                "status": status_dict.get("status", "GREEN"),
                "active_rules": status_dict.get("active_rules", []),
                "halted": bool(status_dict.get("halted", False)),
                "dry_run": _dry_run_active(),
            }

            path = self.dir / "circuit_breakers.json"
            self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))
            logger.debug("Wrote circuit breaker status: %s", path)
        except Exception:
            logger.exception("Failed to write circuit breaker status")
