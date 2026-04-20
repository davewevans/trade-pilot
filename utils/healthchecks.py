"""Healthchecks.io ping lifecycle helpers.

Three public functions — ping_start, ping_success, ping_fail — fire
the corresponding Healthchecks.io slug URLs for a named scheduler job.
All are no-ops when the per-job env var is absent or the global kill
switch is engaged.  Network failures are caught and logged at WARNING;
they never propagate to the caller.

Env vars
--------
HEALTHCHECKS_ENABLED          Global kill switch. Default "true". Set "false" to
                               silence all pings without removing per-job vars.
HC_PING_URL_<JOB_NAME_UPPER>  Base ping URL for the named job (e.g.
                               HC_PING_URL_MARKET_OPEN). Absent → no-op.
"""

import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

# Suppress connection-level chatter from urllib3 inside this module only.
logging.getLogger("urllib3").setLevel(logging.WARNING)

_TIMEOUT: int = 5          # seconds — connect + read
_REASON_MAX: int = 500     # chars — Healthchecks body cap
_VALID_JOB_RE = re.compile(r"^[A-Z0-9_]+$")


def _get_url(job_name: str) -> str | None:
    """Return the base ping URL for *job_name*, or None if pinging is disabled."""
    if os.environ.get("HEALTHCHECKS_ENABLED", "true").lower() == "false":
        return None

    suffix = job_name.upper()
    if not _VALID_JOB_RE.match(suffix):
        logger.warning(
            "healthchecks: job name %r maps to invalid env var suffix %r — skipping",
            job_name, suffix,
        )
        return None

    url = os.environ.get(f"HC_PING_URL_{suffix}", "").strip()
    return url if url else None


def ping_start(job_name: str) -> None:
    """Notify Healthchecks that *job_name* has started."""
    url = _get_url(job_name)
    if not url:
        return
    try:
        requests.get(f"{url}/start", timeout=_TIMEOUT)
    except Exception as exc:
        logger.warning("healthchecks: ping_start failed for %r: %s", job_name, exc)


def ping_success(job_name: str) -> None:
    """Notify Healthchecks that *job_name* completed successfully."""
    url = _get_url(job_name)
    if not url:
        return
    try:
        requests.get(url, timeout=_TIMEOUT)
    except Exception as exc:
        logger.warning("healthchecks: ping_success failed for %r: %s", job_name, exc)


def ping_fail(job_name: str, reason: str | None = None) -> None:
    """Notify Healthchecks that *job_name* failed.

    If *reason* is non-empty it is sent as the POST body (truncated to
    _REASON_MAX chars).  When absent or empty, a simple GET is used instead.
    """
    url = _get_url(job_name)
    if not url:
        return
    fail_url = f"{url}/fail"
    try:
        if reason:
            requests.post(fail_url, data=reason[:_REASON_MAX], timeout=_TIMEOUT)
        else:
            requests.get(fail_url, timeout=_TIMEOUT)
    except Exception as exc:
        logger.warning("healthchecks: ping_fail failed for %r: %s", job_name, exc)
