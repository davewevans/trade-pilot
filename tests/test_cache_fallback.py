"""Tests for ORATSCache fallback behaviour (Phase 11).

Covers:
1. Clean init with valid DB path
2. Production mode + bad DB path → raises RuntimeError (no silent fallback)
3. Dev mode + bad DB path → falls back with CRITICAL log
4. ORATS_CACHE_ALLOW_FALLBACK=1 in production → fallback allowed with CRITICAL log
5. Fallback cache rejects writes when at capacity
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cache(db_path, render: bool, allow_fallback: str = "0"):
    """Import-time patching ensures settings values are consistent."""
    from unittest.mock import patch as _patch

    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = db_path
    mock_settings.RENDER = render
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = allow_fallback

    with _patch("data.orats_cache.ORATSCache.__init__.__globals__", {}):
        pass  # not used — we patch via import below

    import importlib
    import data.orats_cache as mod

    with _patch.object(mod, "ORATSCache") as _cls:
        pass  # reset

    with _patch("config.settings", mock_settings):
        # Re-import to pick up patched settings inside __init__
        from data.orats_cache import ORATSCache

        def _patched_init(self, db_path_arg=None):
            from config import settings  # will resolve to mock via patch
            _orig_init = ORATSCache.__init__
            # We can't easily call __init__ with a patched import, so instantiate directly.
            raise NotImplementedError  # sentinel — use _direct_make instead

    # Simpler: just instantiate with explicit db_path so the `if db_path is None` branch is skipped.
    from data.orats_cache import ORATSCache

    with _patch("config.settings", mock_settings):
        return ORATSCache(db_path=str(db_path))


# ---------------------------------------------------------------------------
# Test 1: clean init with valid DB path
# ---------------------------------------------------------------------------

def test_init_valid_db(tmp_path):
    """ORATSCache initialises cleanly when pointed at a writable DB."""
    from unittest.mock import patch, MagicMock
    db = tmp_path / "test.db"

    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = db
    mock_settings.RENDER = True
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = "0"

    from data.orats_cache import ORATSCache
    with patch("config.settings", mock_settings):
        cache = ORATSCache(db_path=db)

    assert cache._conn is not None
    assert cache._fallback == {}


# ---------------------------------------------------------------------------
# Test 2: production + bad DB path → raises
# ---------------------------------------------------------------------------

def test_production_bad_db_raises(tmp_path):
    """In production mode with an invalid DB path, ORATSCache must raise."""
    from unittest.mock import patch, MagicMock

    bad_path = "/nonexistent_dir/no_perms/test.db"
    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = bad_path
    mock_settings.RENDER = True
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = "0"

    from data.orats_cache import ORATSCache
    with patch("config.settings", mock_settings):
        with pytest.raises(RuntimeError, match="SQLite cache init failed"):
            ORATSCache(db_path=bad_path)


# ---------------------------------------------------------------------------
# Test 3: dev mode + bad DB path → falls back + CRITICAL log
# ---------------------------------------------------------------------------

def test_dev_bad_db_falls_back(tmp_path, caplog):
    """In dev mode with an invalid DB path, ORATSCache falls back and logs CRITICAL."""
    import logging
    from unittest.mock import patch, MagicMock

    bad_path = "/nonexistent_dir/no_perms/test.db"
    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = bad_path
    mock_settings.RENDER = False
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = "0"

    from data.orats_cache import ORATSCache
    with patch("config.settings", mock_settings):
        with caplog.at_level(logging.CRITICAL, logger="data.orats_cache"):
            cache = ORATSCache(db_path=bad_path)

    assert cache._conn is None, "Should fall back to in-memory in dev mode"
    assert any("DEGRADED MODE" in r.message for r in caplog.records), (
        "Expected CRITICAL log mentioning DEGRADED MODE"
    )


# ---------------------------------------------------------------------------
# Test 4: ORATS_CACHE_ALLOW_FALLBACK=1 in production → fallback allowed
# ---------------------------------------------------------------------------

def test_production_allow_fallback_flag(tmp_path, caplog):
    """With ORATS_CACHE_ALLOW_FALLBACK=1, production also falls back with CRITICAL log."""
    import logging
    from unittest.mock import patch, MagicMock

    bad_path = "/nonexistent_dir/no_perms/test.db"
    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = bad_path
    mock_settings.RENDER = True
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = "1"

    from data.orats_cache import ORATSCache
    with patch("config.settings", mock_settings):
        with caplog.at_level(logging.CRITICAL, logger="data.orats_cache"):
            cache = ORATSCache(db_path=bad_path)

    assert cache._conn is None, "Should fall back to in-memory when flag is set"
    assert any("DEGRADED MODE" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Test 5: fallback cache rejects writes at capacity
# ---------------------------------------------------------------------------

def test_fallback_capacity_cap(tmp_path, caplog):
    """Fallback cache stops accepting writes once _FALLBACK_MAX_ENTRIES is reached."""
    import logging
    from unittest.mock import patch, MagicMock

    bad_path = "/nonexistent_dir/no_perms/test.db"
    mock_settings = MagicMock()
    mock_settings.DATABASE_PATH = bad_path
    mock_settings.RENDER = False
    mock_settings.ORATS_CACHE_ALLOW_FALLBACK = "0"

    from data.orats_cache import ORATSCache
    with patch("config.settings", mock_settings):
        cache = ORATSCache(db_path=bad_path)

    # Fill the fallback to capacity
    cap = ORATSCache._FALLBACK_MAX_ENTRIES
    for i in range(cap):
        cache._fallback_set("ep", f"key_{i}", {"v": i}, 60.0)

    assert len(cache._fallback) == cap

    # One more write should be silently dropped and logged as a warning
    with caplog.at_level(logging.WARNING, logger="data.orats_cache"):
        cache._fallback_set("ep", "overflow_key", {"v": "overflow"}, 60.0)

    assert len(cache._fallback) == cap, "Capacity should not exceed the cap"
    assert ("ep", "overflow_key") not in cache._fallback
    assert any("capacity" in r.message for r in caplog.records)
