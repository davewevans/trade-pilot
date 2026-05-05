"""Idempotent Sentry initialization for all trade-pilot processes.

Centralizes LoggingIntegration wiring and inbound event filters so every
process (scheduler, API server) gets identical Sentry configuration.

Usage::

    # In main.py (scheduler):
    from utils.sentry_setup import init_sentry
    init_sentry(process_role="scheduler")

    # In api/server.py (API server):
    from utils.sentry_setup import init_sentry
    init_sentry(process_role="api_server")
"""

from __future__ import annotations

import logging
import os

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

_initialized = False

# Known-benign warning patterns that would otherwise flood Sentry.
# MAINTENANCE NOTE: remove the ApiLedger and SQLite lines 24h after the
# single-connection-per-process fix (commit b6d169f) has been live with
# zero recurrence. They are transition band-aids, not permanent silencers.
_DROP_IF_CONTAINS: tuple[str, ...] = (
    # Finnhub free-tier — news_sentiment returns None on rate limit / no data.
    "news_sentiment returned None",
    # FRED VIX transient — covered by 3-attempt retry (commit 7797904).
    "vix returned None",
    # ORATS transient — covered by retries elsewhere.
    "orats_cores returned None",
    # SQLite contention transition band-aids (see MAINTENANCE NOTE above).
    "ApiLedger.record failed",
    "SQLite locked, retrying",
)


def _before_send(event: dict, hint: dict) -> dict | None:
    # Sentry uses "logentry".message for logging-originated events and
    # top-level "message" for other envelope types; check both to ensure
    # filters apply regardless of how the event was captured.
    msg = (
        (event.get("logentry") or {}).get("message", "")
        or event.get("message", "")
        or ""
    )
    for needle in _DROP_IF_CONTAINS:
        if needle in msg:
            return None
    tags = event.setdefault("tags", {})
    tags.setdefault("job", os.environ.get("TRADE_PILOT_JOB_NAME", "unknown"))
    tags.setdefault("process_role", os.environ.get("TRADE_PILOT_PROCESS_ROLE", "unknown"))
    return event


def init_sentry(*, process_role: str) -> None:
    """Idempotent Sentry init. Safe to call from multiple entry points."""
    global _initialized
    if _initialized:
        return
    from config import settings
    if not settings.SENTRY_DSN:
        return
    os.environ.setdefault("TRADE_PILOT_PROCESS_ROLE", process_role)
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment="production" if settings.RENDER else "development",
        release=settings.VERSION,
        traces_sample_rate=0.1,
        send_default_pii=False,
        integrations=[
            LoggingIntegration(
                level=logging.INFO,           # INFO+ → breadcrumbs
                event_level=logging.WARNING,  # WARNING+ → Sentry events
            ),
        ],
        before_send=_before_send,
    )
    _initialized = True
    logging.getLogger(__name__).info(
        "Sentry initialised (release=%s, role=%s)", settings.VERSION, process_role
    )
