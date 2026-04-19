"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.7.1"
VERSION_DATE = "2026-04-19"
VERSION_NOTES = (
    "Updates the About page to replace the Three Accounts section with a six-strategy overview "
    "and corrects the Python version label from 3.11+ to 3.14.2."
)
