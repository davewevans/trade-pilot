"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.3.0"
VERSION_DATE = "2026-04-15"
VERSION_NOTES = (
    "Iron Butterfly and Calendar Spread strategies, account configuration system, "
    "strategy definition JSON files, startup reconciler, fill quality and NTA events "
    "endpoints, option snapshot batching, strategy param injection into prompts, and "
    "ORATS cache migration to SQLite for restart resilience."
)
