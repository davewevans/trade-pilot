"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.2.0"
VERSION_DATE = "2026-04-14"
VERSION_NOTES = (
    "Backtest Intelligence page, ORATS slippage model, context builder ORATS "
    "enrichment, SkipReasons/Strategies ORATS insights, synthesized learn docs "
    "(IV, non-directional strategies, theta decay), and Alpaca account key "
    "refactor to paper account numbering."
)
