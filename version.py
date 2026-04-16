"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.4.0"
VERSION_DATE = "2026-04-16"
VERSION_NOTES = (
    "Conservative Wheel strategy (shorter-DTE variant running in its own paper account); "
    "research layer with liquidity scoring, win-rate gating, recommendation engine, and weekly sweep; "
    "Claude API improvements — prompt caching instrumentation, structured outputs, adaptive thinking, A/B harness."
)
