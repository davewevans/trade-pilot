"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.9.3"
VERSION_DATE = "2026-04-20"
VERSION_NOTES = (
    "Adds Sentry error monitoring to the FastAPI server and Healthchecks.io ping lifecycle "
    "(start/success/fail) to every scheduled job via safe_run."
)
