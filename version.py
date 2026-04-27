"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.14.0"
VERSION_DATE = "2026-04-27"
VERSION_NOTES = (
    "Adds calendar_spread and iron_butterfly to the backtest engine and full research pipeline, "
    "including watchlist recommender wiring, liquidity thresholds, sweep params, and kill-switch "
    "flags (both default off for staged activation)."
)
