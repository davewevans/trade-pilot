"""trade-pilot scheduler — main entry point for the running bot.

Schedules all jobs using the ``schedule`` library with
America/New_York timezone so times are always expressed in ET.

The ``schedule`` library accepts timezone strings (backed by pytz
internally) in its ``.at(time, tz=...)`` method, which handles
EDT/EST transitions automatically.

NOTE for Render / cloud deployment:
    The server's system clock can be any timezone — the ``tz``
    parameter on each ``.at()`` call ensures the library converts
    to the correct UTC instant regardless of the host's local time.
"""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import schedule

from jobs import (
    expiry_guard,
    market_close,
    market_open,
    portfolio_refresh,
    position_check,
    post_market,
    pre_close,
    pre_market,
    weekly_report,
)

logger = logging.getLogger("trade-pilot.scheduler")

ET = "America/New_York"
_ET_ZONE = ZoneInfo(ET)


# ── helpers ─────────────────────────────────────────────────


def is_weekday() -> bool:
    """Return True if today is Monday–Friday in America/New_York."""
    return datetime.now(_ET_ZONE).weekday() < 5


def safe_run(job_fn, job_name: str) -> None:
    """Execute *job_fn* inside a protective wrapper.

    Logs start/end with elapsed time.  On exception the full
    traceback is logged but the error is **not** re-raised so the
    scheduler loop keeps running.
    """
    logger.info("=== STARTING: %s ===", job_name)
    t0 = time.monotonic()
    try:
        job_fn()
    except Exception:
        logger.exception("=== FAILED: %s ===", job_name)
    else:
        elapsed = time.monotonic() - t0
        logger.info("=== COMPLETED: %s in %.1fs ===", job_name, elapsed)


def _weekday_run(job_fn, job_name: str) -> None:
    """Wrapper that skips execution on weekends."""
    if not is_weekday():
        logger.debug("Skipping %s — weekend", job_name)
        return
    safe_run(job_fn, job_name)


# ── schedule registration ──────────────────────────────────


def _cleanup_orats_cache() -> None:
    from data.orats_cache import ORATSCache
    cache = ORATSCache()
    deleted = cache.clear_expired()
    logger.info("ORATS cache cleanup: %d expired entries removed", deleted)


def register_jobs() -> None:
    """Register every job with the ``schedule`` library."""

    # Weekday jobs — all times ET
    # NOTE: market_open runs at 10:00 instead of 9:30 because:
    # - Options bid-ask spreads are 2-3x wider in the first 30 minutes
    # - Quoted Greeks (especially delta) are unreliable at the open
    # - Limit orders based on mid-price at 9:30 are more likely to be
    #   unfavorable compared to 10:00 pricing.
    # The wheel and spread strategies all use limit orders at the mid,
    # so accurate mid-price matters for fill quality.
    weekday_jobs: list[tuple[str, object, str]] = [
        ("06:00", pre_market.run, "pre_market"),
        ("10:00", market_open.run, "market_open"),
        ("10:45", position_check.run, "position_check"),
        ("11:30", position_check.run, "position_check"),
        ("12:30", position_check.run, "position_check"),
        ("14:00", position_check.run, "position_check"),
        ("15:00", expiry_guard.run, "expiry_guard"),
        ("15:15", pre_close.run, "pre_close"),
        ("16:00", market_close.run, "market_close"),
        ("16:30", post_market.run, "post_market"),
    ]

    for time_str, fn, name in weekday_jobs:
        schedule.every().day.at(time_str, tz=ET).do(
            _weekday_run, job_fn=fn, job_name=name,
        )

    # Portfolio refresh — every 5 minutes during market hours
    schedule.every(5).minutes.do(
        _weekday_run, job_fn=portfolio_refresh.run, job_name="portfolio_refresh",
    )

    # Weekly report — Sunday 6:00 PM ET
    schedule.every().sunday.at("18:00", tz=ET).do(
        safe_run, job_fn=weekly_report.run, job_name="weekly_report",
    )

    # ORATS cache cleanup — daily at 5:00 AM ET
    schedule.every().day.at("05:00", tz=ET).do(
        safe_run, job_fn=_cleanup_orats_cache, job_name="orats_cache_cleanup",
    )


# ── standalone entry (prefer main.py instead) ──────────────

if __name__ == "__main__":
    # When run directly, defer to main.py so logging and startup
    # validation are applied consistently.
    from main import main

    main()
