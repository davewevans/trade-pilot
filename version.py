"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.13.1"
VERSION_DATE = "2026-04-26"
VERSION_NOTES = (
    "Prompt patch: tightened LONG_STOCK sell-the-shares triggers in both wheel prompts "
    "(2% SMA buffer, major-firm downgrade filter with 30-day window, effective_cost_basis "
    "typo fix) and updated the Strategies page to match."
)
