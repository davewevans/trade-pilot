"""TradeRecorder — thin facade over the repositories used during dual-write.

Wraps the cycle/decision/trade insert sequence so callers in jobs only
need a single ``recorder.record_decision(...)`` and
``recorder.record_trade(...)`` call. Designed to never raise out of the
hot path: a DB write failure is logged but the job continues running
on the existing JSON snapshots.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from database.repositories import (
    CycleRepository,
    DecisionRepository,
    TradeRepository,
    TokenUsageRepository,
)
from database.repositories.token_usage_repository import estimate_cost_usd

logger = logging.getLogger(__name__)


# Actions that should auto-create a cycle if none is currently active.
# Both wheel (lowercase) and spread (uppercase) action vocabularies are
# normalized to uppercase before matching.
_CYCLE_OPENING_ACTIONS = frozenset({
    "SELL_PUT", "SELL_CALL", "ROLL", "CLOSE", "OPEN",
})
_NON_TRADE_ACTIONS = frozenset({"HOLD", "SKIP"})

from utils.occ import parse_occ as _parse_occ_shared


def _parse_occ(symbol: str) -> Optional[dict]:
    """Parse an OCC option symbol into root/expiration/side/strike.

    Thin wrapper over utils.occ.parse_occ that preserves this module's
    historical output shape (``side`` key, ``expiration`` as ISO string).
    """
    p = _parse_occ_shared(symbol)
    if p is None:
        return None
    return {
        "root": p["root"],
        "expiration": p["expiration_str"],
        "side": p["option_type"],
        "strike": p["strike"],
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
        self.token_usage = TokenUsageRepository(conn)

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
        research_metadata: dict | None = None,
        skip_gate: str | None = None,
        skip_reason_code: str | None = None,
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
                # Preserve dict structure as JSON so the read path can rehydrate
                # to the 6-field reasoning object. Strings, None, etc. pass
                # through unchanged.
                "reasoning": (
                    json.dumps(reasoning, default=str)
                    if isinstance(reasoning, dict) else reasoning
                ),
                "confidence": _coerce_confidence(confidence),
                "alpaca_order_id": alpaca_order_id,
                "prompt_version": prompt_version,
                "context": context,
                "research_metadata": research_metadata,
                "skip_gate": skip_gate,
                "skip_reason_code": skip_reason_code,
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


    def record_token_usage(
        self,
        *,
        strategy_type: str,
        underlying: str | None,
        model: str,
        usage: dict,
        response_time_ms: int,
        decision_action: str | None = None,
    ) -> None:
        """Best-effort insert into token_usage. Never raises."""
        try:
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            cache_read = usage.get("cache_read_input_tokens", 0) or 0
            cache_creation = usage.get("cache_creation_input_tokens", 0) or 0

            cost = estimate_cost_usd(input_tokens, output_tokens, cache_read, cache_creation)

            self.token_usage.insert({
                "timestamp": _now_iso(),
                "strategy_type": strategy_type,
                "underlying": underlying,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "response_time_ms": response_time_ms,
                "estimated_cost_usd": cost,
                "decision_action": decision_action,
            })
        except Exception:
            logger.warning(
                "Failed to record token usage (strategy=%s underlying=%s)",
                strategy_type, underlying, exc_info=True,
            )

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


# ── Spread trade recording ──────────────────────────────────
# A spread is recorded as ONE trade row (not one per leg). The MLEG
# Alpaca order has a single order_id, which is what we store in
# alpaca_order_id (preserving the table's UNIQUE invariant). Per-leg
# detail (both leg symbols, both strikes, max_loss, max_gain) lives on
# the spread_tracker JSON, which is the existing source of truth for
# spread structure.
#
# Open and close are the SAME row: open inserts; close UPDATEs the
# same row by alpaca_order_id with closed_at / close_fill_price /
# realized_pnl / outcome.

# trade_type vocabulary used for the open row, keyed by strategy name.
# Convention: SELL_* = entry is a credit (cash in); BUY_* = entry is a
# debit (cash out). The api/server.py _trade_pnl helper extends its
# SELL/BUY sets to recognize these so cash-flow P&L stays correct.
_SPREAD_OPEN_TRADE_TYPE = {
    "bull_put_spread":     "SELL_BULL_PUT_SPREAD",
    "bear_call_spread":    "SELL_BEAR_CALL_SPREAD",
    "iron_condor":         "SELL_IRON_CONDOR",
    "iron_butterfly":      "SELL_IRON_BUTTERFLY",
    "long_call_vertical":  "BUY_LONG_CALL_VERTICAL",
    "calendar_spread":     "BUY_CALENDAR_SPREAD",
}
# Closing trade_type — opposite direction. Stored on the same row's
# `outcome` only? No: we keep the original trade_type on the row (it
# describes the position). The close direction is implicit: opposite
# of the open. We don't need a separate column.

# Picks the OCC symbol that's most representative of the spread for
# the row's ``symbol`` / ``strike`` / ``expiration`` columns. For
# credit spreads the short leg is the defining strike; for the debit
# spread it's the long leg.
def _primary_leg(strategy_type: str, legs: list[dict]) -> dict | None:
    if not legs:
        return None
    want_side = "buy" if strategy_type == "long_call_vertical" else "sell"
    for leg in legs:
        if (leg.get("side") or "").lower() == want_side:
            return leg
    return legs[0]


class SpreadTradeRecorder:
    """Spread-aware extension of TradeRecorder. Composed, not subclassed,
    so wheel callers see no change.

    All methods are best-effort: failures are logged but never raised.
    """

    def __init__(self, recorder: "TradeRecorder"):
        self._r = recorder
        self._conn = recorder._conn

    def record_spread_open(
        self,
        *,
        strategy_type: str,
        underlying: str,
        legs: list[dict],
        alpaca_order_id: str,
        fill_price: float | None,
        max_loss: float | None,
        max_gain: float | None,
        cb_status_at_entry: str | None,
        decision_id: int | None = None,
        limit_price: float | None = None,
    ) -> int | None:
        """Insert one trade row representing a confirmed-filled spread.

        Returns the new trade ``id`` or None on failure. Re-callable for
        the same alpaca_order_id is a no-op (UNIQUE constraint blocks
        the second insert; logged at warning).
        """
        try:
            primary = _primary_leg(strategy_type, legs) or {}
            occ = _parse_occ(primary.get("symbol", "")) or {}
            trade_type = _SPREAD_OPEN_TRADE_TYPE.get(
                strategy_type, "SELL_SPREAD",
            )

            # Find or open the cycle for (strategy_type, underlying).
            cycle = self._r.cycles.get_active(underlying, strategy_type)
            if cycle:
                cycle_id = cycle["cycle_id"]
            else:
                cycle_id = self._r.cycles.insert({
                    "strategy_type": strategy_type,
                    "underlying": underlying,
                })

            # premium_credit is signed: positive = credit received,
            # negative = debit paid. Mirrors broker convention so
            # downstream P&L can sum cash flows directly.
            signed_premium: float | None = None
            magnitude: float | None = None
            if fill_price is not None:
                try:
                    fp = float(fill_price)
                    magnitude = abs(fp)
                    is_debit = trade_type.startswith("BUY_")
                    signed_premium = (-magnitude if is_debit else magnitude) * 100
                except (TypeError, ValueError):
                    magnitude = None

            now = _now_iso()
            trade_id = self._r.trades.insert({
                "cycle_id":           cycle_id,
                "decision_id":        decision_id,
                "alpaca_order_id":    alpaca_order_id,
                "underlying":         underlying,
                "strategy_type":      strategy_type,
                "trade_type":         trade_type,
                "symbol":             primary.get("symbol", ""),
                "strike":             occ.get("strike"),
                "expiration":         occ.get("expiration"),
                "contracts":          1,
                "limit_price":        float(limit_price) if limit_price is not None else 0.0,
                "fill_price":         magnitude,
                "fill_status":        "filled",
                "submitted_at":       now,
                "filled_at":          now,
                "premium_credit":     signed_premium,
            })

            # cb_status_at_entry, max_loss, max_gain go on the trade
            # row via direct UPDATE since they aren't in the standard
            # insert payload.
            self._conn.execute(
                """
                UPDATE trades
                   SET cb_status_at_entry = ?
                 WHERE alpaca_order_id    = ?
                """,
                (cb_status_at_entry, alpaca_order_id),
            )
            # max_loss/max_gain belong to the cycle (one cycle per
            # spread instance). Stash them as a small JSON in
            # cycles.notes so they're queryable without a new table.
            self._conn.execute(
                """
                UPDATE cycles
                   SET notes = ?
                 WHERE cycle_id = ?
                """,
                (
                    json.dumps({
                        "legs": [
                            {"symbol": l.get("symbol"), "side": l.get("side")}
                            for l in legs
                        ],
                        "max_loss": max_loss,
                        "max_gain": max_gain,
                    }),
                    cycle_id,
                ),
            )
            self._conn.commit()
            return trade_id
        except Exception:
            logger.warning(
                "record_spread_open failed (strategy=%s underlying=%s order=%s)",
                strategy_type, underlying, alpaca_order_id, exc_info=True,
            )
            return None

    def record_spread_close(
        self,
        *,
        entry_alpaca_order_id: str,
        close_fill_price: float | None,
        realized_pnl: float | None,
        outcome: str,
    ) -> bool:
        """Update the spread's trade row with close info.

        ``outcome`` should be one of: 'profit', 'loss', 'breakeven',
        'expired_worthless', 'assignment'. The lifecycle helper passes
        'profit'/'loss' based on realized_pnl sign; the other outcomes
        are reserved for future expiry/assignment paths.
        """
        try:
            magnitude = abs(float(close_fill_price)) if close_fill_price is not None else None
            cur = self._conn.execute(
                """
                UPDATE trades
                   SET closed_at        = ?,
                       close_fill_price = ?,
                       realized_pnl     = ?,
                       outcome          = ?
                 WHERE alpaca_order_id  = ?
                """,
                (_now_iso(), magnitude, realized_pnl, outcome, entry_alpaca_order_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                logger.warning(
                    "record_spread_close: no trade row for entry order_id=%s",
                    entry_alpaca_order_id,
                )
                return False

            # Close the cycle too. Find it via the trade row.
            row = self._conn.execute(
                "SELECT cycle_id FROM trades WHERE alpaca_order_id = ?",
                (entry_alpaca_order_id,),
            ).fetchone()
            if row and row["cycle_id"]:
                self._r.cycles.close_cycle(
                    row["cycle_id"],
                    outcome=outcome,
                    total_premium=realized_pnl or 0.0,
                )
            return True
        except Exception:
            logger.exception(
                "record_spread_close failed for entry order_id=%s",
                entry_alpaca_order_id,
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
