"""Portfolio refresh job -- runs every 5 minutes during market hours.

Keeps the dashboard data fresh between decision cycles by updating
portfolio, circuit breaker, and spread reconciliation snapshots.
"""

import logging
from dataclasses import asdict

logger = logging.getLogger(__name__)


def run() -> None:
    """Fetch current equity/positions and update snapshot files."""
    logger.debug("=== PORTFOLIO REFRESH ===")

    from brokers.broker_factory import get_broker
    from data.spread_tracker import SpreadTracker
    from data.state_writer import StateWriter
    from strategies.circuit_breaker import CircuitBreaker

    broker = get_broker()
    sw = StateWriter()

    # ── Market-open check ───────────────────────────────────
    clock = broker.get_clock()
    if not clock.get("is_open"):
        return

    account = broker.get_account()
    equity = float(account.get("portfolio_value", 0))
    positions = broker.get_positions()

    # ── Circuit breaker update ──────────────────────────────
    cb = CircuitBreaker()
    cb_status = cb.update(equity)

    try:
        sw.write_circuit_breaker_status(asdict(cb_status))
    except Exception as e:
        logger.warning("Failed to write circuit breaker snapshot: %s", e)

    # ── Spread reconciliation ───────────────────────────────
    open_spreads: list[dict] = []
    try:
        tracker = SpreadTracker()
        closed_ids = tracker.reconcile_with_alpaca(positions)
        for sid in closed_ids:
            tracker.close_spread(sid)
        open_spreads = tracker.to_snapshot()
    except Exception as e:
        logger.warning("Failed to reconcile spreads: %s", e)

    # ── Portfolio snapshot (includes spreads) ────────────────
    try:
        sw.write_portfolio_snapshot(
            account, positions, {},
            open_spreads=open_spreads,
        )
    except Exception as e:
        logger.warning("Failed to write portfolio snapshot: %s", e)

    logger.debug(
        "Portfolio refresh: equity=$%.2f | CB=%s | positions=%d",
        equity, cb_status.status, len(positions),
    )
