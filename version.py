"""Single source of truth for the trade-pilot release version.

Bump VERSION + VERSION_DATE on each tagged release and add a matching
section to CHANGELOG.md. Other modules read this via ``settings.VERSION``
rather than importing this file directly.
"""

VERSION = "1.12.1"
VERSION_DATE = "2026-04-25"
VERSION_NOTES = (
    "IVR fix: routes iv_rank_1y from /ivrank and atm_iv_m* from /cores (both were silently null "
    "from /summaries since launch), activating iv_overvalued_label for the first time; drops skew_m2, "
    "adds exc_info to a silent spread context-build warning, and adds KNOWN_ISSUES.md tracker."
)
