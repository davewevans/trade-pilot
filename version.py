"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.3.1"
VERSION_DATE = "2026-04-15"
VERSION_NOTES = (
    "Screening Filters section on Account Detail pages — collapsible read-only display "
    "of entry criteria for each account's strategy."
)
