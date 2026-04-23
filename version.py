"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.12.0"
VERSION_DATE = "2026-04-23"
VERSION_NOTES = (
    "Post-4/23 fix pack: squashes three silent P0 bugs (trade journal None crash, ORATS cache "
    "InterfaceError, option chain 500-contract cap breach), adds Finnhub free-tier short-circuit, "
    "bull_put_spread pre-check instrumentation, and two new observability features — structured "
    "JSONL log capture to disk and a single-button daily evaluation bundle download."
)
