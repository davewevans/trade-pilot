"""Shared lifecycle helpers for spread strategies.

Each spread strategy mirrors a small state machine:

    IDLE → PENDING_OPEN → OPEN → PENDING_CLOSE → CLOSED → IDLE

This module factors the order-status reconciliation logic that all four
strategies (iron condor, bull put, bear call, long call vertical) share,
so the per-strategy file only has to wire the state enum + state-file path.
"""

import logging

logger = logging.getLogger(__name__)

# Broker order statuses we treat as terminal.
_FILLED = {"filled"}
_CANCELED = {"canceled", "cancelled", "rejected", "expired"}


def reconcile_pending_state(strategy) -> None:
    """Drive ``strategy`` through PENDING_* → OPEN/CLOSED/IDLE transitions.

    Expects ``strategy`` to expose:
        * ``state``                   — current State enum value
        * ``State``                   — the State enum class
        * ``open_spread_id``          — current spread id (or None)
        * ``pending_order_id``        — broker order id awaiting fill
        * ``broker``                  — broker with ``get_order(order_id)``
        * ``spread_tracker``          — SpreadTracker instance
        * ``_save_state()``           — persists state, open_spread_id, pending_order_id

    Idempotent — safe to call repeatedly. Never raises.
    """
    State = strategy.State
    state = strategy.state

    if state not in (State.PENDING_OPEN, State.PENDING_CLOSE):
        return

    order_id = getattr(strategy, "pending_order_id", None)
    spread_id = strategy.open_spread_id
    if not order_id:
        logger.warning(
            "%s in %s but has no pending_order_id — leaving as-is for manual review",
            type(strategy).__name__, state.value,
        )
        return

    try:
        order = strategy.broker.get_order(order_id)
    except Exception:
        logger.warning(
            "%s reconcile: get_order(%s) failed — will retry next cycle",
            type(strategy).__name__, order_id, exc_info=True,
        )
        return

    broker_status = str(order.get("status") or "").lower()
    fill_price = order.get("filled_avg_price")

    if state == State.PENDING_OPEN:
        if broker_status in _FILLED:
            if strategy.spread_tracker and spread_id:
                strategy.spread_tracker.mark_open(spread_id, fill_price=fill_price)
            strategy.state = State.OPEN
            strategy.pending_order_id = None
            strategy._save_state()
            logger.info(
                "%s reconcile: entry %s FILLED → OPEN",
                type(strategy).__name__, order_id,
            )
            _record_spread_open_to_db(strategy, spread_id, order_id, fill_price)
        elif broker_status in _CANCELED:
            if strategy.spread_tracker and spread_id:
                strategy.spread_tracker.cancel_pending_open(
                    spread_id, reason=broker_status,
                )
            strategy.state = State.IDLE
            strategy.open_spread_id = None
            strategy.pending_order_id = None
            strategy._save_state()
            logger.warning(
                "%s reconcile: entry %s %s → IDLE",
                type(strategy).__name__, order_id, broker_status.upper(),
            )
        else:
            logger.debug(
                "%s reconcile: entry %s status=%s — still PENDING_OPEN",
                type(strategy).__name__, order_id, broker_status,
            )

    elif state == State.PENDING_CLOSE:
        if broker_status in _FILLED:
            spread_snapshot = None
            if strategy.spread_tracker and spread_id:
                # Capture the spread BEFORE close_spread mutates it so we
                # can read entry_order_id + computed pnl for the DB update.
                spread_snapshot = strategy.spread_tracker._find(spread_id)
                exit_credit = (
                    abs(float(fill_price)) if fill_price is not None else None
                )
                strategy.spread_tracker.close_spread(
                    spread_id, exit_credit=exit_credit,
                )
                # Re-read so we get the post-close pnl that close_spread computed.
                spread_snapshot = strategy.spread_tracker._find(spread_id)
            strategy.state = State.IDLE
            strategy.open_spread_id = None
            strategy.pending_order_id = None
            strategy._save_state()
            logger.info(
                "%s reconcile: close %s FILLED → CLOSED → IDLE",
                type(strategy).__name__, order_id,
            )
            _record_spread_close_to_db(strategy, spread_snapshot, fill_price)
        elif broker_status in _CANCELED:
            if strategy.spread_tracker and spread_id:
                strategy.spread_tracker.revert_to_open(
                    spread_id, reason=broker_status,
                )
            strategy.state = State.OPEN
            strategy.pending_order_id = None
            strategy._save_state()
            logger.warning(
                "%s reconcile: close %s %s → reverted to OPEN",
                type(strategy).__name__, order_id, broker_status.upper(),
            )
        else:
            logger.debug(
                "%s reconcile: close %s status=%s — still PENDING_CLOSE",
                type(strategy).__name__, order_id, broker_status,
            )


