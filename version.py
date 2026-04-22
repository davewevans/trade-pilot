"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.11.2"
VERSION_DATE = "2026-04-22"
VERSION_NOTES = (
    "Fixes a stale scheduler lock bug that caused the market-open job to be silently skipped "
    "after a Render container restart, and fixes ORATS hist/ivrank 404 errors caused by "
    "requesting today's unsettled trading date."
)
