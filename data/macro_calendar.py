"""Tier 1 macro event calendar — FOMC, CPI, and NFP.

Data sources:
  - FOMC/CPI: data/macro_events.json (operator-maintained)
  - NFP: programmatically generated (first Friday of each month)

All datetime inputs must be ET-aware. Functions raise ValueError for naive datetimes.
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_FILE = Path(__file__).resolve().parent / "macro_events.json"


def load_events() -> list[dict]:
    """Load Tier 1 events from data/macro_events.json.

    Returns [] if file is missing or unparseable (logs warning).
    """
    if not _DATA_FILE.exists():
        logger.warning(
            "macro_events.json not found at %s — FOMC/CPI blocking disabled until populated",
            _DATA_FILE,
        )
        return []
    try:
        data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
        return data.get("events", [])
    except Exception as exc:
        logger.warning("Failed to parse macro_events.json: %s", exc)
        return []


def generate_nfp_dates(year: int) -> list[dict]:
    """Generate all NFP dates for a given year (first Friday of each month, 8:30 AM ET)."""
    result = []
    for month in range(1, 13):
        for day in range(1, 8):
            if datetime(year, month, day).weekday() == 4:  # Friday
                d = date(year, month, day)
                result.append({
                    "type": "NFP",
                    "date": d.isoformat(),
                    "time_et": "08:30",
                    "description": f"Non-Farm Payrolls ({d.strftime('%B %Y')})",
                })
                break
    return result


def all_tier_1_events(now_et: datetime) -> list[dict]:
    """Merge FOMC+CPI from file with generated NFP for current and next year.

    Filters to events with date >= today. Sorted ascending by date+time.
    """
    if now_et.tzinfo is None:
        raise ValueError("now_et must be timezone-aware")

    today_str = now_et.strftime("%Y-%m-%d")
    year = now_et.year

    events = load_events() + generate_nfp_dates(year) + generate_nfp_dates(year + 1)
    events = [e for e in events if e.get("date", "") >= today_str]
    events.sort(key=lambda e: (e.get("date", ""), e.get("time_et", "00:00")))
    return events


def next_event(now_et: datetime) -> dict | None:
    """Return the nearest upcoming Tier 1 event within 30 days, with extra fields.

    Extra fields added to the returned dict:
      - hours_until (float): hours from now to the event
      - is_today (bool): event is on today's session date
      - is_next_trading_day (bool): event is on the next NYSE trading day

    Returns None if no event within 30 days.
    """
    if now_et.tzinfo is None:
        raise ValueError("now_et must be timezone-aware")

    _, next_td = _get_session_pair(now_et)
    cutoff = now_et + timedelta(days=30)

    for event in all_tier_1_events(now_et):
        event_dt = _event_datetime(event)
        if event_dt <= now_et:
            continue
        if event_dt > cutoff:
            break

        event_date = date.fromisoformat(event["date"])
        hours_until = (event_dt - now_et).total_seconds() / 3600
        is_today = event["date"] == now_et.strftime("%Y-%m-%d")
        is_next_td = (event_date == next_td) if next_td is not None else False

        return {
            **event,
            "hours_until": round(hours_until, 1),
            "is_today": is_today,
            "is_next_trading_day": is_next_td,
        }

    return None


def is_blocked(now_et: datetime) -> tuple[bool, str]:
    """Return (True, reason) if a Tier 1 event falls on the current or next trading session.

    Reason format: 'MACRO_EVENT_PROXIMITY: {type} on {date} at {time_et} ET'
    Returns (False, '') if no blocking event is found.

    Trading-day logic uses pandas_market_calendars (NYSE calendar).
    If today is a holiday, the current session is the next open day.
    """
    if now_et.tzinfo is None:
        raise ValueError("now_et must be timezone-aware")

    current_session, next_session = _get_session_pair(now_et)
    if current_session is None:
        return False, ""

    block_dates = {d for d in (current_session, next_session) if d is not None}

    for event in all_tier_1_events(now_et):
        try:
            event_date = date.fromisoformat(event["date"])
        except (ValueError, KeyError):
            continue
        if event_date in block_dates:
            reason = (
                f"MACRO_EVENT_PROXIMITY: {event['type']} on {event['date']} "
                f"at {event.get('time_et', 'TBD')} ET"
            )
            return True, reason

    return False, ""


def fomc_coverage_days(now_et: datetime) -> int:
    """Return days from now to the furthest future FOMC or CPI event in macro_events.json.

    Returns 0 if no future FOMC/CPI events exist (NFP is excluded — always generated).
    Used to drive the Macro Calendar health indicator in the dashboard.
    """
    if now_et.tzinfo is None:
        raise ValueError("now_et must be timezone-aware")

    today_str = now_et.strftime("%Y-%m-%d")
    tier1 = [
        e for e in load_events()
        if e.get("type") in ("FOMC", "CPI") and e.get("date", "") >= today_str
    ]
    if not tier1:
        return 0
    furthest = max(e["date"] for e in tier1)
    return (date.fromisoformat(furthest) - now_et.date()).days


# ── Internals ─────────────────────────────────────────────────────────────────


def _get_session_pair(now_et: datetime) -> tuple[date | None, date | None]:
    """Return (current_session_date, next_session_date) from the NYSE calendar.

    current_session: today if it's an NYSE trading day, else the next open day.
    next_session: the NYSE trading day immediately after current_session.
    Returns (None, None) if the calendar lookup fails.
    """
    import pandas_market_calendars as mcal

    today = now_et.date()
    look_ahead = today + timedelta(days=14)

    try:
        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(
            start_date=today.isoformat(),
            end_date=look_ahead.isoformat(),
        )
    except Exception as exc:
        logger.warning("pandas_market_calendars lookup failed: %s", exc)
        return None, None

    if len(schedule) < 2:
        return None, None

    open_dates = [ts.date() for ts in schedule.index]
    return open_dates[0], open_dates[1]


def _event_datetime(event: dict) -> datetime:
    """Convert an event dict's date + time_et to an ET-aware datetime."""
    from zoneinfo import ZoneInfo

    time_str = event.get("time_et", "00:00")
    h, m = (int(x) for x in time_str.split(":"))
    d = date.fromisoformat(event["date"])
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ZoneInfo("America/New_York"))
