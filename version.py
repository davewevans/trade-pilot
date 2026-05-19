"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.15.4"
VERSION_DATE = "2026-05-19"
VERSION_NOTES = (
    "Bug-fix patch: cross-account context contamination in ContextBuilder fixed by "
    "constructing one builder per strategy with its own broker and account_id, and by "
    "adding strict-equality account_id filters to all journal read helpers."
)
