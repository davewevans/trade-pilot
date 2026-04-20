"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.9.0"
VERSION_DATE = "2026-04-19"
VERSION_NOTES = (
    "Adds broker-truth state reconciliation (boot-time and 5-minute drop-copy) and fill realism "
    "measurement (shadow execution capturing NBBO snapshots at +30s/+2m/+15m/EOD) with a "
    "per-strategy fill-realism score surfaced on the Strategy Health page."
)
