"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.15.3"
VERSION_DATE = "2026-05-14"
VERSION_NOTES = (
    "Bug-fix patch: alpaca-py UUID order IDs coerced to str before SQLite/JSONL persistence; "
    "post_market option NTA activity codes corrected to OPEXP/OPASN/OPEXC; /api/decisions/stats "
    "InterfaceError fixed by switching to per-request SQLite connections."
)
