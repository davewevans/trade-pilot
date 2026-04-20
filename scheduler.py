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

from utils.clock_drift import check_and_halt_on_drift
from utils.healthchecks import ping_fail, ping_start, ping_success

from jobs import (
    drop_copy_reconcile,
    expiry_guard,
    market_close,
    market_open,
    monthly_evaluation,
    portfolio_refresh,
    position_check,
    post_market,
    pre_close,
    pre_market,
    shadow_capture,
    weekly_report,
    weekly_research,
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
    scheduler loop keeps running.  A "critical" notification is fired
    on job crash so the operator is alerted immediately.

    A clock-drift pre-flight runs before the job body. If the host clock
    has drifted beyond the configured threshold the job is skipped and
    HALTED.lock is written. The check fails open on network errors.
    """
    if not check_and_halt_on_drift(job_name):
        logger.info("=== SKIPPING: %s — clock drift halt active ===", job_name)
        return

    try:
        ping_start(job_name)
    except Exception:
        logger.warning("healthchecks: ping_start wrapper error for %s", job_name)

    logger.info("=== STARTING: %s ===", job_name)
    t0 = time.monotonic()
    try:
        job_fn()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        logger.exception("=== FAILED: %s ===", job_name)
        try:
            ping_fail(job_name, reason=tb[:500])
        except Exception:
            logger.warning("healthchecks: ping_fail wrapper error for %s", job_name)
        try:
            from notifications import notify
            notify(
                "critical",
                f"Job crashed: {job_name}",
                tb[:500],
                tags=["crash"],
            )
        except Exception:
            pass  # notification failure must not cascade
    else:
        elapsed = time.monotonic() - t0
        logger.info("=== COMPLETED: %s in %.1fs ===", job_name, elapsed)
        # Write heartbeat so the API can detect scheduler staleness.
        try:
            import json as _json
            from config import settings as _cfg
            _hb = {
                "ts": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"),
                "job": job_name,
                "elapsed_s": round(elapsed, 1),
            }
            _hb_path = _cfg.DATA_DIR / "heartbeat.json"
            _tmp = _hb_path.with_suffix(".tmp")
            _tmp.write_text(_json.dumps(_hb, indent=2), encoding="utf-8")
            import os as _os
            _os.replace(str(_tmp), str(_hb_path))
        except Exception:
            pass  # heartbeat write failure must not break the scheduler
        try:
            ping_success(job_name)
        except Exception:
            logger.warning("healthchecks: ping_success wrapper error for %s", job_name)


def _weekday_run(job_fn, job_name: str) -> None:
    """Wrapper that skips execution on weekends."""
    if not is_weekday():
        logger.debug("Skipping %s — weekend", job_name)
        return
    safe_run(job_fn, job_name)


# ── schedule registration ──────────────────────────────────


def _monthly_eval_wrapper() -> None:
    """Run monthly_evaluation.run() only on the 1st of the month (ET).

    The job is registered as a daily 05:00 ET trigger so the schedule library
    fires it every day; this wrapper gate keeps it a no-op on days 2–31.
    """
    now = datetime.now(_ET_ZONE)
    if now.day != 1:
        logger.debug(
            "monthly_evaluation: skipping — today is day %d, not the 1st", now.day
        )
        return
    monthly_evaluation.run()


def _cleanup_orats_cache() -> None:
    from data.orats_cache import ORATSCache
    cache = ORATSCache()
    deleted = cache.clear_expired()
    logger.info("ORATS cache cleanup: %d expired entries removed", deleted)


def register_jobs() -> None:
    """Register every job with the ``schedule`` library.

    Job times and metadata live in ``config.SCHEDULE`` — the single source of
    truth for the bot's schedule.  This function maps job names to their
    callables and wires them up; it should not contain any hardcoded times.
    """
    from config import SCHEDULE

    _JOB_FN: dict[str, object] = {
        "pre_market": pre_market.run,
        "market_open": market_open.run,
        "position_check": position_check.run,
        "expiry_guard": expiry_guard.run,
        "pre_close": pre_close.run,
        "market_close": market_close.run,
        "post_market": post_market.run,
        "portfolio_refresh": portfolio_refresh.run,
        "drop_copy_reconcile": drop_copy_reconcile.run,
        "shadow_capture": shadow_capture.run,
        "weekly_report": weekly_report.run,
        "weekly_research": weekly_research.run,
        "monthly_evaluation": _monthly_eval_wrapper,
        "orats_cache_cleanup": _cleanup_orats_cache,
    }

    for entry in SCHEDULE:
        job_name = entry["job"]
        fn = _JOB_FN.get(job_name)
        if fn is None:
            logger.warning(
                "register_jobs: no callable mapped for job %r — skipping", job_name
            )
            continue

        job_type = entry["type"]
        tz = entry.get("tz", ET)

        if job_type == "weekday":
            schedule.every().day.at(entry["time"], tz=tz).do(
                _weekday_run, job_fn=fn, job_name=job_name,
            )
        elif job_type == "interval":
            schedule.every(entry["interval_minutes"]).minutes.do(
                _weekday_run, job_fn=fn, job_name=job_name,
            )
        elif job_type == "weekly":
            getattr(schedule.every(), entry["day"]).at(entry["time"], tz=tz).do(
                safe_run, job_fn=fn, job_name=job_name,
            )
        elif job_type == "daily":
            schedule.every().day.at(entry["time"], tz=tz).do(
                safe_run, job_fn=fn, job_name=job_name,
            )
        else:
            logger.warning(
                "register_jobs: unknown schedule type %r for job %r", job_type, job_name
            )


# ── standalone entry (prefer main.py instead) ──────────────

if __name__ == "__main__":
    # When run directly, defer to main.py so logging and startup
    # validation are applied consistently.
    from main import main

    main()
