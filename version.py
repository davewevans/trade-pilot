"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.8.0"
VERSION_DATE = "2026-04-19"
VERSION_NOTES = (
    "Wires the Turnover Wheel strategy end-to-end with its own broker account, context builder, "
    "advisor method, and corrected prompt files fixing a live context-key bug and several "
    "logic errors in the management prompts."
)
