"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.15.1"
VERSION_DATE = "2026-05-06"
VERSION_NOTES = (
    "Bug-fix patch: paper_N account routing 500s fixed in /api/decisions/stats and six related "
    "endpoints; ORATS cache InterfaceError given forensic logging; compute_ev_score guarded "
    "against stale-cache non-dict monies rows; calendar_spread trading-path priority documented."
)
