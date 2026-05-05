"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.15.0"
VERSION_DATE = "2026-05-05"
VERSION_NOTES = (
    "Observability foundation: structured log capture fixed (TimedRotatingFileHandler baseFilename "
    "bug), Sentry wired to both scheduler and API processes with LoggingIntegration, and 19 "
    "boolean env vars hardened against silent-disable on empty string via new _env_bool() helper."
)
