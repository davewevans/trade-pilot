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

# Spread lifecycle statuses.
#   PENDING_OPEN  → entry order placed, not yet confirmed filled
#   OPEN          → entry order filled, position live
#   PENDING_CLOSE → close order placed, not yet confirmed filled
#   CLOSED        → close order filled (terminal)
#   CANCELED      → entry order canceled/rejected before fill (terminal)
STATUS_PENDING_OPEN = "pending_open"
STATUS_OPEN = "open"
STATUS_PENDING_CLOSE = "pending_close"
STATUS_CLOSED = "closed"
STATUS_CANCELED = "canceled"

# Non-terminal statuses — i.e. anything that should block a duplicate
# entry or be examined by reconciliation.
ACTIVE_STATUSES = frozenset({
    STATUS_PENDING_OPEN, STATUS_OPEN, STATUS_PENDING_CLOSE,
})


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
        from utils.fileio import atomic_json_write
        atomic_json_write(self._path, self._spreads)

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
        entry_order_id: str | None = None,
        cb_status_at_entry: str | None = None,
        original_dte: int | None = None,
    ) -> str:
        """Register a newly submitted spread. Starts in PENDING_OPEN.

        Transitions to OPEN only after reconciliation confirms the entry
        order filled. Returns a ``spread_id``.
        """
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
            "status": STATUS_PENDING_OPEN,
            "entry_order_id": entry_order_id,
            "close_order_id": None,
            "cb_status_at_entry": cb_status_at_entry,
            "original_dte": original_dte,
            "exit_credit": None,
            "pnl": None,
            "closed_at": None,
            "registered_at": datetime.now().isoformat(timespec="seconds"),
            "max_adverse_value": None,
            "max_adverse_timestamp": None,
        }
        self._spreads.append(spread)
        self._save()
        logger.info(
            "Registered spread %s: %s %s (%d legs) status=%s order=%s",
            spread_id, strategy_type, underlying, len(legs),
            STATUS_PENDING_OPEN, entry_order_id,
        )
        return spread_id

    def _find(self, spread_id: str) -> dict | None:
        for s in self._spreads:
            if s["spread_id"] == spread_id:
                return s
        return None

    def mark_open(self, spread_id: str, fill_price: float | None = None) -> None:
        """Transition PENDING_OPEN → OPEN after entry order confirmed filled."""
        s = self._find(spread_id)
        if not s:
            logger.warning("mark_open: spread %s not found", spread_id)
            return
        if s.get("status") != STATUS_PENDING_OPEN:
            logger.warning(
                "mark_open: spread %s in unexpected status %s",
                spread_id, s.get("status"),
            )
        s["status"] = STATUS_OPEN
        if fill_price is not None:
            # Fill price for a credit spread is reported as a negative
            # number by Alpaca; store the absolute value as the credit.
            s["entry_credit"] = abs(float(fill_price))
        s["opened_at"] = datetime.now().isoformat(timespec="seconds")
        self._save()
        logger.info("Spread %s confirmed OPEN (fill=%s)", spread_id, fill_price)

    def cancel_pending_open(self, spread_id: str, reason: str = "") -> None:
        """Mark a PENDING_OPEN spread as CANCELED (entry never filled)."""
        s = self._find(spread_id)
        if not s:
            logger.warning("cancel_pending_open: spread %s not found", spread_id)
            return
        s["status"] = STATUS_CANCELED
        s["closed_at"] = datetime.now().isoformat(timespec="seconds")
        s["cancel_reason"] = reason
        self._save()
        logger.info(
            "Spread %s entry canceled/rejected — status=%s reason=%s",
            spread_id, STATUS_CANCELED, reason,
        )

    def mark_pending_close(self, spread_id: str, close_order_id: str | None) -> None:
        """Transition OPEN → PENDING_CLOSE after a close order is submitted."""
        s = self._find(spread_id)
        if not s:
            logger.warning("mark_pending_close: spread %s not found", spread_id)
            return
        if s.get("status") != STATUS_OPEN:
            logger.warning(
                "mark_pending_close: spread %s in unexpected status %s",
                spread_id, s.get("status"),
            )
        s["status"] = STATUS_PENDING_CLOSE
        s["close_order_id"] = close_order_id
        self._save()
        logger.info(
            "Spread %s → PENDING_CLOSE order=%s", spread_id, close_order_id,
        )

    def set_recovery_close_order_id(self, spread_id: str, order_id: str) -> None:
        """Tag a spread with a recovery close order ID for idempotency.

        Called by the startup reconciler when it places a sell-to-close order
        for an orphaned long leg.  If the reconciler restarts before the order
        fills, the tagged ID is detected on the next run so the order is not
        double-placed.
        """
        s = self._find(spread_id)
        if not s:
            logger.warning("set_recovery_close_order_id: spread %s not found", spread_id)
            return
        s["recovery_close_order_id"] = order_id
        self._save()
        logger.info(
            "Spread %s tagged with recovery_close_order_id=%s", spread_id, order_id
        )

    def revert_to_open(self, spread_id: str, reason: str = "") -> None:
        """Roll PENDING_CLOSE back to OPEN if the close order was canceled."""
        s = self._find(spread_id)
        if not s:
            logger.warning("revert_to_open: spread %s not found", spread_id)
            return
        s["status"] = STATUS_OPEN
        s["close_order_id"] = None
        self._save()
        logger.info(
            "Spread %s close canceled/rejected — reverted to OPEN reason=%s",
            spread_id, reason,
        )

    def get_open_spreads(
        self,
        underlying: str | None = None,
        strategy_type: str | None = None,
    ) -> list[dict]:
        """Return spreads strictly in OPEN status (entry filled, no close pending)."""
        results = [s for s in self._spreads if s.get("status") == STATUS_OPEN]
        if underlying:
            ul = underlying.upper()
            results = [s for s in results if s["underlying"].upper() == ul]
        if strategy_type:
            results = [s for s in results if s["strategy_type"] == strategy_type]
        return results

    def get_active_spreads(
        self,
        underlying: str | None = None,
        strategy_type: str | None = None,
    ) -> list[dict]:
        """Return all non-terminal spreads (PENDING_OPEN, OPEN, PENDING_CLOSE).

        Use this for duplicate-entry detection so an in-flight entry
        cannot be double-submitted while it is still PENDING_OPEN.
        """
        results = [s for s in self._spreads if s.get("status") in ACTIVE_STATUSES]
        if underlying:
            ul = underlying.upper()
            results = [s for s in results if s["underlying"].upper() == ul]
        if strategy_type:
            results = [s for s in results if s["strategy_type"] == strategy_type]
        return results

    def get_spreads_by_status(self, status: str) -> list[dict]:
        """Return all spreads currently in *status*."""
        return [s for s in self._spreads if s.get("status") == status]

    def close_spread(
        self,
        spread_id: str,
        exit_credit: float | None = None,
    ) -> None:
        """Mark a spread as terminally CLOSED. Calculate P&L if *exit_credit* given.

        Normally called by reconciliation after PENDING_CLOSE → CLOSED is
        confirmed by the broker. Safe to call from any non-terminal state.
        """
        for s in self._spreads:
            if s["spread_id"] == spread_id:
                s["status"] = STATUS_CLOSED
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
            # Treat OPEN and PENDING_CLOSE as live for leg lookup (legs
            # are filled in both states); skip PENDING_OPEN since legs
            # may not yet exist on the broker side.
            if s.get("status") not in (STATUS_OPEN, STATUS_PENDING_CLOSE):
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
            # Only reconcile confirmed-OPEN spreads. PENDING_OPEN legs
            # may not exist on Alpaca yet, and PENDING_CLOSE is being
            # handled by the order-status reconciler instead.
            if s.get("status") != STATUS_OPEN:
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

    def prune_old_spreads(self, max_age_days: int = 30) -> int:
        """Remove terminal (CLOSED/CANCELED) spreads older than *max_age_days*."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        before = len(self._spreads)
        self._spreads = [
            s for s in self._spreads
            if s.get("status") not in (STATUS_CLOSED, STATUS_CANCELED)
            or (s.get("closed_at") or s.get("registered_at", "")) > cutoff
        ]
        pruned = before - len(self._spreads)
        if pruned > 0:
            self._save()
            logger.info("Pruned %d old spreads (older than %d days)", pruned, max_age_days)
        return pruned

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
            if s.get("status") not in (STATUS_OPEN, STATUS_PENDING_CLOSE):
                continue
            for leg in s.get("legs", []):
                sym = leg.get("symbol")
                if sym:
                    symbols.add(sym.upper())
        return symbols

    def update_mae(self, spread_id: str, current_value: float, timestamp: str) -> None:
        """Update max-adverse-excursion for an open spread.

        current_value is the combined unrealized P&L across all legs (already
        signed by Alpaca — positive = profit, negative = loss). Records the
        worst (most negative) value observed. Backward-compat: spreads loaded
        from disk without these keys treat missing as None.
        """
        s = self._find(spread_id)
        if not s:
            logger.warning("update_mae: spread %s not found", spread_id)
            return
        stored = s.get("max_adverse_value")
        if stored is None or current_value < stored:
            s["max_adverse_value"] = current_value
            s["max_adverse_timestamp"] = timestamp
            self._save()
            logger.debug(
                "MAE updated spread=%s value=%.4f ts=%s",
                spread_id, current_value, timestamp,
            )
