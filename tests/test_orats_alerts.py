"""Tests for Phase 6: ntfy threshold alerts and quota-exceeded notifications."""

import time
from pathlib import Path
from unittest.mock import call, patch

import pytest

from data.api_ledger import ApiLedger, OratsQuotaExceeded


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_ledger(tmp_path: Path) -> ApiLedger:
    from database.db import Database
    db = Database(path=tmp_path / "test.db")
    db.init_schema()
    return ApiLedger(db.get_connection())


# ---------------------------------------------------------------------------
# case 1: monthly threshold alert fires at 50%
# ---------------------------------------------------------------------------


def test_monthly_threshold_fires_at_50_pct(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 100)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)

    with patch("notifications.notify") as mock_notify:
        # 49 calls: 49% — no threshold yet
        for _ in range(49):
            ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 10, None)
        mock_notify.assert_not_called()

        # 50th call: crosses 50%
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 10, None)

    called_titles = [c.args[1] for c in mock_notify.call_args_list]
    assert any("50%" in t for t in called_titles), f"Expected 50% alert; got: {called_titles}"


# ---------------------------------------------------------------------------
# case 2: each threshold fires at most once per month
# ---------------------------------------------------------------------------


def test_threshold_fires_only_once_per_month(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 100)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)

    with patch("notifications.notify") as mock_notify:
        # Drive past 50% three times worth of calls (150 total)
        # Cap is 100 so we won't actually hit the DB cap (check_and_reserve blocks first)
        # Use record() directly to bypass cap enforcement
        for _ in range(75):
            ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 10, None)

    # Count how many 50%-threshold alerts fired
    fifty_pct_calls = [
        c for c in mock_notify.call_args_list if "50%" in c.args[1]
    ]
    assert len(fifty_pct_calls) == 1, (
        f"50% threshold should fire exactly once; fired {len(fifty_pct_calls)} times"
    )


# ---------------------------------------------------------------------------
# case 3: quota-exceeded fires an immediate ntfy alert
# ---------------------------------------------------------------------------


def test_quota_exceeded_fires_ntfy(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 1)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    with patch("notifications.notify") as mock_notify:
        with pytest.raises(OratsQuotaExceeded):
            ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    assert mock_notify.called
    titles = [c.args[1] for c in mock_notify.call_args_list]
    assert any("exceeded" in t.lower() or "monthly_cap" in t for t in titles), (
        f"Expected quota-exceeded alert; got: {titles}"
    )


# ---------------------------------------------------------------------------
# case 4: minute_cap alerts are rate-limited (max one per 5 minutes)
# ---------------------------------------------------------------------------


def test_minute_cap_alert_rate_limited(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 1)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)
    # Use up the 1-call per-minute budget
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 10, None)

    with patch("notifications.notify") as mock_notify:
        # First minute_cap block → should fire alert
        with pytest.raises(OratsQuotaExceeded):
            ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

        first_call_count = mock_notify.call_count

        # Second block immediately → should be rate-limited (no second alert)
        with pytest.raises(OratsQuotaExceeded):
            ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    # Exactly one ntfy call total for the two minute_cap blocks
    assert mock_notify.call_count == first_call_count, (
        f"Expected {first_call_count} total alerts but got {mock_notify.call_count}"
    )


# ---------------------------------------------------------------------------
# case 5: multiple thresholds fire as usage climbs (50, 75, 90, 95)
# ---------------------------------------------------------------------------


def test_all_thresholds_fire_in_order(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 100)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)
    monkeypatch.setattr(settings, "SNAPSHOTS_DIR", tmp_path / "snapshots")

    ledger = make_ledger(tmp_path)

    fired_thresholds: list[int] = []

    def capture_notify(severity, title, message, **kwargs):
        for pct in (50, 75, 90, 95):
            if f"{pct}%" in title:
                fired_thresholds.append(pct)

    with patch("notifications.notify", side_effect=capture_notify):
        for _ in range(96):
            ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 10, None)

    # All four thresholds should have fired exactly once, in order
    assert fired_thresholds == [50, 75, 90, 95], (
        f"Expected thresholds [50, 75, 90, 95]; got {fired_thresholds}"
    )
