"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.9.1"
VERSION_DATE = "2026-04-20"
VERSION_NOTES = (
    "Fixes NameError crash in ORATS IV history cache lookup (503 on every iv-history request) "
    "and adds the missing /api/backtest/history and /api/backtest/reality-check endpoints."
)
