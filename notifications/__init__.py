"""
Notification layer. Severity-routed, pluggable backends.

severity:
  "critical" → ntfy (immediate) + JSONL append
  "warning"  → JSONL append only
  "info"     → JSONL append only

Non-blocking. All exceptions swallowed. A failed notification must
never break the calling code.
"""

import logging

logger = logging.getLogger(__name__)


def notify(severity: str, title: str, message: str, *, tags: list[str] | None = None) -> None:
    """Route a notification to appropriate backends based on severity.

    Args:
        severity: "critical", "warning", or "info"
        title: Short description of the event
        message: Full message body
        tags: Optional list of tag strings for filtering/routing
    """
    from notifications.ntfy_backend import NtfyBackend
    from notifications.digest import append as digest_append

    try:
        digest_append(severity, title, message, tags or [])
    except Exception:
        pass  # digest failure must not propagate

    if severity == "critical":
        try:
            NtfyBackend().send(title, message, tags or [], priority=5)
        except Exception:
            pass  # ntfy failure must not propagate
