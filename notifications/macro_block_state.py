"""Edge-triggered notifications for the macro event block.

Compares the current is_blocked() result against the last-observed status from
data/snapshots/macro_block_state.json. Fires `notifications.notify(...)` on
transition (clear → blocked or blocked → clear). Silent on unchanged state and
on first run (no state file).

All exceptions are swallowed. A failed notification or state read must never
break the calling job.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# State file lives alongside other snapshots so the existing snapshot retention /
# backup logic covers it.
_STATE_FILE_NAME = "macro_block_state.json"


def _state_path() -> Path:
    """Resolve the state file path lazily so test patches of settings take effect."""
    from config import settings
    return settings.SNAPSHOTS_DIR / _STATE_FILE_NAME


def _load_state() -> dict | None:
    """Return the last persisted state dict, or None if file is missing/unreadable."""
    path = _state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("macro_block_state: failed to read %s", path, exc_info=True)
        return None


def _save_state(state: dict) -> None:
    """Persist current state. Atomic write via tmp + rename."""
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.warning("macro_block_state: failed to write %s", path, exc_info=True)


def check_and_notify(now_et: datetime) -> None:
    """Detect block-status transitions and fire a single ntfy on edge.

    Behavior:
      - First run (no state file): writes current state silently. No notification.
      - Status unchanged AND reason unchanged: writes state silently (timestamp refresh).
      - Status changed (clear ↔ blocked): notify, then write new state.
      - Status still blocked but reason changed (e.g., FOMC cluster transitions
        to NFP cluster mid-week): notify with reason-change message, then write.

    Honors MACRO_BLOCK_NOTIFY_ENABLED — when false, no state is written and no
    notification fires. The next time the flag is true, the run is treated as
    a first-run (silent) and behavior resumes from there.

    Never raises.
    """
    try:
        from config import settings
        if not settings.MACRO_BLOCK_NOTIFY_ENABLED:
            return

        from data.macro_calendar import is_blocked, next_clear_session

        blocked, reason = is_blocked(now_et)
        current = {
            "status": "BLOCKED" if blocked else "CLEAR",
            "reason": reason or "",
            "checked_at": now_et.isoformat(),
        }

        prior = _load_state()

        # First run — silent baseline write.
        if prior is None:
            _save_state(current)
            logger.info(
                "macro_block_state: first-run baseline written (status=%s)",
                current["status"],
            )
            return

        prior_status = prior.get("status")
        prior_reason = prior.get("reason", "")

        # Same status AND same reason → just refresh timestamp.
        if prior_status == current["status"] and prior_reason == current["reason"]:
            _save_state(current)
            return

        # Edge detected — build a notification.
        from notifications import notify

        if current["status"] == "BLOCKED" and prior_status == "CLEAR":
            clear_date = next_clear_session(now_et)
            clear_str = clear_date.isoformat() if clear_date else "unknown"
            notify(
                "high",
                "Macro block ACTIVE",
                f"{reason}\nNext clear session: {clear_str}\n"
                f"Management cycles still run; only new entries are blocked.",
                tags=["macro_block", "block_enter"],
            )
            logger.info(
                "macro_block_state: notified BLOCK ENTER (reason=%s, next_clear=%s)",
                reason, clear_str,
            )
        elif current["status"] == "CLEAR" and prior_status == "BLOCKED":
            notify(
                "high",
                "Macro block CLEARED",
                f"Prior block: {prior_reason}\nNormal entries resume this session.",
                tags=["macro_block", "block_exit"],
            )
            logger.info("macro_block_state: notified BLOCK EXIT (prior=%s)", prior_reason)
        else:
            # Same status (BLOCKED), different reason — cluster transition.
            clear_date = next_clear_session(now_et)
            clear_str = clear_date.isoformat() if clear_date else "unknown"
            notify(
                "high",
                "Macro block reason changed",
                f"Was: {prior_reason}\nNow: {reason}\nNext clear session: {clear_str}",
                tags=["macro_block", "reason_change"],
            )
            logger.info(
                "macro_block_state: notified REASON CHANGE (%s → %s)",
                prior_reason, reason,
            )

        _save_state(current)

    except Exception:
        # Never propagate. A failed notification must not break market_open.
        logger.exception("macro_block_state.check_and_notify failed (non-fatal)")
