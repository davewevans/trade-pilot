"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.11.0"
VERSION_DATE = "2026-04-21"
VERSION_NOTES = (
    "Adds a mobile-first /overview page for quick bot status checks, backed by a new "
    "/api/heartbeat endpoint and a composite 30s-polling hook."
)
