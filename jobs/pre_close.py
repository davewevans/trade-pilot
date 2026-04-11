"""Pre-close job — runs at 3:15 PM ET every weekday."""

import logging
from datetime import datetime

from config import settings
from jobs._report import append_section

logger = logging.getLogger(__name__)


def run() -> None:
    """Observation-only sweep for expiring and high-gamma positions.

    No new orders are placed — too close to the 3:30 PM cutoff.
    """
    logger.info("=== PRE-CLOSE JOB STARTING ===")

    from brokers.broker_factory import get_broker

    broker = get_broker()

    # ── Market-open check ───────────────────────────────────
    clock = broker.get_clock()
    if not clock.get("is_open"):
        logger.info("Market is closed. Exiting early.")
        return

    positions = broker.get_positions()
    if not positions:
        logger.info("No open positions to review before close.")
        append_section("Pre-Close Review (3:15 PM ET)", "No open positions.")
        logger.info("=== PRE-CLOSE JOB COMPLETE ===")
        return

    today = datetime.now().date()
    report_lines: list[str] = []

    for pos in positions:
        symbol = pos.get("symbol", "")
        try:
            # Parse expiration from OCC symbol: ROOT(≤6) + YYMMDD + C/P + strike(8)
            # Find the first digit run of 6 for the date
            digits_start = None
            for i, ch in enumerate(symbol):
                if ch.isdigit():
                    digits_start = i
                    break

            if digits_start is None or len(symbol) < digits_start + 6:
                logger.warning("Cannot parse expiration from %s — skipping", symbol)
                continue

            date_part = symbol[digits_start : digits_start + 6]
            exp_date = datetime.strptime(date_part, "%y%m%d").date()
            dte = (exp_date - today).days

            if dte == 0:
                msg = (
                    f"WARNING: {symbol} expires today — "
                    "Alpaca will auto-liquidate at 3:30 PM if not closed"
                )
                logger.warning(msg)
                report_lines.append(f"🚨 **{symbol}** — EXPIRES TODAY")

            elif dte <= 7:
                logger.info("%s DTE=%d — gamma risk window", symbol, dte)
                report_lines.append(f"⚠️ **{symbol}** — DTE {dte} (gamma risk)")

            else:
                report_lines.append(f"**{symbol}** — DTE {dte} (OK)")

        except Exception:
            logger.exception("Pre-close check failed for %s — continuing", symbol)
            report_lines.append(f"**{symbol}** — ERROR (see logs)")

    append_section("Pre-Close Review (3:15 PM ET)", "\n".join(report_lines))
    logger.info("=== PRE-CLOSE JOB COMPLETE ===")
