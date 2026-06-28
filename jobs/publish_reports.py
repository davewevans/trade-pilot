"""Daily report-publish job — runs at 4:45 PM ET every weekday.

Builds today's (ET) daily evaluation bundle and commits it to the private
reports repo that the Hermes self-improvement agent reads. Publishes
unconditionally when the flag is on — a zero-decision day is itself signal for
Hermes's watchdog, so we do NOT gate on whether the bot traded.

Flag-gated on ``HERMES_PUBLISH_ENABLED``; a no-op when the flag is off.
Publishing is data exhaust: any failure is logged and the job returns
normally — it never raises into the scheduler loop.

Backfillable for the current day: ``python main.py --job publish_reports``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def run() -> None:
    """Build today's daily bundle and publish it to the reports repo."""
    logger.info("=== PUBLISH-REPORTS JOB STARTING ===")

    if not settings.HERMES_PUBLISH_ENABLED:
        logger.info("publish_reports: HERMES_PUBLISH_ENABLED is false — skipping")
        return

    target_date = datetime.now(_ET).date()

    try:
        from api.daily_bundle import build_daily_bundle

        content = build_daily_bundle(
            target_date=target_date,
            db_path=str(settings.DATABASE_PATH),
            snapshots_dir=str(settings.SNAPSHOTS_DIR),
            log_dir=settings.STRUCTURED_LOG_DIR,
        )
    except Exception:
        logger.exception("publish_reports: failed to build daily bundle — skipping publish")
        return

    repo_path = f"daily/{target_date.isoformat()}.md"
    message = f"Daily evaluation bundle for {target_date.isoformat()}"

    try:
        from data.report_publisher import publish_report

        ok = publish_report(repo_path, content, message=message)
    except Exception:
        # publish_report is contracted never to raise, but belt-and-suspenders:
        # the publish step must never crash the scheduler.
        logger.exception("publish_reports: publish_report raised unexpectedly")
        ok = False

    if ok:
        logger.info("publish_reports: published %s", repo_path)
    else:
        logger.warning("publish_reports: did not publish %s (see prior logs)", repo_path)

    logger.info("=== PUBLISH-REPORTS JOB COMPLETE ===")
