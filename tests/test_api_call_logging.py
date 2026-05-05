"""Tests for the structured api_calls.jsonl log (Phase 4)."""

import json
import logging
import os
from pathlib import Path

import pytest

from data.api_ledger import ApiLedger, OratsQuotaExceeded
from utils.json_log_formatter import JsonFormatter


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_ledger_with_log_handler(tmp_path: Path):
    """Create an ApiLedger and attach a capturing api_calls log handler."""
    log_path = tmp_path / "api_calls.jsonl"
    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(JsonFormatter())

    api_log = logging.getLogger("api_calls")
    # Remove existing handlers to avoid cross-test contamination
    api_log.handlers.clear()
    api_log.addHandler(handler)
    api_log.setLevel(logging.INFO)
    api_log.propagate = False

    from database.db import Database
    _db = Database(path=tmp_path / "test.db")
    _db.init_schema()
    ledger = ApiLedger(_db.get_connection())
    return ledger, log_path, handler


def read_log_lines(log_path: Path) -> list[dict]:
    """Parse all non-empty lines from a JSONL log file."""
    lines = []
    if not log_path.exists():
        return lines
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            lines.append(json.loads(raw))
    return lines


# ---------------------------------------------------------------------------
# case 1: successful request writes one line with cache_hit=false and status=200
# ---------------------------------------------------------------------------


def test_successful_request_logs_one_line(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 999)

    ledger, log_path, handler = make_ledger_with_log_handler(tmp_path)

    ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", "weekly_research")
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 45, "weekly_research")

    handler.flush()
    lines = read_log_lines(log_path)
    assert len(lines) == 1
    assert lines[0]["cache_hit"] is False
    assert lines[0]["status"] == 200
    assert lines[0]["api"] == "orats_historical"
    assert lines[0]["endpoint"] == "hist/summaries"
    assert lines[0]["symbol"] == "AAPL"
    assert lines[0]["job_name"] == "weekly_research"


# ---------------------------------------------------------------------------
# case 2: cache hit writes one line with cache_hit=true
# ---------------------------------------------------------------------------


def test_cache_hit_logs_one_line(tmp_path):
    ledger, log_path, handler = make_ledger_with_log_handler(tmp_path)

    ledger.record("orats_historical", "hist/summaries", "AAPL", True, None, None, None)

    handler.flush()
    lines = read_log_lines(log_path)
    assert len(lines) == 1
    assert lines[0]["cache_hit"] is True
    assert lines[0]["status"] is None
    assert lines[0]["duration_ms"] is None


# ---------------------------------------------------------------------------
# case 3: blocked-by-cap writes one line with blocked_reason set, status=null
# ---------------------------------------------------------------------------


def test_blocked_call_logs_blocked_reason(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 1)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)

    ledger, log_path, handler = make_ledger_with_log_handler(tmp_path)

    # Use up the 1-call monthly budget
    ledger.record("orats_historical", "hist/summaries", "AAPL", False, 200, 50, None)

    with pytest.raises(OratsQuotaExceeded):
        ledger.check_and_reserve("orats_historical", "hist/summaries", "AAPL", None)

    handler.flush()
    lines = read_log_lines(log_path)
    # First line: the successful record; second line: the blocked attempt
    assert len(lines) == 2
    blocked = lines[1]
    assert blocked["blocked_reason"] == "monthly_cap"
    assert blocked["status"] is None


# ---------------------------------------------------------------------------
# case 4: all log lines are valid JSON
# ---------------------------------------------------------------------------


def test_all_log_lines_are_valid_json(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MONTHLY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_DAILY_CAP", 99999)
    monkeypatch.setattr(settings, "ORATS_HISTORICAL_MINUTE_CAP", 99999)

    ledger, log_path, handler = make_ledger_with_log_handler(tmp_path)

    for i in range(5):
        ledger.record("orats_live", "summaries", f"SYM{i}", False, 200, 100, None)
    for i in range(3):
        ledger.record("orats_live", "summaries", f"CACHED{i}", True, None, None, None)

    handler.flush()
    raw_text = log_path.read_text(encoding="utf-8")
    for line in raw_text.splitlines():
        if line.strip():
            obj = json.loads(line)  # raises if invalid
            assert "ts" in obj
            assert "api" in obj


# ---------------------------------------------------------------------------
# case 5: trade-pilot.log does not receive hist/summaries per-request entries
# ---------------------------------------------------------------------------


def test_main_log_not_flooded_by_orats_historical(tmp_path, caplog):
    """The data.orats_historical logger should be at WARNING in normal config.

    In main.py this is set via:
        logging.getLogger("data.orats_historical").setLevel(logging.WARNING)

    Here we verify that an INFO-level message from that logger is NOT captured
    when the root logger is at INFO, once the historical logger is silenced.
    """
    hist_logger = logging.getLogger("data.orats_historical")
    original_level = hist_logger.level
    try:
        hist_logger.setLevel(logging.WARNING)
        # Do NOT use caplog.at_level here — it would override setLevel(WARNING) and
        # lower the effective level back to INFO, defeating the assertion.
        hist_logger.info("ORATS hist/summaries OK for AAPL on 2024-01-15")

        # The INFO message should NOT appear because the logger is at WARNING
        orats_msgs = [r for r in caplog.records if "hist/summaries" in r.message]
        assert len(orats_msgs) == 0
    finally:
        hist_logger.setLevel(original_level)
