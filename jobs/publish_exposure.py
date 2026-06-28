"""Cross-account exposure-snapshot publish job — runs twice on trading days.

Builds the consolidated cross-account exposure snapshot (per-account positions
with sectors + the book-level family view) and commits it to the private reports
repo that the Hermes advisory risk monitor reads. Publishes a dated snapshot
(``exposure/<YYYY-MM-DD-HHMM>.json``) plus ``exposure/latest.json`` so Hermes can
always read the newest without listing the directory.

Scheduled ~10:15 ET (after the entry cycle settles) and ~16:15 ET (after close)
on weekdays. Flag-gated on ``HERMES_PUBLISH_ENABLED`` (reuses the Ticket B
publisher); a no-op when the flag is off. Publishing is data exhaust: any
failure is logged and the job returns normally — it never raises into the
scheduler loop, so a GitHub outage cannot affect trading.

Backfillable: ``python main.py --job publish_exposure``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def run() -> None:
    """Build the cross-account exposure snapshot and publish it."""
    logger.info("=== PUBLISH-EXPOSURE JOB STARTING ===")

    if not settings.HERMES_PUBLISH_ENABLED:
        logger.info("publish_exposure: HERMES_PUBLISH_ENABLED is false — skipping")
        return

    now_et = datetime.now(_ET)

    try:
        from data.exposure_snapshot import build_exposure_snapshot

        snapshot = build_exposure_snapshot()
        content = json.dumps(snapshot, indent=2, default=str)
    except Exception:
        logger.exception("publish_exposure: failed to build exposure snapshot — skipping publish")
        return

    dated_path = f"exposure/{now_et.strftime('%Y-%m-%d-%H%M')}.json"
    latest_path = "exposure/latest.json"
    message = f"Exposure snapshot {now_et.strftime('%Y-%m-%d %H:%M')} ET"

    try:
        from data.report_publisher import publish_report

        dated_ok = publish_report(dated_path, content, message=message)
        latest_ok = publish_report(latest_path, content, message=f"{message} (latest)")
    except Exception:
        # publish_report is contracted never to raise, but belt-and-suspenders:
        # the publish step must never crash the scheduler.
        logger.exception("publish_exposure: publish_report raised unexpectedly")
        dated_ok = latest_ok = False

    logger.info(
        "publish_exposure: dated=%s latest=%s (dated_path=%s)",
        dated_ok, latest_ok, dated_path,
    )
    logger.info("=== PUBLISH-EXPOSURE JOB COMPLETE ===")
