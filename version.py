"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.12.2"
VERSION_DATE = "2026-04-25"
VERSION_NOTES = (
    "Safety patch: bear call spread guardrail now rejects defensively when ex-dividend data fetch "
    "fails (yfinance quoteSummary 404 on SPY/QQQ/IWM/GLD) instead of silently passing."
)
