"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.5.0"
VERSION_DATE = "2026-04-17"
VERSION_NOTES = (
    "Notification backends (ntfy/email) with severity routing; recommendation accuracy scorecard with "
    "live-outcome feedback loop; skip-reason telemetry with gate-breakdown dashboard; ORATS usage "
    "tracking; SQLite db_retry for write contention; EV-gated win-rate multiplier."
)
