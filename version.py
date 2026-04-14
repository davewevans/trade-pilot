"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.1.0"
VERSION_DATE = "2026-04-13"
VERSION_NOTES = (
    "Backtesting engine, ORATS expansion (9 endpoints), EV scoring, "
    "vol-of-vol classification, IV history chart, source health tracking, "
    "collapsible sidebar, and dashboard circuit breaker reset."
)
