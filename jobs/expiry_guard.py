"""Expiry guard job -- runs at 3:00 PM ET every trading day.

Safety net that closes short options expiring today if they are ITM,
to avoid surprise assignment.  Not a primary exit mechanism.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings
from jobs._report import append_section
from utils.market_hours import parse_dte_from_occ

logger = logging.getLogger(__name__)

_ET = ZoneInfo(settings.TIMEZONE)


def run() -> None:
    """Check for 0-DTE positions and close ITM short options."""
    logger.info("=== EXPIRY GUARD JOB STARTING ===")

    from brokers.broker_factory import get_broker

    broker = get_broker()

    clock = broker.get_clock()
    if not clock.get("is_open"):
        logger.info("Market is closed. Exiting early.")
        return

    positions = broker.get_positions()
    if not positions:
        logger.info("No open positions.")
        logger.info("=== EXPIRY GUARD JOB COMPLETE ===")
        return

    report_lines: list[str] = []

    for pos in positions:
        symbol = pos.get("symbol", "")
        qty = float(pos.get("qty", 0))

        dte = parse_dte_from_occ(symbol)
        if dte is None or dte != 0:
            continue

        # This position expires today
        current_price = float(pos.get("current_price", 0) or 0)
        # Extract strike from last 8 digits of OCC symbol
        try:
            strike = int(symbol[-8:]) / 1000
        except (ValueError, IndexError):
            logger.warning("Cannot parse strike from %s", symbol)
            continue

        # Determine if C or P
        is_put = "P" in symbol[len(symbol) - 9:len(symbol) - 8].upper() if len(symbol) > 9 else False
        # Better: parse type from OCC
        opt_type = None
        for i, ch in enumerate(symbol):
            if ch.isdigit():
                type_idx = i + 6
                if type_idx < len(symbol):
                    opt_type = symbol[type_idx].upper()
                break

        is_short = qty < 0
        is_itm = False
        underlying_price = float(pos.get("underlying_price", 0) or 0)

        if underlying_price > 0:
            if opt_type == "P":
                is_itm = underlying_price < (strike - 0.10)
            elif opt_type == "C":
                is_itm = underlying_price > (strike + 0.10)

        if is_short and is_itm:
            logger.warning(
                "EXPIRY GUARD: %s is SHORT, ITM, expiring today "
                "(strike=%.2f, underlying=%.2f). Closing to avoid assignment.",
                symbol, strike, underlying_price,
            )
            try:
                if settings.DRY_RUN:
                    logger.info("DRY RUN - would close %s (qty=%s)", symbol, qty)
                    report_lines.append(f"DRY RUN: would close {symbol}")
                else:
                    broker.place_order(
                        symbol=symbol,
                        qty=abs(int(qty)),
                        side="buy",  # buy to close a short
                        order_type="market",
                        time_in_force="day",
                    )
                    report_lines.append(f"CLOSED: {symbol} (ITM short, expiring today)")
            except Exception:
                logger.exception("Failed to close expiring position %s", symbol)
                report_lines.append(f"FAILED to close: {symbol}")
        elif is_short and not is_itm:
            logger.info(
                "EXPIRY GUARD: %s is SHORT, OTM, expiring today — will expire worthless",
                symbol,
            )
            report_lines.append(f"OTM: {symbol} (will expire worthless)")
        else:
            logger.info("EXPIRY GUARD: %s expires today (long, qty=%s)", symbol, qty)
            report_lines.append(f"LONG expiring: {symbol}")

    if report_lines:
        append_section("Expiry Guard (3:00 PM ET)", "\n".join(report_lines))

    logger.info("=== EXPIRY GUARD JOB COMPLETE ===")
