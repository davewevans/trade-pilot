"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.7.0"
VERSION_DATE = "2026-04-19"
VERSION_NOTES = (
    "Adds How Evaluations Work instructional Learn page documenting the monthly scoring pipeline, "
    "flag detection thresholds, spot-check workflow, and operator review process; "
    "updates Python runtime to 3.14.2."
)
