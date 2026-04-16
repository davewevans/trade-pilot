"""Tests for per-call Claude API usage instrumentation (Story 1).

Verifies that:
- record_api_call() is invoked with correct values for cache-hit responses
- record_api_call() is invoked with correct values for cache-miss responses
- _record_usage() never raises when the DB repo is None
- _cache_hit_pct() computes correctly including the zero-denominator edge case
"""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from ai.claude_advisor import ClaudeAdvisor, _cache_hit_pct
from database.db import Database
from database.repositories import ApiUsageRepository


# ── Unit: _cache_hit_pct ─────────────────────────────────────────────────────

def test_cache_hit_pct_full_cache():
    # All tokens served from cache
    assert _cache_hit_pct(cache_read=1000, cache_write=0, input_tokens=0) == 100.0


def test_cache_hit_pct_no_cache():
    # Zero cache reads
    assert _cache_hit_pct(cache_read=0, cache_write=500, input_tokens=500) == 0.0


def test_cache_hit_pct_partial():
    # 500 / (500 + 300 + 200) = 50%
    assert _cache_hit_pct(cache_read=500, cache_write=300, input_tokens=200) == 50.0


def test_cache_hit_pct_zero_denominator():
    # Should return 0.0, not raise ZeroDivisionError
    assert _cache_hit_pct(cache_read=0, cache_write=0, input_tokens=0) == 0.0


# ── Integration: ApiUsageRepository via real SQLite ──────────────────────────

@pytest.fixture
def db_and_repo(tmp_path):
    db = Database(path=str(tmp_path / "test_usage.db"))
    db.init_schema()
    repo = ApiUsageRepository(db.get_connection())
    yield db, repo
    db.close()


def test_record_api_call_cache_hit(db_and_repo):
    """Cache-hit response: cache_read > 0, cache_write == 0."""
    _, repo = db_and_repo
    row_id = repo.record_api_call(
        strategy="wheel",
        phase="idle",
        model="claude-sonnet-4-6",
        input_tokens=100,
        cache_read_tokens=800,
        cache_write_tokens=0,
        output_tokens=50,
        latency_ms=1200,
    )
    assert row_id is not None

    rows = repo.get_recent(days=1)
    assert len(rows) == 1
    r = rows[0]
    assert r["strategy"] == "wheel"
    assert r["phase"] == "idle"
    assert r["cache_read_tokens"] == 800
    assert r["cache_write_tokens"] == 0
    assert r["input_tokens"] == 100
    assert r["output_tokens"] == 50
    assert r["latency_ms"] == 1200


def test_record_api_call_cache_miss(db_and_repo):
    """Cache-miss response: cache_write > 0, cache_read == 0."""
    _, repo = db_and_repo
    row_id = repo.record_api_call(
        strategy="bull_put_spread",
        phase="idle",
        model="claude-sonnet-4-6",
        input_tokens=500,
        cache_read_tokens=0,
        cache_write_tokens=600,
        output_tokens=80,
        latency_ms=3500,
    )
    assert row_id is not None

    rows = repo.get_recent(days=1)
    assert len(rows) == 1
    r = rows[0]
    assert r["strategy"] == "bull_put_spread"
    assert r["cache_read_tokens"] == 0
    assert r["cache_write_tokens"] == 600


# ── Unit: _record_usage via mocked advisor ───────────────────────────────────

def _make_usage(input_tokens=100, cache_read=0, cache_write=0, output_tokens=50):
    """Build a minimal mock that looks like an Anthropic Usage object."""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.cache_read_input_tokens = cache_read
    usage.cache_creation_input_tokens = cache_write
    usage.output_tokens = output_tokens
    return usage


def _make_advisor(repo=None):
    """Construct a ClaudeAdvisor without hitting Anthropic or reading prompts."""
    with patch("ai.claude_advisor.anthropic.Anthropic"), \
         patch("ai.claude_advisor.settings"):
        advisor = ClaudeAdvisor.__new__(ClaudeAdvisor)
        advisor.client = MagicMock()
        advisor.model = "claude-sonnet-4-6"
        advisor._last_usage = None
        advisor._api_usage_repo = repo
        advisor.thinking_mode = "off"
    return advisor


def test_record_usage_calls_repo_with_cache_hit():
    repo = MagicMock()
    advisor = _make_advisor(repo=repo)
    usage = _make_usage(input_tokens=100, cache_read=800, cache_write=0, output_tokens=50)

    advisor._record_usage(usage, strategy="wheel", phase="short_put", latency_ms=1000)

    repo.record_api_call.assert_called_once_with(
        strategy="wheel",
        phase="short_put",
        model="claude-sonnet-4-6",
        input_tokens=100,
        cache_read_tokens=800,
        cache_write_tokens=0,
        output_tokens=50,
        latency_ms=1000,
    )


def test_record_usage_calls_repo_with_cache_miss():
    repo = MagicMock()
    advisor = _make_advisor(repo=repo)
    usage = _make_usage(input_tokens=500, cache_read=0, cache_write=600, output_tokens=80)

    advisor._record_usage(usage, strategy="iron_condor", phase="open", latency_ms=2500)

    repo.record_api_call.assert_called_once_with(
        strategy="iron_condor",
        phase="open",
        model="claude-sonnet-4-6",
        input_tokens=500,
        cache_read_tokens=0,
        cache_write_tokens=600,
        output_tokens=80,
        latency_ms=2500,
    )


def test_record_usage_none_repo_does_not_raise():
    """When no repo is wired, _record_usage should still succeed (just logs)."""
    advisor = _make_advisor(repo=None)
    usage = _make_usage(input_tokens=200, cache_read=400, cache_write=0, output_tokens=60)
    # Should not raise
    advisor._record_usage(usage, strategy="wheel", phase="idle", latency_ms=500)


def test_record_usage_none_usage_does_not_raise():
    """When usage is None (API returned nothing), should default to zeros."""
    repo = MagicMock()
    advisor = _make_advisor(repo=repo)

    advisor._record_usage(None, strategy="wheel", phase="idle", latency_ms=0)

    repo.record_api_call.assert_called_once_with(
        strategy="wheel",
        phase="idle",
        model="claude-sonnet-4-6",
        input_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        output_tokens=0,
        latency_ms=0,
    )
