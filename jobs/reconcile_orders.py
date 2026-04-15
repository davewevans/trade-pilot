"""Pending order fill reconciliation.

For every trade in the SQLite ``trades`` table with
``fill_status='pending'`` that was submitted *today*, ask the broker
for its current status and write the resolution back into the DB.

Scope (option 1):
    Today's pending rows only. Prior-day stragglers are counted and
    logged but not processed — they will be swept up by a future
    startup reconciler (option 2).

Status mapping:
    ``filled``                            → ``filled``  (sets fill_price + filled_at)
    ``canceled`` / ``cancelled``          → ``canceled``
    anything else (open, accepted,
    pending_new, …)                       → leave row as ``pending``

This function never raises. Per-order failures are logged; the loop
continues. The market_close JSON path (`journal.update`) is
independent of this and runs regardless of DB state.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


def reconcile_pending_orders(broker, recorder) -> None:
    """Resolve today's pending trades against the broker.

    Args:
        broker: A BaseBroker instance with ``get_order(order_id)``.
        recorder: A ``TradeRecorder``. Must expose ``trades.get_pending()``
            and ``resolve_trade(order_id, fill_status, fill_price)``.

    Note: the original stub named the second parameter ``trade_repo``;
    it is now ``recorder`` because the body calls a recorder method.
    Same arity and position — caller updates are trivial.
    """
    today_str = date.today().isoformat()

    try:
        pending = recorder.trades.get_pending()
    except Exception:
        logger.exception("reconcile: failed to fetch pending trades")
        return

    if not pending:
        logger.info("reconcile: no pending trades")
        return

    today_rows = []
    stale_rows = []
    for r in pending:
        submitted_at = str(r.get("submitted_at") or "")
        if submitted_at.startswith(today_str):
            today_rows.append(r)
        else:
            stale_rows.append(r)

    logger.info(
        "reconcile: %d pending today, %d from prior days",
        len(today_rows), len(stale_rows),
    )

    resolved = 0
    skipped = 0
    errors = 0

    for row in today_rows:
        order_id = row.get("alpaca_order_id")
        if not order_id:
            logger.warning("reconcile: row id=%s has no alpaca_order_id", row.get("id"))
            errors += 1
            continue

        try:
            order = broker.get_order(order_id)
        except Exception:
            logger.warning(
                "reconcile: broker.get_order(%s) failed", order_id, exc_info=True,
            )
            errors += 1
            continue

        broker_status = str(order.get("status") or "").lower()
        if broker_status == "filled":
            fill_price = _to_float(order.get("filled_avg_price"))
            ok = recorder.resolve_trade(order_id, "filled", fill_price=fill_price)
            if ok:
                resolved += 1
                logger.info(
                    "reconcile: %s → filled @ %s", order_id, fill_price,
                )
            else:
                errors += 1
        elif broker_status in ("canceled", "cancelled"):
            ok = recorder.resolve_trade(order_id, "canceled")
            if ok:
                resolved += 1
                logger.info("reconcile: %s → canceled", order_id)
            else:
                errors += 1
        else:
            # Non-terminal (open, accepted, pending_new, ...) — leave
            # pending. Per spec, terminal-but-uncommon statuses like
            # 'expired'/'rejected' also fall through here for now; the
            # future startup reconciler can address them.
            skipped += 1
            logger.debug(
                "reconcile: %s status=%s — leaving pending",
                order_id, broker_status,
            )

    logger.info(
        "reconcile: today resolved=%d still_pending=%d errors=%d",
        resolved, skipped, errors,
    )

    if stale_rows:
        logger.warning(
            "reconcile: %d pending trades from prior days — "
            "will be resolved by future startup reconciler",
            len(stale_rows),
        )


def reconcile_all_pending(broker, recorder) -> None:
    """Resolve ALL pending trades regardless of date.

    Called once at startup (in main.py validate_startup) to sweep up any
    stale pending rows from prior days that were never resolved due to
    crashes, Render restarts, or market_close job failures.

    Same logic as reconcile_pending_orders() but without the today-only
    filter.  Stale open orders (e.g. day orders from a prior session that
    never got a terminal status) are canceled outright.
    """
    try:
        pending = recorder.trades.get_pending()
    except Exception:
        logger.exception("Startup reconciler: failed to query pending trades")
        return

    if not pending:
        logger.info("Startup reconciler: no pending trades to resolve")
        return

    logger.info(
        "Startup reconciler: found %d pending trade(s) to resolve",
        len(pending),
    )

    resolved = 0
    for row in pending:
        order_id = row.get("alpaca_order_id")
        if not order_id:
            continue

        try:
            order = broker.get_order(order_id)
        except Exception:
            logger.warning(
                "Startup reconciler: get_order(%s) failed — skipping",
                order_id, exc_info=True,
            )
            continue

        broker_status = str(order.get("status") or "").lower()
        fill_price = order.get("filled_avg_price")

        if broker_status == "filled":
            fp = float(fill_price) if fill_price is not None else None
            recorder.resolve_trade(order_id, "filled", fp)
            resolved += 1
        elif broker_status in ("canceled", "cancelled", "rejected", "expired"):
            recorder.resolve_trade(order_id, broker_status)
            resolved += 1
        else:
            # Still open somehow — cancel it (stale from a prior session)
            try:
                broker.cancel_order(order_id)
                logger.info(
                    "Startup reconciler: canceled stale open order %s", order_id,
                )
                recorder.resolve_trade(order_id, "canceled")
                resolved += 1
            except Exception:
                logger.warning(
                    "Startup reconciler: failed to cancel stale order %s",
                    order_id, exc_info=True,
                )

    logger.info(
        "Startup reconciler: resolved %d / %d pending trade(s)",
        resolved, len(pending),
    )


def reconcile_pending_spreads(strategies) -> None:
    """Drive each spread strategy through PENDING_* → terminal transitions.

    Args:
        strategies: iterable of spread-strategy instances exposing
            ``reconcile_pending()``. Each call is independent and never
            raises — failures are logged and the loop continues.

    This is the order-status reconciler for multi-leg spread positions.
    Run it at market_open *before* any trading decisions are made, and
    again after each entry/exit so stale PENDING_* state is short-lived.
    """
    for strat in strategies:
        try:
            strat.reconcile_pending()
        except Exception:
            logger.exception(
                "reconcile_pending_spreads: %s failed",
                type(strat).__name__,
            )


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
