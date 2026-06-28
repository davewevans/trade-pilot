"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.15.5"
VERSION_DATE = "2026-06-28"
VERSION_NOTES = (
    "Adds the flag-gated Hermes daily-bundle publisher (commits the daily evaluation "
    "bundle to the reports repo, never crashing the scheduler), fixes Standard Wheel "
    "order misrouting so wheel orders execute on their own account, and pins both with "
    "regression tests."
)
