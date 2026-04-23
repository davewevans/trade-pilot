"""Tests for the SQLite-backed ORATSCache."""

import json
import time
import sqlite3

import pytest

from data.orats_cache import ORATSCache


@pytest.fixture
def cache(tmp_path):
    """Return an ORATSCache backed by a temporary SQLite database."""
    db_path = tmp_path / "test_trade_pilot.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orats_cache (
            endpoint    TEXT NOT NULL,
            cache_key   TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            ttl_seconds REAL NOT NULL,
            PRIMARY KEY (endpoint, cache_key)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_orats_cache_endpoint ON orats_cache(endpoint)"
    )
    conn.commit()
    conn.close()

    c = ORATSCache(db_path=db_path)
    return c


def test_get_returns_none_on_miss(cache):
    result = cache.get("summaries", "AAPL", 1800)
    assert result is None


def test_set_then_get_returns_data(cache):
    data = {"iv_rank_1y": 42.5, "ticker": "AAPL"}
    cache.set("summaries", "AAPL", data, 1800)
    result = cache.get("summaries", "AAPL", 1800)
    assert result == data


def test_expired_entry_returns_none(cache):
    data = {"ticker": "MSFT"}
    cache.set("summaries", "MSFT", data, 0.001)  # 1 ms TTL
    time.sleep(0.05)
    result = cache.get("summaries", "MSFT", 0.001)
    assert result is None


def test_clear_expired_removes_only_expired_rows(cache):
    data_fresh = {"ticker": "AAPL"}
    data_stale = {"ticker": "MSFT"}

    cache.set("summaries", "AAPL", data_fresh, 3600)  # fresh
    cache.set("summaries", "MSFT", data_stale, 0.001)  # will expire
    time.sleep(0.05)

    deleted = cache.clear_expired()
    assert deleted >= 1

    # Fresh entry should still be there
    assert cache.get("summaries", "AAPL", 3600) == data_fresh
    # Stale entry should be gone
    assert cache.get("summaries", "MSFT", 0.001) is None


def test_corrupt_json_returns_none_and_deletes_row(cache):
    # Directly insert a corrupt row into the database
    cache._conn.execute(
        """INSERT OR REPLACE INTO orats_cache
           (endpoint, cache_key, data_json, fetched_at, ttl_seconds)
           VALUES (?, ?, ?, ?, ?)""",
        ("summaries", "CORRUPT", "not valid json{{", time.time(), 3600),
    )
    cache._conn.commit()

    result = cache.get("summaries", "CORRUPT", 3600)
    assert result is None

    # Row should be deleted
    row = cache._conn.execute(
        "SELECT 1 FROM orats_cache WHERE endpoint='summaries' AND cache_key='CORRUPT'"
    ).fetchone()
    assert row is None


def test_cache_with_in_memory_sqlite(tmp_path):
    """Cache works with a fresh temp database (similar to :memory: but file-backed)."""
    db_path = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orats_cache (
            endpoint    TEXT NOT NULL,
            cache_key   TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            ttl_seconds REAL NOT NULL,
            PRIMARY KEY (endpoint, cache_key)
        )"""
    )
    conn.commit()
    conn.close()

    c = ORATSCache(db_path=db_path)
    assert c.get("ivrank", "SPY", 1800) is None
    c.set("ivrank", "SPY", {"ivRank1y": 65.0}, 1800)
    assert c.get("ivrank", "SPY", 1800) == {"ivRank1y": 65.0}


def test_stats_returns_expected_shape(cache):
    cache.set("summaries", "AAPL", {"x": 1}, 3600)
    cache.set("earnings", "MSFT", {"y": 2}, 3600)
    stats = cache.stats()
    assert stats["total"] >= 2
    assert "by_endpoint" in stats
    assert stats["by_endpoint"].get("summaries", 0) >= 1


def test_fallback_to_in_memory_when_db_unavailable():
    """When db_path is invalid, ORATSCache falls back to in-memory dict."""
    c = ORATSCache(db_path="/nonexistent/path/db.sqlite")
    assert c._conn is None

    c.set("summaries", "AAPL", {"ticker": "AAPL"}, 3600)
    result = c.get("summaries", "AAPL", 3600)
    assert result == {"ticker": "AAPL"}


def test_set_handles_datetime_in_data(tmp_path):
    """Regression: json.dumps blew up on datetime objects in ORATS payloads."""
    from datetime import datetime
    import sqlite3 as _sqlite3

    db_path = tmp_path / "cache.db"
    conn = _sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orats_cache (
            endpoint    TEXT NOT NULL,
            cache_key   TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            ttl_seconds REAL NOT NULL,
            PRIMARY KEY (endpoint, cache_key)
        )"""
    )
    conn.commit()
    conn.close()

    cache = ORATSCache(db_path=db_path)
    # Payload that would fail without default=str
    data = {"as_of": datetime(2026, 4, 23, 10, 0), "iv_rank": 42.5}
    cache.set("cores", "AAPL", data, ttl_seconds=3600)

    got = cache.get("cores", "AAPL", 3600)
    assert got is not None
    # datetime was stringified, which is acceptable for cache purposes
    assert "2026-04-23" in str(got)
