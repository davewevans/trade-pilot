"""TradeRecorder — thin facade over the repositories used during dual-write.

Wraps the cycle/decision/trade insert sequence so callers in jobs only
need a single ``recorder.record_decision(...)`` and
``recorder.record_trade(...)`` call. Designed to never raise out of the
hot path: a DB write failure is logged but the job continues running
on the existing JSON snapshots.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from database.repositories import (
    CycleRepository,
    DecisionRepository,
    TradeRepository,
)

logger = logging.getLogger(__name__)


# Actions that should auto-create a cycle if none is currently active.
# Both wheel (lowercase) and spread (uppercase) action vocabularies are
# normalized to uppercase before matching.
_CYCLE_OPENING_ACTIONS = frozenset({
    "SELL_PUT", "SELL_CALL", "ROLL", "CLOSE", "OPEN",
})
_NON_TRADE_ACTIONS = frozenset({"HOLD", "SKIP"})

_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def _parse_occ(symbol: str) -> Optional[dict]:
    """Parse an OCC option symbol into root/expiration/side/strike.

    Returns None if the symbol doesn't match the OCC format.
    """
    if not symbol:
        return None
    m = _OCC_RE.match(symbol.upper())
    if not m:
        return None
    root, yymmdd, side, strike8 = m.groups()
    return {
        "root": root,
        "expiration": f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}",
        "side": side,  # 'P' or 'C'
        "strike": int(strike8) / 1000,
    }


def _derive_trade_type(action: str, occ_side: str | None) -> str:
    """Map a wheel action + OCC side onto the trades.trade_type vocabulary."""
    a = (action or "").upper()
    if a == "SELL_PUT":
        return "SELL_PUT"
    if a == "SELL_CALL":
        return "SELL_CALL"
    if a == "ROLL":
        # Roll opens a new short position of the same side as the new contract.
        if occ_side == "P":
            return "SELL_PUT"
        if occ_side == "C":
            return "SELL_CALL"
        return "SELL_PUT"  # fallback
    if a == "CLOSE":
        if occ_side == "P":
            return "BUY_PUT"
        if occ_side == "C":
            return "BUY_CALL"
        return "BUY_PUT"  # fallback
    return a or "SELL_PUT"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TradeRecorder:
    """Dual-write helper: records decisions and trades into SQLite.

    Cycles are auto-created on the first actionable decision per
    (strategy_type, underlying) and left open. Cycle closure is a
    separate concern owned by a future reconciler / state-machine
    integration.
    """

    def __init__(self, conn):
        self._conn = conn
        self.cycles = CycleRepository(conn)
        self.decisions = DecisionRepository(conn)
        self.trades = TradeRepository(conn)

    # ── Decision recording ─────────────────────────────────

    def record_decision(
        self,
        *,
        strategy_type: str,
        underlying: str,
        action: str,
        wheel_state: str | None = None,
        reasoning: str | None = None,
        confidence: float | None = None,
        alpaca_order_id: str | None = None,
        prompt_version: str | None = None,
        context: dict | None = None,
        timestamp: str | None = None,
    ) -> tuple[int | None, str | None]:
        """Insert a decision row, auto-creating a cycle if appropriate.

        Returns ``(decision_id, cycle_id)``. Either may be None if the
        write failed (logged, never raised). For HOLD/SKIP, no cycle
        is created — ``cycle_id`` will be the existing active cycle if
        one is open, otherwise None.
        """
        try:
            action_upper = (action or "").upper()
            cycle = self.cycles.get_active(underlying, strategy_type)
            cycle_id = cycle["cycle_id"] if cycle else None

            if (
                action_upper in _CYCLE_OPENING_ACTIONS
                and action_upper not in _NON_TRADE_ACTIONS
                and cycle_id is None
            ):
                cycle_id = self.cycles.insert({
                    "strategy_type": strategy_type,
                    "underlying": underlying,
                })
                logger.info(
                    "Opened cycle %s for %s/%s (action=%s)",
                    cycle_id, strategy_type, underlying, action_upper,
                )

            decision_id = self.decisions.insert({
                "timestamp": timestamp or _now_iso(),
                "strategy_type": strategy_type,
                "underlying": underlying,
                "cycle_id": cycle_id,
                "wheel_state": wheel_state,
                "action": action_upper,
                "reasoning": str(reasoning) if reasoning is not None else None,
                "confidence": _coerce_confidence(confidence),
                "alpaca_order_id": alpaca_order_id,
                "prompt_version": prompt_version,
                "context": context,
            })
            return decision_id, cycle_id
        except Exception:
            logger.warning(
                "Failed to record decision in DB (strategy=%s underlying=%s "
                "action=%s)", strategy_type, underlying, action,
                exc_info=True,
            )
            return None, None

    # ── Trade recording ───────────────────────────────────

    def record_trade(
        self,
        *,
        cycle_id: str,
        decision_id: int | None,
        alpaca_order_id: str,
        underlying: str,
        strategy_type: str,
        action: str,
        symbol: str,
        limit_price: float,
        contracts: int = 1,
        delta_at_entry: float | None = None,
        iv_rank_at_entry: float | None = None,
    ) -> int | None:
        """Insert a trade row in 'pending' status. Returns trade id or None."""
        try:
            occ = _parse_occ(symbol) or {}
            trade_type = _derive_trade_type(action, occ.get("side"))
            strike = occ.get("strike")
            expiration = occ.get("expiration")
            dte = None
            if expiration:
                try:
                    exp_d = datetime.fromisoformat(expiration).date()
                    dte = (exp_d - datetime.now(timezone.utc).date()).days
                except (ValueError, TypeError):
                    dte = None

            return self.trades.insert({
                "cycle_id": cycle_id,
                "decision_id": decision_id,
                "alpaca_order_id": alpaca_order_id,
                "underlying": underlying,
                "strategy_type": strategy_type,
                "trade_type": trade_type,
                "symbol": symbol,
                "strike": strike,
                "expiration": expiration,
                "dte_at_entry": dte,
                "contracts": contracts,
                "limit_price": float(limit_price) if limit_price is not None else 0.0,
                "fill_status": "pending",
                "submitted_at": _now_iso(),
                "delta_at_entry": delta_at_entry,
                "iv_rank_at_entry": iv_rank_at_entry,
            })
        except Exception:
            logger.warning(
                "Failed to record trade in DB (order=%s symbol=%s)",
                alpaca_order_id, symbol, exc_info=True,
            )
            return None


    def resolve_trade(
        self,
        order_id: str,
        fill_status: str,
        fill_price: float | None = None,
    ) -> bool:
        """Update a pending trade with its resolved fill status.

        Writes to the ``trades`` table's ``fill_status``, ``fill_price``,
        and ``filled_at`` columns (the schema names ``filled_at`` what the
        recon spec called ``fill_timestamp`` — same field). ``filled_at``
        is stamped with the current UTC time only when ``fill_status``
        is ``'filled'`` (or ``'partially_filled'``); for terminal
        non-fill statuses (canceled/expired/rejected) it stays NULL.

        Returns ``True`` if a row was updated, ``False`` if no row
        matched ``order_id`` (warning logged) or if the write raised.
        Never raises.
        """
        try:
            filled_at: str | None = None
            if fill_status in ("filled", "partially_filled"):
                filled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

            cur = self._conn.execute(
                """
                UPDATE trades
                   SET fill_status = ?,
                       fill_price  = COALESCE(?, fill_price),
                       filled_at   = COALESCE(?, filled_at)
                 WHERE alpaca_order_id = ?
                """,
                (fill_status, fill_price, filled_at, order_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                logger.warning(
                    "resolve_trade: no row found for order_id=%s", order_id,
                )
                return False
            return True
        except Exception:
            logger.exception(
                "resolve_trade failed for order_id=%s", order_id,
            )
            return False


def _coerce_confidence(value) -> float | None:
    """Accept string ('high'/'medium'/'low') or numeric confidence."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        v = value.strip().lower()
        return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(v)
    return None
