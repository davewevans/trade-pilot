"""
reconcile_orders.py — Pending order fill reconciliation job.

STATUS: STUB — not yet implemented. Do not register in scheduler.py.

Purpose:
    Query trades WHERE fill_status = 'pending', call broker.get_order()
    for each, and UPDATE fill_price / fill_status / filled_at / filled_qty
    / premium_credit. Runs at each position_check pass and at pre_close.

This job was deferred until the SQLite schema was in place.
See: TradeRepository.get_pending() and TradeRepository.update_fill().
"""


def reconcile_pending_orders(broker, trade_repo) -> None:
    """Update fill data for all orders with fill_status='pending'.

    Args:
        broker: A BaseBroker instance with a get_order(order_id) method.
        trade_repo: A TradeRepository instance.
    """
    raise NotImplementedError("reconcile_pending_orders is not yet implemented")
