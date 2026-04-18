"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.6.0"
VERSION_DATE = "2026-04-18"
VERSION_NOTES = (
    "9-phase ORATS quota hardening: process singleton lock, API usage ledger with rolling caps, "
    "pre-flight budget checks, structured call logging, ntfy threshold alerts, admin kill switch, "
    "and sweep resume safety — following the 2026-04-17 incident that exhausted the monthly budget."
)
