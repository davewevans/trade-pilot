"""Tracks open spread positions as unified units.

Alpaca shows each leg as a separate position.  This class groups them
into spread units for management, P&L tracking, and reconciliation.

State is persisted to ``settings.SNAPSHOTS_DIR / "open_spreads.json"``.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class SpreadTracker:
    """Register, query, close, and reconcile multi-leg spread positions."""

    def __init__(self, state_path: Path | None = None) -> None:
        if state_path is not None:
            self._path = state_path
        else:
            from config import settings
            self._path = settings.SNAPSHOTS_DIR / "open_spreads.json"

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._spreads: list[dict] = []
        self._load()

    # ── persistence ─────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._spreads = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load spread tracker state — starting fresh")
            self._spreads = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._spreads, indent=2, default=str),
            encoding="utf-8",
        )

    # ── public API ──────────────────────────────────────────

    def register_spread(
        self,
        strategy_type: str,
        underlying: str,
        legs: list[dict],
        entry_credit: float,
        entry_date: str,
        expiration: str,
        max_loss: float,
        max_gain: float,
    ) -> str:
        """Register a newly filled spread. Returns a ``spread_id``."""
        spread_id = str(uuid.uuid4())
        spread = {
            "spread_id": spread_id,
            "strategy_type": strategy_type,
            "underlying": underlying,
            "legs": legs,
            "entry_credit": entry_credit,
            "entry_date": entry_date,
            "expiration": expiration,
            "max_loss": max_loss,
            "max_gain": max_gain,
            "status": "open",
            "exit_credit": None,
            "pnl": None,
            "closed_at": None,
            "registered_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._spreads.append(spread)
        self._save()
        logger.info(
            "Registered spread %s: %s %s (%d legs)",
            spread_id, strategy_type, underlying, len(legs),
        )
        return spread_id

    def get_open_spreads(
        self,
        underlying: str | None = None,
        strategy_type: str | None = None,
    ) -> list[dict]:
        """Return all open spreads, optionally filtered."""
        results = [s for s in self._spreads if s["status"] == "open"]
        if underlying:
            ul = underlying.upper()
            results = [s for s in results if s["underlying"].upper() == ul]
        if strategy_type:
            results = [s for s in results if s["strategy_type"] == strategy_type]
        return results

    def close_spread(
        self,
        spread_id: str,
        exit_credit: float | None = None,
    ) -> None:
        """Mark a spread as closed. Calculate P&L if *exit_credit* given."""
        for s in self._spreads:
            if s["spread_id"] == spread_id:
                s["status"] = "closed"
                s["closed_at"] = datetime.now().isoformat(timespec="seconds")
                if exit_credit is not None:
                    s["exit_credit"] = exit_credit
                    s["pnl"] = round(s["entry_credit"] - exit_credit, 4)
                self._save()
                logger.info(
                    "Closed spread %s: pnl=%s",
                    spread_id, s.get("pnl"),
                )
                return
        logger.warning("Spread %s not found for closing", spread_id)

    def get_spread_by_leg_symbol(self, symbol: str) -> dict | None:
        """Find which open spread a given leg symbol belongs to."""
        sym_upper = symbol.upper()
        for s in self._spreads:
            if s["status"] != "open":
                continue
            for leg in s.get("legs", []):
                if leg.get("symbol", "").upper() == sym_upper:
                    return s
        return None

    def reconcile_with_alpaca(self, alpaca_positions: list[dict]) -> list[str]:
        """Compare registered open spreads against Alpaca positions.

        Returns a list of ``spread_id`` values where **all** legs are
        missing from Alpaca (likely closed, expired, or assigned).
        Logs warnings for any discrepancies.
        """
        # Build a set of all position symbols from Alpaca
        alpaca_symbols = {
            p.get("symbol", "").upper() for p in alpaca_positions
        }

        closed_ids: list[str] = []
        for s in self._spreads:
            if s["status"] != "open":
                continue

            leg_symbols = [
                leg.get("symbol", "").upper() for leg in s.get("legs", [])
            ]
            present = [sym in alpaca_symbols for sym in leg_symbols]

            if not any(present):
                # All legs gone — spread is fully closed/expired
                closed_ids.append(s["spread_id"])
                logger.info(
                    "Spread %s (%s %s): all legs gone from Alpaca — marking closed",
                    s["spread_id"], s["strategy_type"], s["underlying"],
                )
            elif not all(present):
                # Partial — some legs remain, some don't
                missing = [
                    sym for sym, p in zip(leg_symbols, present) if not p
                ]
                logger.warning(
                    "Spread %s (%s %s): partial legs missing %s — "
                    "possible assignment or manual close",
                    s["spread_id"], s["strategy_type"], s["underlying"],
                    missing,
                )

        return closed_ids

    def to_snapshot(self) -> list[dict]:
        """Return all spreads in a format suitable for StateWriter."""
        return [dict(s) for s in self._spreads]

    def get_all_leg_symbols(self) -> set[str]:
        """Return the OCC symbols of every leg across all open spreads.

        Used by ``StateWriter.write_portfolio_snapshot`` to tag positions
        with their owning strategy.
        """
        symbols: set[str] = set()
        for s in self._spreads:
            if s.get("status") != "open":
                continue
            for leg in s.get("legs", []):
                sym = leg.get("symbol")
                if sym:
                    symbols.add(sym.upper())
        return symbols
