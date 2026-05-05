"""Tests for ORATSCache connection injection and fallback behaviour.

Covers:
1. Clean init with an injected connection
2. Explicit in-memory fallback (conn=None) — CRITICAL log emitted
3. Fallback cache rejects writes when at capacity

Note: the old db_path / ORATS_CACHE_ALLOW_FALLBACK / RENDER-raises behaviour has
been removed.  ORATSCache no longer opens its own sqlite3 connection; it accepts
an injected connection or falls back to in-memory when conn=None is passed.
"""

import logging
import sqlite3

import pytest


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS orats_cache (
        endpoint    TEXT NOT NULL,
        cache_key   TEXT NOT NULL,
        data_json   TEXT NOT NULL,
        fetched_at  REAL NOT NULL,
        ttl_seconds REAL NOT NULL,
        PRIMARY KEY (endpoint, cache_key)
    )
"""


def _make_conn(tmp_path):
    """Open a fresh test sqlite3 connection with the orats_cache schema."""
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Test 1: clean init with an injected connection
# ---------------------------------------------------------------------------

def test_init_valid_conn(tmp_path):
    """ORATSCache initialises cleanly when given an open connection."""
    from data.orats_cache import ORATSCache

    conn = _make_conn(tmp_path)
    cache = ORATSCache(conn=conn)

    assert cache._conn is not None
    assert cache._fallback == {}

    # Basic round-trip works
    cache.set("summaries", "AAPL", {"iv": 42}, 3600)
    assert cache.get("summaries", "AAPL", 3600) == {"iv": 42}


# ---------------------------------------------------------------------------
# Test 2: explicit in-memory fallback (conn=None) → CRITICAL log
# ---------------------------------------------------------------------------

def test_fallback_when_conn_is_none(caplog):
    """ORATSCache falls back to in-memory dict when conn=None is passed."""
    from data.orats_cache import ORATSCache

    with caplog.at_level(logging.CRITICAL, logger="data.orats_cache"):
        cache = ORATSCache(conn=None)

    assert cache._conn is None, "Should use in-memory fallback"
    assert any("DEGRADED MODE" in r.message for r in caplog.records)

    # In-memory fallback still serves get/set
    cache.set("summaries", "AAPL", {"ticker": "AAPL"}, 3600)
    assert cache.get("summaries", "AAPL", 3600) == {"ticker": "AAPL"}


# ---------------------------------------------------------------------------
# Test 3: fallback cache rejects writes at capacity
# ---------------------------------------------------------------------------

def test_fallback_capacity_cap(caplog):
    """Fallback cache stops accepting writes once _FALLBACK_MAX_ENTRIES is reached."""
    from data.orats_cache import ORATSCache

    cache = ORATSCache(conn=None)
    cap = ORATSCache._FALLBACK_MAX_ENTRIES
    for i in range(cap):
        cache._fallback_set("ep", f"key_{i}", {"v": i}, 60.0)

    assert len(cache._fallback) == cap

    with caplog.at_level(logging.WARNING, logger="data.orats_cache"):
        cache._fallback_set("ep", "overflow_key", {"v": "overflow"}, 60.0)

    assert len(cache._fallback) == cap, "Capacity should not exceed the cap"
    assert ("ep", "overflow_key") not in cache._fallback
    assert any("capacity" in r.message for r in caplog.records)
