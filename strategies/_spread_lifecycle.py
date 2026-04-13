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
            if strategy.spread_tracker and spread_id:
                exit_credit = (
                    abs(float(fill_price)) if fill_price is not None else None
                )
                strategy.spread_tracker.close_spread(
                    spread_id, exit_credit=exit_credit,
                )
            strategy.state = State.IDLE
            strategy.open_spread_id = None
            strategy.pending_order_id = None
            strategy._save_state()
            logger.info(
                "%s reconcile: close %s FILLED → CLOSED → IDLE",
                type(strategy).__name__, order_id,
            )
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
