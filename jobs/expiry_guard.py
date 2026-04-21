"""Expiry guard job -- runs at 3:00 PM ET every trading day.

Safety net that closes short options expiring today if they are ITM,
to avoid surprise assignment.  Not a primary exit mechanism.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings
from jobs._report import append_section
from utils.occ import dte_from_occ, extract_option_type, extract_strike

logger = logging.getLogger(__name__)

_ET = ZoneInfo(settings.TIMEZONE)


def run() -> None:
    """Check for 0-DTE positions and close ITM short options across all accounts."""
    logger.info("=== EXPIRY GUARD JOB STARTING ===")

    from brokers.broker_factory import get_broker, make_broker_cached
    from data.account_manager import AccountManager

    broker = get_broker()

    clock = broker.get_clock()
    if not clock.get("is_open"):
        logger.info("Market is closed. Exiting early.")
        return

    # Aggregate option positions across all accounts, tagging each with its account.
    _manager = AccountManager()
    account_brokers: dict[str, object] = {}
    positions: list[dict] = []
    for account_id in _manager.get_all_accounts():
        try:
            acct_broker = make_broker_cached(*_manager.get_credentials(account_id))
            account_brokers[account_id] = acct_broker
            acct_positions = acct_broker.get_positions()
            for p in acct_positions:
                p["_account"] = account_id
            positions.extend(acct_positions)
        except Exception as e:
            logger.warning("Failed to get positions for %s: %s", account_id, e)

    if not positions:
        logger.info("No open positions.")
        logger.info("=== EXPIRY GUARD JOB COMPLETE ===")
        return

    report_lines: list[str] = []

    for pos in positions:
        symbol = pos.get("symbol", "")
        acct_name = pos.get("_account")
        pos_broker = account_brokers.get(acct_name, broker)
        qty = float(pos.get("qty", 0))

        dte = dte_from_occ(symbol)
        if dte is None or dte != 0:
            continue

        # This position expires today
        current_price = float(pos.get("current_price", 0) or 0)
        strike = extract_strike(symbol)
        opt_type = extract_option_type(symbol)
        if strike is None or opt_type is None:
            logger.warning("Cannot parse OCC symbol %s", symbol)
            continue

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
                    pos_broker.place_order(
                        symbol=symbol,
                        qty=abs(int(qty)),
                        side="buy",  # buy to close a short
                        order_type="market",
                        time_in_force="day",
                    )
                    report_lines.append(
                        f"CLOSED: {symbol} (ITM short, expiring today, acct={acct_name})"
                    )
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
