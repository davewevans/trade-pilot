"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.13.3"
VERSION_DATE = "2026-04-27"
VERSION_NOTES = (
    "Recommendations page fixes: per-row Accept/Reject now works correctly (recommendation_id "
    "was missing from the snapshot, causing all buttons to activate together), removed the "
    "always-active Undecided button, and added a legend explaining ADD/REMOVE/HOLD/SKIP labels."
)
