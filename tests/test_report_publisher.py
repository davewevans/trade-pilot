"""Tests for data/report_publisher.py and jobs/publish_reports.py.

The publisher is data exhaust: it must never crash the caller, and it must be a
no-op unless explicitly enabled with a repo + token. These tests mock the
GitHub Contents API entirely — no real network call is ever made.

Covered:
- publish_report: flag-off no-op, missing repo/token no-op, create (201),
  update via sha (422 -> GET sha -> 200), network/timeout error.
- publish_reports.run(): flag-off short-circuit (no bundle build), flag-on
  builds + publishes daily/<today>.md, and never raises even on failure.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import data.report_publisher as rp
import jobs.publish_reports as pj
from config import settings

REPO = "davewevans/trade-pilot-reports"
TOKEN = "ghp_testtoken"
REPO_PATH = "daily/2026-06-28.md"
CONTENT = "# bundle\n\nsome markdown"


def _resp(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body or {}
    r.text = text
    return r


@pytest.fixture
def enabled(monkeypatch):
    """Flag on, repo + token set."""
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_REPO", REPO, raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_TOKEN", TOKEN, raising=False)


# ── publish_report: no-op cases ────────────────────────────────────────────


def test_flag_off_is_noop_returns_false_no_http(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_REPO", REPO, raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_TOKEN", TOKEN, raising=False)

    with patch.object(rp, "requests") as mock_requests:
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is False
    mock_requests.put.assert_not_called()
    mock_requests.get.assert_not_called()


def test_missing_repo_is_noop_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_REPO", "", raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_TOKEN", TOKEN, raising=False)

    with patch.object(rp, "requests") as mock_requests:
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is False
    mock_requests.put.assert_not_called()


def test_missing_token_is_noop_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_REPO", REPO, raising=False)
    monkeypatch.setattr(settings, "GITHUB_REPORTS_TOKEN", "", raising=False)

    with patch.object(rp, "requests") as mock_requests:
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is False
    mock_requests.put.assert_not_called()


# ── publish_report: create / update / error ────────────────────────────────


def test_create_path_put_201_returns_true(enabled):
    with patch.object(rp, "requests") as mock_requests:
        mock_requests.put.return_value = _resp(201)
        result = rp.publish_report(REPO_PATH, CONTENT, message="msg")

    assert result is True
    # Single PUT, no sha lookup needed.
    assert mock_requests.put.call_count == 1
    mock_requests.get.assert_not_called()

    _, kwargs = mock_requests.put.call_args
    body = kwargs["json"]
    assert body["message"] == "msg"
    assert "content" in body and "sha" not in body  # create has no sha
    assert kwargs["timeout"] == 20
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_update_path_422_then_get_sha_then_200(enabled):
    with patch.object(rp, "requests") as mock_requests:
        mock_requests.put.side_effect = [_resp(422, text="sha required"), _resp(200)]
        mock_requests.get.return_value = _resp(200, {"sha": "abc123"})
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is True
    assert mock_requests.put.call_count == 2
    mock_requests.get.assert_called_once()
    # Second PUT must carry the looked-up sha.
    _, second_kwargs = mock_requests.put.call_args_list[1]
    assert second_kwargs["json"]["sha"] == "abc123"


def test_update_path_422_but_sha_lookup_fails_returns_false(enabled):
    with patch.object(rp, "requests") as mock_requests:
        mock_requests.put.return_value = _resp(422, text="sha required")
        mock_requests.get.return_value = _resp(404)  # no sha available
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is False
    # Only the first PUT happened; no retry without a sha.
    assert mock_requests.put.call_count == 1


def test_network_error_returns_false_does_not_raise(enabled):
    with patch.object(rp, "requests") as mock_requests:
        mock_requests.put.side_effect = RuntimeError("connection reset")
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is False


def test_timeout_returns_false_does_not_raise(enabled):
    class _Timeout(Exception):
        pass

    with patch.object(rp, "requests") as mock_requests:
        mock_requests.put.side_effect = _Timeout("timed out")
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is False


def test_unexpected_status_returns_false(enabled):
    with patch.object(rp, "requests") as mock_requests:
        mock_requests.put.return_value = _resp(500, text="server error")
        result = rp.publish_report(REPO_PATH, CONTENT)

    assert result is False


# ── publish_reports.run() ──────────────────────────────────────────────────


def test_run_flag_off_short_circuits_without_building(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_PUBLISH_ENABLED", False, raising=False)

    with patch("api.daily_bundle.build_daily_bundle") as mock_build, \
         patch("data.report_publisher.publish_report") as mock_pub:
        pj.run()

    mock_build.assert_not_called()
    mock_pub.assert_not_called()


def test_run_flag_on_builds_and_publishes_today(enabled):
    today = datetime.now(ZoneInfo("America/New_York")).date()
    expected_path = f"daily/{today.isoformat()}.md"

    with patch("api.daily_bundle.build_daily_bundle", return_value=CONTENT) as mock_build, \
         patch("data.report_publisher.publish_report", return_value=True) as mock_pub:
        pj.run()

    mock_build.assert_called_once()
    _, build_kwargs = mock_build.call_args
    assert build_kwargs["target_date"] == today

    mock_pub.assert_called_once()
    args, kwargs = mock_pub.call_args
    assert args[0] == expected_path
    assert args[1] == CONTENT


def test_run_never_raises_when_publish_returns_false(enabled):
    with patch("api.daily_bundle.build_daily_bundle", return_value=CONTENT), \
         patch("data.report_publisher.publish_report", return_value=False):
        pj.run()  # must not raise


def test_run_never_raises_when_publish_raises(enabled):
    with patch("api.daily_bundle.build_daily_bundle", return_value=CONTENT), \
         patch("data.report_publisher.publish_report", side_effect=RuntimeError("boom")):
        pj.run()  # must not raise


def test_run_never_raises_when_build_raises(enabled):
    with patch("api.daily_bundle.build_daily_bundle", side_effect=RuntimeError("db gone")), \
         patch("data.report_publisher.publish_report") as mock_pub:
        pj.run()  # must not raise

    mock_pub.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
