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
    # Keep conn open — ORATSCache does not own the connection.
    c = ORATSCache(conn=conn)
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
    # Keep conn open — passed to ORATSCache which does not own it.
    c = ORATSCache(conn=conn)
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
    """When conn=None is passed, ORATSCache uses the in-memory dict fallback."""
    c = ORATSCache(conn=None)
    assert c._conn is None

    c.set("summaries", "AAPL", {"ticker": "AAPL"}, 3600)
    result = c.get("summaries", "AAPL", 3600)
    assert result == {"ticker": "AAPL"}


# ── Defensive null / type checks ─────────────────────────────────────────────

def test_get_returns_none_on_non_string_endpoint(cache):
    cache.set("summaries", "AAPL", {"x": 1}, 3600)
    result = cache.get(None, "AAPL", 3600)
    assert result is None


def test_get_returns_none_on_non_string_cache_key(cache):
    cache.set("summaries", "AAPL", {"x": 1}, 3600)
    result = cache.get("summaries", 42, 3600)
    assert result is None


def test_get_returns_none_on_null_fetched_at(tmp_path):
    # Schema in production has `fetched_at REAL NOT NULL`, but old rows written
    # before that constraint existed may have NULL. Create the table without NOT
    # NULL on fetched_at so we can insert a bad row directly.
    db_path = tmp_path / "nullable.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE orats_cache (
            endpoint    TEXT NOT NULL,
            cache_key   TEXT NOT NULL,
            data_json   TEXT NOT NULL,
            fetched_at  REAL,
            ttl_seconds REAL NOT NULL,
            PRIMARY KEY (endpoint, cache_key)
        )"""
    )
    conn.execute(
        "INSERT INTO orats_cache (endpoint, cache_key, data_json, fetched_at, ttl_seconds) "
        "VALUES (?, ?, ?, ?, ?)",
        ("summaries", "AAPL", '{"iv_rank": 42}', None, 3600),
    )
    conn.commit()
    # Keep conn open — passed to ORATSCache which does not own it.
    c = ORATSCache(conn=conn)
    result = c.get("summaries", "AAPL", 3600)
    assert result is None


def test_get_returns_none_on_interface_error(cache):
    from unittest.mock import MagicMock

    # Replace _conn with a mock that raises InterfaceError on execute.
    # (sqlite3.Connection.execute is a C extension method and cannot be
    # patched via patch.object on Python 3.14.)
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = sqlite3.InterfaceError("bad param")
    cache._conn = mock_conn

    result = cache.get("summaries", "AAPL", 3600)
    assert result is None


def test_set_rejects_non_string_endpoint(cache):
    cache.set(None, "AAPL", {"x": 1}, 3600)
    # Row must not exist in DB
    row = cache._conn.execute(
        "SELECT 1 FROM orats_cache WHERE cache_key='AAPL'"
    ).fetchone()
    assert row is None


def test_set_rejects_non_string_cache_key(cache):
    cache.set("summaries", 123, {"x": 1}, 3600)
    row = cache._conn.execute(
        "SELECT 1 FROM orats_cache WHERE endpoint='summaries'"
    ).fetchone()
    assert row is None


def test_get_happy_path_unaffected_by_defensive_checks(cache):
    """Existing happy-path contract: valid string params still hit and return data."""
    data = {"iv_rank_1y": 55.0}
    cache.set("ivrank", "SPY", data, 3600)
    assert cache.get("ivrank", "SPY", 3600) == data


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
    # Keep conn open — passed to ORATSCache which does not own it.
    cache = ORATSCache(conn=conn)
    # Payload that would fail without default=str
    data = {"as_of": datetime(2026, 4, 23, 10, 0), "iv_rank": 42.5}
    cache.set("cores", "AAPL", data, ttl_seconds=3600)

    got = cache.get("cores", "AAPL", 3600)
    assert got is not None
    # datetime was stringified, which is acceptable for cache purposes
    assert "2026-04-23" in str(got)


# ── Atomicity + defensive read regressions (2026-05-07) ─────────────────────


def test_set_then_get_round_trips(cache):
    """Basic write→read round trip on the production schema."""
    data = {"iv_rank_1y": 38.5, "ticker": "AAPL"}
    cache.set("cores", "AAPL", data, 3600)
    assert cache.get("cores", "AAPL", 3600) == data


def _nullable_cache(tmp_path, name="legacy.db"):
    """Build a cache backed by a table without NOT NULL on fetched_at/data_json.

    Mirrors the legacy schema state where corrupt rows can exist.
    """
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE orats_cache (
            endpoint    TEXT NOT NULL,
            cache_key   TEXT NOT NULL,
            data_json   TEXT,
            fetched_at  REAL,
            ttl_seconds REAL NOT NULL,
            PRIMARY KEY (endpoint, cache_key)
        )"""
    )
    conn.commit()
    return ORATSCache(conn=conn), conn


