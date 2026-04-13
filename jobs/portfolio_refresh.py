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
    positions = broker.get_positions()

    # Aggregate equity across all accounts for the circuit breaker.
    from jobs.startup_snapshot import ACCOUNT_BROKER_MAP
    from brokers.broker_factory import make_broker
    total_equity = 0.0
    aggregation_ok = True
    for acct_name, strategy_key in ACCOUNT_BROKER_MAP.items():
        try:
            acct_broker = make_broker(strategy_key)
            acct = acct_broker.get_account()
            total_equity += float(acct.get("portfolio_value", 0))
        except Exception as e:
            logger.warning("Failed to get equity for %s account: %s", acct_name, e)
            aggregation_ok = False
            break
    equity = total_equity if (aggregation_ok and total_equity > 0) else float(account.get("portfolio_value", 0))

    # ── Circuit breaker update ──────────────────────────────
    cb = CircuitBreaker()
    cb_status = cb.update(equity)

    try:
        sw.write_circuit_breaker_status(asdict(cb_status))
    except Exception as e:
        logger.warning("Failed to write circuit breaker snapshot: %s", e)

    # ── Spread reconciliation ───────────────────────────────
    open_spreads: list[dict] = []
    spread_legs: set[str] = set()
    try:
        tracker = SpreadTracker()
        closed_ids = tracker.reconcile_with_alpaca(positions)
        for sid in closed_ids:
            tracker.close_spread(sid)
        open_spreads = tracker.to_snapshot()
        spread_legs = tracker.get_all_leg_symbols()
    except Exception as e:
        logger.warning("Failed to reconcile spreads: %s", e)

    # ── Portfolio snapshot (includes spreads) ────────────────
    try:
        from config import settings
        sw.write_portfolio_snapshot(
            account, positions, {},
            open_spreads=open_spreads,
            wheel_symbols=list(settings.WATCHLIST),
            spread_leg_symbols=spread_legs,
        )
    except Exception as e:
        logger.warning("Failed to write portfolio snapshot: %s", e)

    # ── Per-account snapshots (powers individual account cards) ──
    for acct_name, strategy_key in ACCOUNT_BROKER_MAP.items():
        try:
            acct_broker = make_broker(strategy_key)
            acct_data = acct_broker.get_account()
            acct_positions = acct_broker.get_positions()
            sw.write_account_snapshot(acct_name, acct_data, acct_positions)
        except Exception as e:
            logger.warning(
                "Failed to refresh account snapshot for %s: %s", acct_name, e
            )

    # ── Equity history (powers the equity curve chart) ──────────
    try:
        history = broker.get_portfolio_history(period="3M", timeframe="1D")
        if history:
            sw.write_equity_history(history)
    except Exception as e:
        logger.warning("Failed to fetch/write equity history: %s", e)

    logger.debug(
        "Portfolio refresh: equity=$%.2f | CB=%s | positions=%d",
        equity, cb_status.status, len(positions),
    )
