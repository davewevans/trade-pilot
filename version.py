"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.0.0"
VERSION_DATE = "2026-04-12"
VERSION_NOTES = (
    "Initial release — wheel strategy, four spread strategies, "
    "circuit breaker, guardrails, dashboard with auth."
)