# ── DB dual-write hooks ─────────────────────────────────────
# These run AFTER the state machine has finished the in-memory
# transition. They are best-effort: a DB failure must never roll back
# the strategy state, which is the source of truth for trading logic.

def _strategy_name_for_recorder(strategy) -> str | None:
    """Map the strategy class to its trades.strategy_type string."""
    name = type(strategy).__name__
    return {
        "BullPutSpreadStrategy":     "bull_put_spread",
        "BearCallSpreadStrategy":    "bear_call_spread",
        "IronCondorStrategy":        "iron_condor",
        "LongCallVerticalStrategy":  "long_call_vertical",
    }.get(name)


def _record_spread_open_to_db(strategy, spread_id, order_id, fill_price) -> None:
    recorder = getattr(strategy, "recorder", None)
    if recorder is None or not strategy.spread_tracker or not spread_id:
        return
    spread = strategy.spread_tracker._find(spread_id)
    if not spread:
        return
    strategy_type = _strategy_name_for_recorder(strategy)
    if strategy_type is None:
        return
    try:
        from database.recorder import SpreadTradeRecorder
        SpreadTradeRecorder(recorder).record_spread_open(
            strategy_type=strategy_type,
            underlying=spread.get("underlying", ""),
            legs=spread.get("legs") or [],
            alpaca_order_id=order_id,
            fill_price=fill_price,
            max_loss=spread.get("max_loss"),
            max_gain=spread.get("max_gain"),
            cb_status_at_entry=spread.get("cb_status_at_entry"),
        )
    except Exception:
        logger.warning(
            "DB record_spread_open failed for %s order=%s",
            type(strategy).__name__, order_id, exc_info=True,
        )


def _record_spread_close_to_db(strategy, spread_snapshot, close_fill_price) -> None:
    recorder = getattr(strategy, "recorder", None)
    if recorder is None or not spread_snapshot:
        return
    entry_order_id = spread_snapshot.get("entry_order_id")
    if not entry_order_id:
        return
    pnl = spread_snapshot.get("pnl")
    # close_spread() stores pnl as (entry_credit - exit_credit). For a
    # debit spread the user "paid" entry_credit (which we stored as a
    # positive magnitude), so a profitable exit is exit_credit > entry,
    # which the same formula reports as NEGATIVE — flip the sign for
    # debit spreads so 'outcome' is meaningful.
    strategy_type = _strategy_name_for_recorder(strategy)
    is_debit_spread = strategy_type == "long_call_vertical"
    realized = -pnl if (is_debit_spread and pnl is not None) else pnl

    if realized is None:
        outcome = "unknown"
    elif realized > 0:
        outcome = "profit"
    elif realized < 0:
        outcome = "loss"
    else:
        outcome = "breakeven"

    try:
        from database.recorder import SpreadTradeRecorder
        SpreadTradeRecorder(recorder).record_spread_close(
            entry_alpaca_order_id=entry_order_id,
            close_fill_price=close_fill_price,
            realized_pnl=realized,
            outcome=outcome,
        )
    except Exception:
        logger.warning(
            "DB record_spread_close failed for %s entry_order=%s",
            type(strategy).__name__, entry_order_id, exc_info=True,
        )
