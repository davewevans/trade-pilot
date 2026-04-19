"""Tests for data.api_ledger — API usage ledger and hard cap enforcement."""

import time
import threading
from pathlib import Path

import pytest

from data.api_ledger import ApiLedger, OratsQuotaExceeded, _set_ledger_for_testing


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_ledger(tmp_path: Path) -> ApiLedger:
    return ApiLedger(tmp_path / "test.db")


def _set_caps(settings, monthly=99999, daily=99999, minute=99999, api="orats_historical"):
    """Override settings caps for a given api bucket."""
    if api == "orats_historical":
        settings.ORATS_HISTORICAL_MONTHLY_CAP = monthly
        settings.ORATS_HISTORICAL_DAILY_CAP = daily
        settings.ORATS_HISTORICAL_MINUTE_CAP = minute
    else:
        settings.ORATS_LIVE_MONTHLY_CAP = monthly
        settings.ORATS_LIVE_DAILY_CAP = daily
        settings.ORATS_LIVE_MINUTE_CAP = minute


# ---------------------------------------------------------------------------
# case 1: check_and_reserve passes when usage is below all caps
# ---------------------------------------------------------------------------


def test_check_and_reserve_passes_when_under_all_caps(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 10)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 10)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 10)

    ledger = make_ledger(tmp_path)
    # Should not raise
    ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)


# ---------------------------------------------------------------------------
# case 2: monthly cap enforced
# ---------------------------------------------------------------------------


def test_monthly_cap_enforced(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 3)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)

    ledger = make_ledger(tmp_path)
    # Record 3 billable calls
    for _ in range(3):
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    with pytest.raises(OratsQuotaExceeded) as exc_info:
        ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    assert exc_info.value.reason == "monthly_cap"
    assert exc_info.value.usage["month_used"] == 3


# ---------------------------------------------------------------------------
# case 3: daily_cap and minute_cap enforced
# ---------------------------------------------------------------------------


def test_daily_cap_enforced(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 2)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)

    ledger = make_ledger(tmp_path)
    for _ in range(2):
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    with pytest.raises(OratsQuotaExceeded) as exc_info:
        ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    assert exc_info.value.reason == "daily_cap"


def test_minute_cap_enforced(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 2)

    ledger = make_ledger(tmp_path)
    for _ in range(2):
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    with pytest.raises(OratsQuotaExceeded) as exc_info:
        ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    assert exc_info.value.reason == "minute_cap"


# ---------------------------------------------------------------------------
# case 4: blocked attempts are recorded with blocked_reason set
# ---------------------------------------------------------------------------


def test_blocked_attempts_recorded(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 1)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)

    ledger = make_ledger(tmp_path)
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    with pytest.raises(OratsQuotaExceeded):
        ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    # Verify blocked row was inserted
    row = ledger._conn.execute(
        "SELECT blocked_reason FROM api_usage_ledger WHERE blocked_reason IS NOT NULL"
    ).fetchone()
    assert row is not None
    assert row[0] == "monthly_cap"


# ---------------------------------------------------------------------------
# case 5: cache hits don't count toward caps
# ---------------------------------------------------------------------------


def test_cache_hits_do_not_consume_budget(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 2)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)

    ledger = make_ledger(tmp_path)
    usage_before = ledger.get_usage("orats_historical")

    # Record 5 cache hits — should not affect month_used
    for _ in range(5):
        ledger.record("orats_historical", "hist/summaries", "AAPL", True, None, None, None)

    usage_after = ledger.get_usage("orats_historical")
    assert usage_after["month_used"] == usage_before["month_used"] == 0

    # check_and_reserve should still pass (cap=2, used=0)
    ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)


# ---------------------------------------------------------------------------
# case 6: separate buckets — historical rows don't trigger live cap
# ---------------------------------------------------------------------------


def test_historical_does_not_affect_live_bucket(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_LIVE_MONTHLY_CAP", 1)
    monkeypatch.setattr(settings, "ORATS_LIVE_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_LIVE_MINUTE_CAP", 99999)

    ledger = make_ledger(tmp_path)
    # Insert 14000 historical rows (above the live monthly cap of 1)
    for _ in range(100):
        ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    # Live cap should still be unaffected
    ledger.check_and_reserve("orats_live", "summaries", "AAPL", None)  # should not raise


# ---------------------------------------------------------------------------
# case 7: month window starts at 1st of current month 00:00 UTC
# ---------------------------------------------------------------------------


def test_month_window_starts_at_first_of_month(tmp_path):
    from datetime import datetime, timezone

    ledger = make_ledger(tmp_path)
    now = time.time()
    month_start = ApiLedger._month_start_ts(now)

    dt = datetime.fromtimestamp(month_start, tz=timezone.utc)
    assert dt.day == 1
    assert dt.hour == 0
    assert dt.minute == 0
    assert dt.second == 0

    # A row with ts = month_start - 1 should NOT be in this month's count
    ledger._insert("orats_historical", "hist/summaries", month_start - 1, "AAPL",
                   False, 200, 50, None, None)
    count = ledger._count_window("orats_historical", month_start)
    assert count == 0

    # A row with ts = month_start should be counted
    ledger._insert("orats_historical", "hist/summaries", month_start + 1, "AAPL",
                   False, 200, 50, None, None)
    count = ledger._count_window("orats_historical", month_start)
    assert count == 1


# ---------------------------------------------------------------------------
# case 8: day window is rolling 24 hours (not calendar day)
# ---------------------------------------------------------------------------


def test_day_window_is_rolling_24_hours(tmp_path):
    """Day window uses rolling 24h (ts >= now - 86400), not a calendar day.

    This is intentional: weekly_research sweeps can span midnight and we
    don't want a window reset to block a sweep that started before midnight.
    """
    ledger = make_ledger(tmp_path)
    now = time.time()

    # Row from 25 hours ago — outside the rolling 24h window
    ledger._insert("orats_historical", "hist/summaries", now - 90000, "AAPL",
                   False, 200, 50, None, None)
    count = ledger._count_window("orats_historical", now - 86400)
    assert count == 0

    # Row from 23 hours ago — inside the rolling 24h window
    ledger._insert("orats_historical", "hist/summaries", now - 82800, "AAPL",
                   False, 200, 50, None, None)
    count = ledger._count_window("orats_historical", now - 86400)
    assert count == 1


# ---------------------------------------------------------------------------
# case 9: minute window is rolling 60 seconds
# ---------------------------------------------------------------------------


def test_minute_window_is_rolling_60_seconds(tmp_path):
    ledger = make_ledger(tmp_path)
    now = time.time()

    # Row from 61 seconds ago — outside the window
    ledger._insert("orats_historical", "hist/summaries", now - 61, "AAPL",
                   False, 200, 50, None, None)
    count = ledger._count_window("orats_historical", now - 60)
    assert count == 0

    # Row from 59 seconds ago — inside the window
    ledger._insert("orats_historical", "hist/summaries", now - 59, "AAPL",
                   False, 200, 50, None, None)
    count = ledger._count_window("orats_historical", now - 60)
    assert count == 1


# ---------------------------------------------------------------------------
# case 10: concurrent writes from two threads don't corrupt counts
# ---------------------------------------------------------------------------


def test_concurrent_writes_correct_count(tmp_path):
    ledger = make_ledger(tmp_path)
    n = 50

    def write_n():
        for _ in range(n):
            ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    t1 = threading.Thread(target=write_n)
    t2 = threading.Thread(target=write_n)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    usage = ledger.get_usage("orats_historical")
    assert usage["month_used"] == n * 2
