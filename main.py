"""trade-pilot — unified entry point.

Usage:
  python main.py                                  # start scheduler
  python main.py --job pre_market                 # run pre-market job now
  python main.py --job market_open --dry-run      # test without trading
  python main.py --job market_open --symbol AAPL  # one symbol only
"""

import argparse
import importlib
import json
import logging
import logging.handlers
import sys

logger = logging.getLogger("trade-pilot")

JOB_MODULES = {
    "pre_market": "jobs.pre_market",
    "market_open": "jobs.market_open",
    "position_check": "jobs.position_check",
    "expiry_guard": "jobs.expiry_guard",
    "pre_close": "jobs.pre_close",
    "market_close": "jobs.market_close",
    "post_market": "jobs.post_market",
    "weekly_report": "jobs.weekly_report",
}


# ── CLI ────────────────────────────────��────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trade-pilot wheel strategy bot",
        epilog=(
            "examples:\n"
            "  python main.py                                  # start scheduler\n"
            "  python main.py --job pre_market                 # run one job\n"
            "  python main.py --job market_open --dry-run      # test without trading\n"
            "  python main.py --job market_open --symbol AAPL  # single symbol\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--job",
        choices=list(JOB_MODULES.keys()),
        help="Run a single job by name, then exit",
    )
    parser.add_argument(
        "--symbol",
        help="Override WATCHLIST with a single symbol (e.g. AAPL)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Override DRY_RUN to True — log decisions but do not trade",
    )
    return parser.parse_args()


# ── order execution helper (used by jobs) ───────────────────


def execute_decision(broker, decision: dict) -> dict | None:
    """Translate a Claude recommendation into a broker order.

    Args:
        broker: A BaseBroker instance.
        decision: Validated recommendation dict.

    Returns:
        The order result dict (with fill_price/fill_status attached
        when available), or None for hold/skip.
    """
    action = decision["action"]

    if action in ("hold", "skip"):
        return None

    result: dict | None = None

    if action in ("sell_put", "sell_call"):
        result = broker.place_order(
            symbol=decision["symbol"],
            qty=decision["qty"],
            side="sell",
            order_type=decision["order_type"],
            time_in_force="day",
            limit_price=decision.get("limit_price"),
        )

    elif action == "roll":
        # Roll = cancel existing + open new position
        open_orders = broker.get_orders(status="open")
        for order in open_orders:
            if order.get("symbol", "").upper().startswith(
                decision["symbol"][:6] if decision.get("symbol") else ""
            ):
                broker.cancel_order(order["id"])
                logger.info("Cancelled order %s for roll", order["id"])
                break

        result = broker.place_order(
            symbol=decision["symbol"],
            qty=decision["qty"],
            side="sell",
            order_type=decision["order_type"],
            time_in_force="day",
            limit_price=decision.get("limit_price"),
        )

    else:
        logger.warning("Unhandled action: %s", action)
        return None

    # ── Fill confirmation ──────────────────────────────────
    if result:
        import time as _time
        order_id = result.get("id")
        if order_id:
            _time.sleep(30)
            try:
                filled = broker.get_order(order_id)
                fill_price = filled.get("filled_avg_price")
                status = filled.get("status")
                logger.info(
                    "Order %s status=%s fill_price=%s",
                    order_id, status, fill_price,
                )
                if status not in ("filled", "partially_filled"):
                    logger.warning(
                        "Order %s not filled after 30s — status=%s. "
                        "It will expire at market close if not filled.",
                        order_id, status,
                    )
                result["fill_price"] = fill_price
                result["fill_status"] = status
            except Exception:
                logger.warning(
                    "Could not confirm fill for order %s", order_id, exc_info=True,
                )

    return result


# ── startup validation ──────────────────────────────────────


def validate_startup() -> None:
    """Verify env vars, broker connectivity, and account health.

    Exits the process if any critical check fails.
    """
    from config import settings

    # 1. Required env vars (Settings._require already enforces these
    #    at import time, but be explicit for clarity)
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY"):
        if not getattr(settings, var, None):
            logger.error("Missing required environment variable: %s", var)
            sys.exit(1)

    # 2. Test Alpaca connection
    from brokers.broker_factory import get_broker

    try:
        broker = get_broker()
        account = broker.get_account()
    except Exception:
        logger.exception("Failed to connect to broker — check API keys")
        sys.exit(1)

    # 3. Log account status
    logger.info(
        "Account: status=%s | buying_power=%s | portfolio_value=%s | options_level=%s",
        account.get("status"),
        account.get("buying_power"),
        account.get("portfolio_value"),
        account.get("options_trading_level"),
    )

    # 4. Options trading level check
    if account.get("options_trading_level") == 0:
        logger.error(
            "Options trading is not enabled on this account (level=0). Exiting."
        )
        sys.exit(1)

    logger.info("Startup validation passed")


# ── main ────────────────────────────────────────────────────


def main() -> None:
    args = parse_args()

    from config import settings

    # Apply CLI overrides to settings
    if args.dry_run:
        settings.DRY_RUN = True

    if args.symbol:
        settings.WATCHLIST = [args.symbol.upper()]

    # Configure logging
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=settings.LOG_DIR / "trade-pilot.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(log_format, datefmt=date_fmt))

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(log_format, datefmt=date_fmt))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[stream_handler, file_handler],
    )

    logger.info("Mode: %s", "PRODUCTION" if settings.RENDER else "LOCAL")
    logger.info("Dry Run: %s", settings.DRY_RUN)
    logger.info("Watchlist: %s", settings.WATCHLIST)

    # Startup validation (always runs)
    validate_startup()

    # ── Job mode: run once and exit ─────────────────────────
    if args.job:
        logger.info("Running job: %s", args.job)
        module = importlib.import_module(JOB_MODULES[args.job])
        module.run()
        logger.info("Job %s finished — exiting", args.job)
        return

    # ── Scheduler mode: start the loop ──────────────────────
    from scheduler import register_jobs, is_weekday, safe_run
    from jobs import pre_market
    import schedule

    logger.info("=== trade-pilot scheduler starting ===")

    register_jobs()

    logger.info("Scheduled jobs: %d", len(schedule.jobs))

    # Run pre-market immediately on startup if it's a weekday
    # (handles scheduler restarts mid-day)
    if is_weekday():
        logger.info("Weekday detected — running startup check")
        safe_run(pre_market.run, "startup_pre_market")

    import time

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutdown requested — exiting.")