def test_partial_row_treated_as_miss(tmp_path, caplog):
    """A row with NULL fetched_at must be treated as miss, never raise unpack errors.

    Regression for 2026-05-07 ValueError at orats_cache.py:89 ('expected 2, got 0').
    """
    import logging

    cache, conn = _nullable_cache(tmp_path)
    conn.execute(
        "INSERT INTO orats_cache (endpoint, cache_key, data_json, fetched_at, ttl_seconds) "
        "VALUES (?, ?, ?, ?, ?)",
        ("cores", "INTC", '{"atm_iv_m1": 0.25}', None, 3600),
    )
    conn.commit()

    with caplog.at_level(logging.WARNING, logger="data.orats_cache"):
        result = cache.get("cores", "INTC", 3600)

    assert result is None
    assert any("treating as miss" in rec.getMessage() for rec in caplog.records)


def test_partial_row_with_null_data_json_treated_as_miss(tmp_path, caplog):
    """Symmetric to NULL fetched_at: NULL data_json must also be treated as miss."""
    import logging

    cache, conn = _nullable_cache(tmp_path, name="legacy_dj.db")
    conn.execute(
        "INSERT INTO orats_cache (endpoint, cache_key, data_json, fetched_at, ttl_seconds) "
        "VALUES (?, ?, ?, ?, ?)",
        ("cores", "AAPL", None, time.time(), 3600),
    )
    conn.commit()

    with caplog.at_level(logging.WARNING, logger="data.orats_cache"):
        result = cache.get("cores", "AAPL", 3600)

    assert result is None
    assert any("treating as miss" in rec.getMessage() for rec in caplog.records)


def test_concurrent_writes_atomic(cache):
    """Two writers racing on the same key must not leave a partially-written row.

    Each writer uses INSERT OR REPLACE inside an explicit transaction, so the
    final row reflects exactly one of the two payloads (last-writer-wins) and
    the row's data_json + fetched_at are always coherent.
    """
    import sqlite3 as _sqlite3
    import threading

    # The fixture connection is single-threaded; spin up thread-local
    # connections that hit the same db file.
    db_path = cache._conn.execute("PRAGMA database_list").fetchone()[2]

    payload_a = {"writer": "A", "value": 1}
    payload_b = {"writer": "B", "value": 2}
    barrier = threading.Barrier(2)

    def _write(payload):
        c = _sqlite3.connect(db_path)
        try:
            local = ORATSCache(conn=c)
            barrier.wait()
            for _ in range(50):
                local.set("cores", "RACE", payload, 3600)
        finally:
            c.close()

    t1 = threading.Thread(target=_write, args=(payload_a,))
    t2 = threading.Thread(target=_write, args=(payload_b,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    row = cache._conn.execute(
        "SELECT data_json, fetched_at FROM orats_cache "
        "WHERE endpoint='cores' AND cache_key='RACE'"
    ).fetchone()
    assert row is not None
    data_json, fetched_at = row
    # Atomicity check: both columns populated, data_json parses, identifies one writer
    assert fetched_at is not None
    assert data_json is not None
    parsed = json.loads(data_json)
    assert parsed["writer"] in ("A", "B")
    assert parsed["value"] in (1, 2)


def test_cleanup_corrupt_removes_nulls(tmp_path):
    """cleanup_corrupt deletes rows with NULL fetched_at or NULL data_json and reports counts."""
    cache, conn = _nullable_cache(tmp_path, name="cleanup.db")

    # 1 healthy, 2 corrupt (one NULL fetched_at, one NULL data_json)
    conn.execute(
        "INSERT INTO orats_cache VALUES (?, ?, ?, ?, ?)",
        ("cores", "OK", '{"x": 1}', time.time(), 3600),
    )
    conn.execute(
        "INSERT INTO orats_cache VALUES (?, ?, ?, ?, ?)",
        ("cores", "BAD_FETCHED_AT", '{"x": 2}', None, 3600),
    )
    conn.execute(
        "INSERT INTO orats_cache VALUES (?, ?, ?, ?, ?)",
        ("ivrank", "BAD_DATA", None, time.time(), 3600),
    )
    conn.commit()

    # Dry run: counts but no deletion.
    dry = cache.cleanup_corrupt(dry_run=True)
    assert dry["scanned"] == 3
    assert dry["corrupt"] == 2
    assert dry["deleted"] == 0
    remaining = conn.execute("SELECT COUNT(*) FROM orats_cache").fetchone()[0]
    assert remaining == 3

    # Real run: corrupt rows gone, healthy row preserved.
    real = cache.cleanup_corrupt()
    assert real["corrupt"] == 2
    assert real["deleted"] == 2
    assert real["by_endpoint"] == {"cores": 1, "ivrank": 1}
    surviving = conn.execute(
        "SELECT cache_key FROM orats_cache"
    ).fetchall()
    assert surviving == [("OK",)]
