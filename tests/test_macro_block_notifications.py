"""Tests for notifications.macro_block_state.check_and_notify."""

import json
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


def et(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ET)


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    """Patch settings.SNAPSHOTS_DIR to a tmp dir; return the state file path."""
    from config import settings
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path)
    monkeypatch.setattr(settings, "MACRO_BLOCK_NOTIFY_ENABLED", True)
    return tmp_path / "macro_block_state.json"


def test_first_run_writes_state_silently(state_path):
    """No prior state → write baseline, do NOT notify."""
    from notifications.macro_block_state import check_and_notify

    with patch("data.macro_calendar.is_blocked", return_value=(True, "FOMC on 2026-04-29")), \
         patch("data.macro_calendar.next_clear_session", return_value=None), \
         patch("notifications.notify") as mock_notify:
        check_and_notify(et(2026, 4, 28))

    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["status"] == "BLOCKED"
    mock_notify.assert_not_called()


def test_unchanged_status_does_not_notify(state_path):
    """Same status + same reason → no notification, just timestamp refresh."""
    from notifications.macro_block_state import check_and_notify

    state_path.write_text(json.dumps({
        "status": "BLOCKED",
        "reason": "MACRO_EVENT_PROXIMITY: NFP on 2026-05-01 at 08:30 ET",
        "checked_at": "2026-04-30T10:00:00-04:00",
    }))

    with patch(
        "data.macro_calendar.is_blocked",
        return_value=(True, "MACRO_EVENT_PROXIMITY: NFP on 2026-05-01 at 08:30 ET"),
    ), patch("notifications.notify") as mock_notify:
        check_and_notify(et(2026, 5, 1))

    mock_notify.assert_not_called()


def test_clear_to_blocked_fires_notification(state_path):
    from notifications.macro_block_state import check_and_notify

    state_path.write_text(json.dumps({
        "status": "CLEAR", "reason": "", "checked_at": "2026-04-27T10:00:00-04:00",
    }))

    with patch(
        "data.macro_calendar.is_blocked",
        return_value=(True, "MACRO_EVENT_PROXIMITY: FOMC on 2026-04-29 at 14:00 ET"),
    ), patch("data.macro_calendar.next_clear_session", return_value=date(2026, 5, 4)), \
         patch("notifications.notify") as mock_notify:
        check_and_notify(et(2026, 4, 28))

    assert mock_notify.called
    args = mock_notify.call_args
    assert args[0][0] == "high"
    assert "ACTIVE" in args[0][1]
    assert "FOMC" in args[0][2]
    assert "2026-05-04" in args[0][2]


def test_blocked_to_clear_fires_notification(state_path):
    from notifications.macro_block_state import check_and_notify

    state_path.write_text(json.dumps({
        "status": "BLOCKED",
        "reason": "MACRO_EVENT_PROXIMITY: NFP on 2026-05-01 at 08:30 ET",
        "checked_at": "2026-05-01T10:00:00-04:00",
    }))

    with patch("data.macro_calendar.is_blocked", return_value=(False, "")), \
         patch("notifications.notify") as mock_notify:
        check_and_notify(et(2026, 5, 4))

    assert mock_notify.called
    args = mock_notify.call_args
    assert args[0][0] == "high"
    assert "CLEARED" in args[0][1]


def test_disabled_flag_is_total_no_op(tmp_path, monkeypatch):
    """MACRO_BLOCK_NOTIFY_ENABLED=False → no state write, no notification."""
    from config import settings
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path)
    monkeypatch.setattr(settings, "MACRO_BLOCK_NOTIFY_ENABLED", False)

    from notifications.macro_block_state import check_and_notify

    with patch("data.macro_calendar.is_blocked", return_value=(True, "anything")), \
         patch("notifications.notify") as mock_notify:
        check_and_notify(et(2026, 5, 1))

    assert not (tmp_path / "macro_block_state.json").exists()
    mock_notify.assert_not_called()


def test_swallows_exceptions(state_path):
    """is_blocked raising must not propagate."""
    from notifications.macro_block_state import check_and_notify

    with patch("data.macro_calendar.is_blocked", side_effect=RuntimeError("boom")):
        # Must not raise.
        check_and_notify(et(2026, 5, 1))


def test_reason_change_while_blocked_fires_notification(state_path):
    """BLOCKED → BLOCKED with different reason fires a 'reason changed' message."""
    from notifications.macro_block_state import check_and_notify

    state_path.write_text(json.dumps({
        "status": "BLOCKED",
        "reason": "MACRO_EVENT_PROXIMITY: FOMC on 2026-04-29 at 14:00 ET",
        "checked_at": "2026-04-29T10:00:00-04:00",
    }))

    with patch(
        "data.macro_calendar.is_blocked",
        return_value=(True, "MACRO_EVENT_PROXIMITY: NFP on 2026-05-01 at 08:30 ET"),
    ), patch("data.macro_calendar.next_clear_session", return_value=date(2026, 5, 4)), \
         patch("notifications.notify") as mock_notify:
        check_and_notify(et(2026, 4, 30))

    assert mock_notify.called
    args = mock_notify.call_args
    assert args[0][0] == "high"
    assert "reason" in args[0][1].lower()
