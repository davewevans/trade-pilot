"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.10.0"
VERSION_DATE = "2026-04-20"
VERSION_NOTES = (
    "Schedules the weekly_research job and adds a self-review extension to monthly_evaluation "
    "that uses Opus to propose prompt patches for flagged reasoning dimensions."
)
