"""Tests for historical cache TTL fix (Phase 12a).

Covers:
1. Cache entry for a past date returns hit regardless of fetched_at age
2. Cache entry for today with fetched_at > 1 hour ago returns miss
3. Cache entry for today with fetched_at < 1 hour ago returns hit
"""

import time
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, timedelta


# ---------------------------------------------------------------------------
# Helper: build an ORATSCache with a controlled in-memory or tmp SQLite DB
# ---------------------------------------------------------------------------

def _make_hist_cache(tmp_path):
    """Return an ORATSCache backed by a fresh SQLite DB in tmp_path."""
    from unittest.mock import MagicMock
    db = tmp_path / "hist_test.db"
    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = db
    mock_settings.RENDER = False
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = "0"

    from data.orats_cache import ORATSCache
    with patch("config.settings", mock_settings):
        cache = ORATSCache(db_path=db)
    # Ensure the orats_cache table exists
    cache._conn.execute("""
        CREATE TABLE IF NOT EXISTS orats_cache (
            endpoint   TEXT NOT NULL,
            cache_key  TEXT NOT NULL,
            data_json  TEXT NOT NULL,
            fetched_at REAL NOT NULL,
            ttl_seconds REAL NOT NULL,
            PRIMARY KEY (endpoint, cache_key)
        )
    """)
    cache._conn.commit()
    return cache


# ---------------------------------------------------------------------------
# Test 1: Past date → always-fresh regardless of how old the entry is
# ---------------------------------------------------------------------------

def test_past_date_is_always_fresh(tmp_path):
    """Historical data for a past date is always returned as a cache hit."""
    from data.orats_historical import _ttl_for_date, _IMMUTABLE_TTL

    past_date = (date.today() - timedelta(days=30)).isoformat()
    ttl = _ttl_for_date(past_date)

    assert ttl == _IMMUTABLE_TTL, (
        f"Expected IMMUTABLE_TTL ({_IMMUTABLE_TTL}) for past date, got {ttl}"
    )

    # Simulate a very old cache entry (1 year ago)
    cache = _make_hist_cache(tmp_path)
    one_year_ago = time.time() - 365 * 24 * 3600
    cache._conn.execute(
        "INSERT OR REPLACE INTO orats_cache VALUES (?, ?, ?, ?, ?)",
        ("hist/summaries", f"AAPL|{past_date}", '[{"iv_rank_1y": 45}]', one_year_ago, ttl),
    )
    cache._conn.commit()

    result = cache.get("hist/summaries", f"AAPL|{past_date}", ttl)
    assert result is not None, "Past-date entry should always be a cache hit"
    assert result[0]["iv_rank_1y"] == 45


# ---------------------------------------------------------------------------
# Test 2: Today's date + entry > 1 hour old → cache miss
# ---------------------------------------------------------------------------

def test_today_date_stale_entry_is_miss(tmp_path):
    """Cache entry for today fetched > 1 hour ago is treated as a miss."""
    from data.orats_historical import _ttl_for_date, _TODAY_TTL

    today = date.today().isoformat()
    ttl = _ttl_for_date(today)

    assert ttl == _TODAY_TTL, (
        f"Expected TODAY_TTL ({_TODAY_TTL}) for today's date, got {ttl}"
    )

    cache = _make_hist_cache(tmp_path)
    two_hours_ago = time.time() - 2 * 3600
    cache._conn.execute(
        "INSERT OR REPLACE INTO orats_cache VALUES (?, ?, ?, ?, ?)",
        ("hist/summaries", f"AAPL|{today}", '[{"iv_rank_1y": 55}]', two_hours_ago, ttl),
    )
    cache._conn.commit()

    result = cache.get("hist/summaries", f"AAPL|{today}", ttl)
    assert result is None, "Today's entry fetched > 1 hour ago should be a cache miss"


# ---------------------------------------------------------------------------
# Test 3: Today's date + entry < 1 hour old → cache hit
# ---------------------------------------------------------------------------

def test_today_date_fresh_entry_is_hit(tmp_path):
    """Cache entry for today fetched < 1 hour ago is a cache hit."""
    from data.orats_historical import _ttl_for_date, _TODAY_TTL

    today = date.today().isoformat()
    ttl = _ttl_for_date(today)

    cache = _make_hist_cache(tmp_path)
    thirty_min_ago = time.time() - 30 * 60
    cache._conn.execute(
        "INSERT OR REPLACE INTO orats_cache VALUES (?, ?, ?, ?, ?)",
        ("hist/summaries", f"AAPL|{today}", '[{"iv_rank_1y": 60}]', thirty_min_ago, ttl),
    )
    cache._conn.commit()

    result = cache.get("hist/summaries", f"AAPL|{today}", ttl)
    assert result is not None, "Today's entry fetched < 1 hour ago should be a cache hit"
    assert result[0]["iv_rank_1y"] == 60
