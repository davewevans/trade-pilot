"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.11.1"
VERSION_DATE = "2026-04-21"
VERSION_NOTES = (
    "Fixes blank dashboard account cards and detail-page data duplication caused by an "
    "incomplete multi-account migration from legacy three-account names to paper_1–paper_6."
)
