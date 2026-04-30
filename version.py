"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.14.1"
VERSION_DATE = "2026-04-28"
VERSION_NOTES = (
    "Fixes three daily bundle reporting bugs: circuit breaker status now reads from the correct "
    "file and key, source health staleness detection added (⚠ for sources silent >26 hours, "
    "expected on macro-blocked days), and market context reads from context.json instead of the "
    "wrong regime_history.json shape."
)
