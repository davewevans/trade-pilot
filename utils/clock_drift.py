"""Clock drift detection — halt the bot if the host clock has drifted.

Checks the local system clock against Alpaca's /v2/clock endpoint at the
start of every scheduled job. If drift exceeds the configured threshold the
bot writes HALTED.lock and returns False, causing safe_run to skip the job.

Fails open on any network or parse failure: a transient blip must not halt
trading. The operator can disable the check entirely via CLOCK_DRIFT_HALT_ENABLED=false.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import settings

logger = logging.getLogger(__name__)


def fetch_reference_time_utc() -> datetime | None:
    """Return Alpaca's server time as a UTC datetime, or None on failure.

    Retries up to CLOCK_DRIFT_FETCH_MAX_RETRIES times with a short per-attempt
    timeout (CLOCK_DRIFT_FETCH_TIMEOUT_SECONDS). Never raises.
    """
    creds = settings.get_all_broker_credentials()
    if not creds:
        logger.warning("clock_drift: no broker credentials available for reference time fetch")
        return None

    api_key, secret_key = creds[0]
    url = f"{settings.ALPACA_TRADE_URL}/v2/clock"
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    timeout = settings.CLOCK_DRIFT_FETCH_TIMEOUT_SECONDS
    max_retries = settings.CLOCK_DRIFT_FETCH_MAX_RETRIES

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            ts_str = data.get("timestamp")
            if not ts_str:
                logger.warning("clock_drift: Alpaca /v2/clock response missing 'timestamp' field")
                return None
            return datetime.fromisoformat(ts_str).astimezone(timezone.utc)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(0.5)

    logger.warning(
        "clock_drift: failed to fetch reference time after %d attempts — last error: %s",
        max_retries, last_exc,
    )
    return None


def measure_clock_drift() -> tuple[float | None, str]:
    """Return (drift_seconds, error_message).

    drift_seconds > 0 means local clock is AHEAD of reference.
    drift_seconds < 0 means local clock is BEHIND reference.
    If drift_seconds is None, error_message explains why.

    Local time is captured AFTER the fetch completes to minimise roundtrip
    bias: any latency inflates the apparent drift, so we want the sample taken
    as close as possible to when the reference time was read by Alpaca.
    """
    reference = fetch_reference_time_utc()
    if reference is None:
        return None, "reference time fetch failed"

    local_utc = datetime.now(timezone.utc)
    drift = (local_utc - reference).total_seconds()
    return drift, ""


def _write_drift_halt_lock(job_name: str, drift_seconds: float) -> None:
    """Write data/HALTED.lock atomically if it does not already exist.

    Matches the JSON format and atomic-write pattern from circuit_breaker.py.
    """
    lock_path = settings.DATA_DIR / "HALTED.lock"
    if lock_path.exists():
        return  # already locked — do not overwrite

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "halted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "clock_drift",
        "reason": f"clock_drift_{drift_seconds:.1f}s",
        "job_that_detected": job_name,
        "drift_seconds": drift_seconds,
        "reference_source": "alpaca_v2_clock",
    }, indent=2)
    tmp_path = lock_path.with_name("HALTED.lock.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(str(tmp_path), str(lock_path))


def check_and_halt_on_drift(job_name: str) -> bool:
    """Run at the top of every scheduled job.

    Returns True if the job should proceed, False if it should exit.

    Behaviour:
    - Returns True immediately if CLOCK_DRIFT_HALT_ENABLED is false.
    - Fails open on fetch/parse errors (returns True, logs WARNING once).
    - Halts (returns False, writes HALTED.lock, logs CRITICAL, notifies) when
      abs(drift) strictly exceeds CLOCK_DRIFT_THRESHOLD_SECONDS. Exactly at the
      threshold is not a halt (> not >=).
    - Non-zero drift below threshold is logged at DEBUG only.
    """
    if not settings.CLOCK_DRIFT_HALT_ENABLED:
        return True

    drift, err = measure_clock_drift()

    if drift is None:
        # Fail open — transient network failure must not halt trading.
        return True

    threshold = settings.CLOCK_DRIFT_THRESHOLD_SECONDS

    if abs(drift) <= threshold:
        if drift != 0.0:
            logger.debug("clock_drift: job=%s drift=%.2fs (within threshold %ds)", job_name, drift, threshold)
        return True

    # Drift exceeds threshold — halt.
    logger.critical(
        "CLOCK DRIFT DETECTED — job=%s drift=%.2fs exceeds threshold %ds. "
        "Writing HALTED.lock. Fix the host clock and delete the lock to resume.",
        job_name, drift, threshold,
    )
    _write_drift_halt_lock(job_name, drift)
    try:
        from notifications import notify
        notify(
            "critical",
            "Clock drift halt",
            f"Job {job_name} detected {drift:.1f}s clock drift (threshold {threshold}s). "
            "Trading halted. Fix host clock and delete HALTED.lock to resume.",
            tags=["halt", "clock_drift"],
        )
    except Exception:
        pass
    return False
