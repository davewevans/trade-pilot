"""
Notification layer. Severity-routed, pluggable backends.

severity:
  "critical" → ntfy priority 5 (breaks DND) + JSONL append
  "high"     → ntfy priority 4 + JSONL append
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

    if severity in ("critical", "high"):
        # Suppress ntfy push notifications on local dev machines so only the
        # production instance (Render) sends phone alerts.
        # Set ALLOW_LOCAL_NOTIFY=1 to override during local testing.
        from config import settings as _s
        _is_local = not getattr(_s, "RENDER", False)
        _notify_allowed = not _is_local or bool(__import__("os").environ.get("ALLOW_LOCAL_NOTIFY"))
        if _notify_allowed:
            try:
                _priority = 5 if severity == "critical" else 4
                NtfyBackend().send(title, message, tags or [], priority=_priority)
            except Exception:
                pass  # ntfy failure must not propagate
        else:
            logger.debug(
                "ntfy suppressed (local dev): [%s] %s", severity, title
            )
