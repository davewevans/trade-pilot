"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.9.2"
VERSION_DATE = "2026-04-20"
VERSION_NOTES = (
    "Fixes market_open crash at market open caused by missing pandas-market-calendars "
    "dependency; import now guarded inside try block for graceful fallback."
)
