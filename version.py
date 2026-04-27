"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.13.2"
VERSION_DATE = "2026-04-27"
VERSION_NOTES = (
    "Reliability fixes: SQLite retry on api_ledger writes, exponential backoff before "
    "yfinance fallbacks for FRED VIX and Alpaca corp-actions, cycle-summary empty-state "
    "UX correction, and boot-time diagnostic logging for structured-log handler and scheduler."
)
