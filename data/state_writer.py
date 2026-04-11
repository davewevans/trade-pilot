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
    ) -> None:
        """Write ``snapshots/portfolio.json``."""
        try:
            equity = self._safe_float(account_data.get("portfolio_value"), 0)
            buying_power = self._safe_float(account_data.get("buying_power"), 0)
            bp_used_pct = (
                round((1 - buying_power / equity) * 100, 2)
                if equity
                else 0.0
            )

            pos_list = []
            for p in positions:
                pos_list.append({
                    "underlying": p.get("underlying", p.get("root_symbol", "")),
                    "strategy_type": p.get("strategy_type", ""),
                    "symbol": p.get("symbol", ""),
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

            snapshot = {
                "timestamp": self._now_iso(),
                "account": {
                    "total_equity": equity,
                    "buying_power": buying_power,
                    "buying_power_used_pct": bp_used_pct,
                },
                "positions": pos_list,
                "wheel_states": wheel_states,
            }

            path = self.dir / "portfolio.json"
            self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))
            logger.debug("Wrote portfolio snapshot: %s", path)
        except Exception:
            logger.exception("Failed to write portfolio snapshot")

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
    ) -> None:
        """Append one record to ``snapshots/decisions.jsonl``."""
        try:
            record = {
                "timestamp": self._now_iso(),
                "underlying": underlying,
                "action": decision_dict.get("action", ""),
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
            }

            path = self.dir / "circuit_breakers.json"
            self._atomic_write(path, json.dumps(snapshot, indent=2, default=str))
            logger.debug("Wrote circuit breaker status: %s", path)
        except Exception:
            logger.exception("Failed to write circuit breaker status")
