"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.15.2"
VERSION_DATE = "2026-05-07"
VERSION_NOTES = (
    "Bug-fix patch: stale ORATS cache cores-shape AttributeError fixed at boundary; ORATSCache "
    "atomic writes + defensive reads + corrupt-row cleanup CLI; /api/decisions/stats diagnostic "
    "upgrade; system.md confidence semantics pinned; data/heartbeat.json untracked."
)
