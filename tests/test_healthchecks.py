"""Tests for utils/healthchecks.py ping lifecycle helpers."""

from unittest.mock import MagicMock, patch

import pytest
import requests as real_requests


BASE_URL = "https://hc-ping.com/abc-123"


class TestPingStart:
    def test_no_env_var(self, monkeypatch):
        """HC_PING_URL_FOO absent → no HTTP call made."""
        monkeypatch.delenv("HC_PING_URL_FOO", raising=False)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get") as mock_get:
            from utils.healthchecks import ping_start
            ping_start("foo")
            mock_get.assert_not_called()

    def test_disabled_via_flag(self, monkeypatch):
        """URL set but HEALTHCHECKS_ENABLED=false → no HTTP call made."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.setenv("HEALTHCHECKS_ENABLED", "false")

        with patch("utils.healthchecks.requests.get") as mock_get:
            from utils.healthchecks import ping_start
            ping_start("foo")
            mock_get.assert_not_called()

    def test_enabled_default(self, monkeypatch):
        """HEALTHCHECKS_ENABLED unset (default true), URL set → GET {url}/start once."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get") as mock_get:
            from utils.healthchecks import ping_start
            ping_start("foo")
            mock_get.assert_called_once_with(f"{BASE_URL}/start", timeout=5)

    def test_empty_url_treated_as_unset(self, monkeypatch):
        """HC_PING_URL set to empty string → no HTTP call."""
        monkeypatch.setenv("HC_PING_URL_FOO", "  ")
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get") as mock_get:
            from utils.healthchecks import ping_start
            ping_start("foo")
            mock_get.assert_not_called()


class TestPingSuccess:
    def test_hits_base_url(self, monkeypatch):
        """ping_success → GET {url} with no suffix."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get") as mock_get:
            from utils.healthchecks import ping_success
            ping_success("foo")
            mock_get.assert_called_once_with(BASE_URL, timeout=5)


class TestPingFail:
    def test_with_reason_posts_body(self, monkeypatch):
        """ping_fail with reason → POST {url}/fail with reason as body."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.post") as mock_post:
            from utils.healthchecks import ping_fail
            ping_fail("foo", reason="something broke")
            mock_post.assert_called_once_with(
                f"{BASE_URL}/fail", data="something broke", timeout=5
            )

    def test_reason_truncated_to_500_chars(self, monkeypatch):
        """Reason longer than 500 chars is truncated to 500."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        long_reason = "x" * 600

        with patch("utils.healthchecks.requests.post") as mock_post:
            from utils.healthchecks import ping_fail
            ping_fail("foo", reason=long_reason)
            _, kwargs = mock_post.call_args
            assert len(kwargs["data"]) == 500

    def test_without_reason_uses_get(self, monkeypatch):
        """ping_fail with reason=None → GET {url}/fail (no POST)."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get") as mock_get, \
             patch("utils.healthchecks.requests.post") as mock_post:
            from utils.healthchecks import ping_fail
            ping_fail("foo", reason=None)
            mock_get.assert_called_once_with(f"{BASE_URL}/fail", timeout=5)
            mock_post.assert_not_called()

    def test_empty_string_reason_uses_get(self, monkeypatch):
        """ping_fail with reason='' → GET {url}/fail (no POST)."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get") as mock_get, \
             patch("utils.healthchecks.requests.post") as mock_post:
            from utils.healthchecks import ping_fail
            ping_fail("foo", reason="")
            mock_get.assert_called_once_with(f"{BASE_URL}/fail", timeout=5)
            mock_post.assert_not_called()


class TestNetworkResilience:
    def test_connection_error_does_not_raise(self, monkeypatch):
        """requests.ConnectionError is caught; function returns normally."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get",
                   side_effect=real_requests.exceptions.ConnectionError("refused")):
            from utils.healthchecks import ping_start
            ping_start("foo")  # must not raise

    def test_timeout_does_not_raise(self, monkeypatch):
        """requests.Timeout is caught; function returns normally."""
        monkeypatch.setenv("HC_PING_URL_FOO", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get",
                   side_effect=real_requests.exceptions.Timeout("timed out")):
            from utils.healthchecks import ping_success
            ping_success("foo")  # must not raise


class TestJobNameHandling:
    def test_job_name_case_insensitive_lookup(self, monkeypatch):
        """Job name 'market_open' → env var HC_PING_URL_MARKET_OPEN is consulted."""
        monkeypatch.setenv("HC_PING_URL_MARKET_OPEN", BASE_URL)
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        with patch("utils.healthchecks.requests.get") as mock_get:
            from utils.healthchecks import ping_start
            ping_start("market_open")
            mock_get.assert_called_once_with(f"{BASE_URL}/start", timeout=5)

    def test_invalid_job_name_skips_and_warns(self, monkeypatch, caplog):
        """Job name with invalid chars skips ping and logs at WARNING."""
        monkeypatch.delenv("HEALTHCHECKS_ENABLED", raising=False)

        import logging
        with patch("utils.healthchecks.requests.get") as mock_get, \
             caplog.at_level(logging.WARNING, logger="utils.healthchecks"):
            from utils.healthchecks import ping_start
            ping_start("bad-name!")  # hyphen and bang are invalid
            mock_get.assert_not_called()
            assert any("invalid" in r.message for r in caplog.records)
