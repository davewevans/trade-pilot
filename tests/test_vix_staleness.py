"""Tests for get_vix_with_age stale-tolerance logic in data/market_data.py."""

import time
from unittest.mock import patch

import pytest

import data.market_data as _md


@pytest.fixture(autouse=True)
def reset_vix_cache():
    """Reset the module-level VIX cache before every test."""
    _md._VIX_CACHE["value"] = None
    _md._VIX_CACHE["fetched_at"] = None
    yield
    _md._VIX_CACHE["value"] = None
    _md._VIX_CACHE["fetched_at"] = None


# ── Fresh fetch ───────────────────────────────────────────────────────────────

def test_fresh_fetch_populates_cache_and_returns_not_stale(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", False)
    result = _md.get_vix_with_age(18.5)
    assert result["value"] == 18.5
    assert result["stale"] is False
    assert result["age_seconds"] == 0
    assert _md._VIX_CACHE["value"] == 18.5
    assert _md._VIX_CACHE["fetched_at"] is not None


def test_fresh_fetch_overwrites_old_cache(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 1800)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    _md._VIX_CACHE["value"] = 30.0
    _md._VIX_CACHE["fetched_at"] = time.time() - 600

    result = _md.get_vix_with_age(22.0)
    assert result["value"] == 22.0
    assert result["stale"] is False
    assert _md._VIX_CACHE["value"] == 22.0


# ── Flag off ──────────────────────────────────────────────────────────────────

def test_flag_off_returns_none_on_miss(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", False)
    result = _md.get_vix_with_age(None)
    assert result["value"] is None
    assert result["stale"] is True
    assert result["age_seconds"] is None


def test_flag_off_ignores_warm_cache(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", False)
    _md._VIX_CACHE["value"] = 20.0
    _md._VIX_CACHE["fetched_at"] = time.time() - 60

    result = _md.get_vix_with_age(None)
    assert result["value"] is None
    assert result["stale"] is True


# ── Stale cache within age window ─────────────────────────────────────────────

def test_stale_within_market_hours_window_returns_cached_value(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 1800)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    monkeypatch.setattr(_md, "_is_market_hours", lambda: True)

    _md._VIX_CACHE["value"] = 21.5
    _md._VIX_CACHE["fetched_at"] = time.time() - 900  # 15 min ago, inside 30 min window

    result = _md.get_vix_with_age(None)
    assert result["value"] == 21.5
    assert result["stale"] is True
    assert result["age_seconds"] is not None
    assert result["age_seconds"] <= 910  # allow a little clock drift in test


def test_stale_within_off_hours_window_returns_cached_value(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 1800)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    monkeypatch.setattr(_md, "_is_market_hours", lambda: False)

    _md._VIX_CACHE["value"] = 17.0
    _md._VIX_CACHE["fetched_at"] = time.time() - 7200  # 2 hours ago, inside 4 hr window

    result = _md.get_vix_with_age(None)
    assert result["value"] == 17.0
    assert result["stale"] is True


# ── Stale cache past age window ───────────────────────────────────────────────

def test_stale_past_market_hours_window_returns_none(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 1800)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    monkeypatch.setattr(_md, "_is_market_hours", lambda: True)

    _md._VIX_CACHE["value"] = 25.0
    _md._VIX_CACHE["fetched_at"] = time.time() - 3600  # 1 hr ago, past 30 min window

    result = _md.get_vix_with_age(None)
    assert result["value"] is None
    assert result["stale"] is True
    assert result["age_seconds"] is not None
    assert result["age_seconds"] >= 3600


def test_stale_past_off_hours_window_returns_none(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 1800)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    monkeypatch.setattr(_md, "_is_market_hours", lambda: False)

    _md._VIX_CACHE["value"] = 19.0
    _md._VIX_CACHE["fetched_at"] = time.time() - 18000  # 5 hrs ago, past 4 hr window

    result = _md.get_vix_with_age(None)
    assert result["value"] is None
    assert result["stale"] is True


# ── No cache at all ───────────────────────────────────────────────────────────

def test_no_cache_returns_none_even_when_flag_on(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 1800)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    # cache is empty (ensured by fixture)
    result = _md.get_vix_with_age(None)
    assert result["value"] is None
    assert result["stale"] is True
    assert result["age_seconds"] is None


# ── Exact boundary ────────────────────────────────────────────────────────────

def test_boundary_exactly_at_max_age_is_within_window(monkeypatch):
    """age == max_age should still return the cached value (uses >, not >=)."""
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 100)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    monkeypatch.setattr(_md, "_is_market_hours", lambda: True)

    _md._VIX_CACHE["value"] = 14.0
    # Set fetched_at such that age is exactly max_age (100s); int rounding means
    # we need to be a tiny bit inside the window to avoid flakiness.
    _md._VIX_CACHE["fetched_at"] = time.time() - 99

    result = _md.get_vix_with_age(None)
    assert result["value"] == 14.0


def test_boundary_one_second_past_max_age_returns_none(monkeypatch):
    monkeypatch.setattr("config.settings.VIX_STALE_TOLERANCE_ENABLED", True)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_MARKET_HOURS", 100)
    monkeypatch.setattr("config.settings.VIX_STALE_MAX_AGE_SECONDS_OFF_HOURS", 14400)
    monkeypatch.setattr(_md, "_is_market_hours", lambda: True)

    _md._VIX_CACHE["value"] = 14.0
    _md._VIX_CACHE["fetched_at"] = time.time() - 101  # 101s > 100s max

    result = _md.get_vix_with_age(None)
    assert result["value"] is None
