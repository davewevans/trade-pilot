"""Options-specific trading time constraints.

All times are Eastern (America/New_York).  Alpaca rejects options orders
after 3:15 PM ET, and expiration-day handling has additional restrictions.
"""

import logging
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)

_ET = ZoneInfo(settings.TIMEZONE)

BROAD_BASED_ETFS = {"SPY", "QQQ", "IWM", "DIA", "XSP", "VIX"}

MARKET_OPEN = dt_time(9, 30)
OPTIONS_CUTOFF = dt_time(15, 15)
MARKET_CLOSE = dt_time(16, 0)


def is_options_order_allowed(
    underlying_symbol: str,
    dte: int | None = None,
) -> tuple[bool, str]:
    """Check whether an options order may be placed right now.

    Returns ``(True, "")`` if allowed, or ``(False, reason)`` if not.
    """
    now_et = datetime.now(_ET)
    current = now_et.time()

    # 1. Market hours
    if current < MARKET_OPEN:
        return False, f"Market not open yet (current {current.strftime('%H:%M')} ET)"
    if current >= MARKET_CLOSE:
        return False, f"Market closed (current {current.strftime('%H:%M')} ET)"

    # 2. Options cutoff — Alpaca rejects after 3:15 PM ET
    if current >= OPTIONS_CUTOFF:
        return False, f"After options cutoff 3:15 PM ET (current {current.strftime('%H:%M')} ET)"

    # 3. Expiration-day restrictions
    if dte is not None and dte <= 0:
        return False, (
            f"Cannot open new positions on expiration day (DTE={dte}) "
            f"for {underlying_symbol}"
        )

    return True, ""


def parse_dte_from_occ(symbol: str) -> int | None:
    """Extract DTE from an OCC symbol, or None if unparseable."""
    for i, ch in enumerate(symbol):
        if ch.isdigit():
            date_part = symbol[i : i + 6]
            if len(date_part) == 6:
                try:
                    exp = datetime.strptime(date_part, "%y%m%d").date()
                    return (exp - datetime.now(_ET).date()).days
                except ValueError:
                    pass
            break
    return None
