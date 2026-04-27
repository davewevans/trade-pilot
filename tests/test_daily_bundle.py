"""Tests for api/daily_bundle.py."""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from api.daily_bundle import (
    build_daily_bundle,
    _section_errors_warnings,
    _section_cycle_timing,
    _section_decisions,
    _section_cycle_summary,
    _section_ai_usage,
    _section_header,
    _section_data_health,
)

TARGET_DATE = date(2026, 4, 23)


# ── fixtures ─────────────────────────────────────────────────


def _make_db(path: Path) -> str:
    db = str(path / "trade_pilot.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            underlying TEXT NOT NULL,
            cycle_id TEXT,
            wheel_state TEXT,
            action TEXT NOT NULL,
            reasoning TEXT,
            confidence REAL,
            alpaca_order_id TEXT,
            prompt_version TEXT,
            context_json TEXT
        );
        CREATE TABLE IF NOT EXISTS cycles (
            cycle_id TEXT PRIMARY KEY,
            strategy_type TEXT NOT NULL,
            underlying TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            outcome TEXT,
            total_premium REAL,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_summaries (
            date TEXT PRIMARY KEY,
            decisions_total INTEGER NOT NULL DEFAULT 0,
            skips INTEGER NOT NULL DEFAULT 0,
            trades_executed INTEGER NOT NULL DEFAULT 0,
            premium_collected REAL NOT NULL DEFAULT 0.0,
            skip_reasons_json TEXT
        );
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            underlying TEXT,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            response_time_ms INTEGER,
            estimated_cost_usd REAL,
            decision_action TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db


def _make_snapshots(snap_dir: Path) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "regime_history.json").write_text(json.dumps([{
        "regime": "NEUTRAL", "vix": 18.5, "fear_greed_value": 42,
        "fear_greed_rating": "Fear", "iv_environment": "normal",
        "stability": "stable", "timestamp": "2026-04-23T10:00:00",
    }]))
    (snap_dir / "source_health.json").write_text(json.dumps({
        "alpaca": {
            "last_success": "2026-04-23T10:00:00",
            "last_failure": None,
            "last_failure_reason": None,
            "consecutive_failures": 0,
            "today_successes": 12,
            "today_failures": 0,
            "last_checked": "2026-04-23T10:00:00",
        },
        "orats": {
            "last_success": "2026-04-22T14:00:00",
            "last_failure": "2026-04-23T09:55:00",
            "last_failure_reason": "HTTP 429",
            "consecutive_failures": 1,
            "today_successes": 0,
            "today_failures": 1,
            "last_checked": "2026-04-23T09:55:00",
        },
    }))
    (snap_dir / "circuit_breaker_state.json").write_text(json.dumps({"state": "GREEN"}))


# ── section tests ─────────────────────────────────────────────


def test_section_errors_warnings_missing_log(tmp_path):
    result = _section_errors_warnings(TARGET_DATE, str(tmp_path / "logs"))
    assert "No structured log file found" in result


def test_section_errors_warnings_groups_by_logger_message(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "2026-04-23.jsonl"
    records = [
        {"ts": "2026-04-23T10:00:00", "level": "WARNING", "logger": "data.orats", "message": "ORATS failed", "traceback": None, "extra": {"symbol": "SPY"}},
        {"ts": "2026-04-23T10:01:00", "level": "WARNING", "logger": "data.orats", "message": "ORATS failed", "traceback": None, "extra": {"symbol": "AAPL"}},
        {"ts": "2026-04-23T10:02:00", "level": "ERROR", "logger": "data.finnhub", "message": "403 error", "traceback": "Traceback...\nValueError: x", "extra": {}},
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records))
    result = _section_errors_warnings(TARGET_DATE, str(log_dir))
    assert "## Errors & warnings" in result
    assert "data.orats" in result
    assert "Count:** 2" in result
    assert "data.finnhub" in result
    assert "traceback" in result.lower()


def test_section_errors_warnings_empty_file(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "2026-04-23.jsonl").write_text("")
    result = _section_errors_warnings(TARGET_DATE, str(log_dir))
    assert "empty" in result.lower()


def test_section_decisions_no_data(tmp_path):
    db = _make_db(tmp_path)
    result = _section_decisions(TARGET_DATE, db)
    assert "No decisions" in result


def test_section_decisions_one_decision(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO decisions (timestamp, strategy_type, underlying, action, confidence, reasoning) VALUES (?,?,?,?,?,?)",
        ("2026-04-23T10:04:29", "turnover_wheel", "AAPL", "SKIP", 0.85,
         json.dumps({"Macro": "Neutral market", "Technical": "Below SMA"})),
    )
    conn.commit()
    conn.close()
    result = _section_decisions(TARGET_DATE, db)
    assert "SKIP" in result
    assert "AAPL" in result
    assert "Macro" in result
    assert "Neutral market" in result


def test_section_ai_usage_no_data(tmp_path):
    db = _make_db(tmp_path)
    result = _section_ai_usage(TARGET_DATE, db)
    assert "No token_usage" in result


def test_section_ai_usage_with_data(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO token_usage (timestamp, strategy_type, model, input_tokens, output_tokens, estimated_cost_usd) VALUES (?,?,?,?,?,?)",
        ("2026-04-23T10:05:00", "wheel", "claude-sonnet-4-6", 5000, 300, 0.0123),
    )
    conn.commit()
    conn.close()
    result = _section_ai_usage(TARGET_DATE, db)
    assert "wheel" in result
    assert "0.0123" in result


def test_section_data_health_no_snapshot(tmp_path):
    result = _section_data_health(str(tmp_path))
    assert "No source_health" in result


def test_section_data_health_with_snapshot(tmp_path):
    _make_snapshots(tmp_path)
    result = _section_data_health(str(tmp_path))
    assert "alpaca" in result
    assert "orats" in result


# ── _section_data_health unit tests (health derivation contract) ──────────


def _write_health(snap: Path, payload: dict) -> None:
    (snap / "source_health.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def test_data_health_renders_healthy_source_as_check(tmp_path):
    _write_health(tmp_path, {
        "ORATS": {
            "last_success": "2026-04-25T14:00:00",
            "last_failure": None,
            "last_failure_reason": None,
            "consecutive_failures": 0,
            "today_successes": 47,
            "today_failures": 0,
            "last_checked": "2026-04-25T14:00:00",
        },
    })
    out = _section_data_health(str(tmp_path))
    assert "| ORATS | ✓ | 47/0" in out


def test_data_health_renders_unhealthy_source_as_x(tmp_path):
    _write_health(tmp_path, {
        "yfinance": {
            "last_success": "2026-04-24T20:30:00",
            "last_failure": "2026-04-25T14:00:00",
            "last_failure_reason": "HTTP 404 quoteSummary missing",
            "consecutive_failures": 3,
            "today_successes": 5,
            "today_failures": 3,
            "last_checked": "2026-04-25T14:00:00",
        },
    })
    out = _section_data_health(str(tmp_path))
    assert "| yfinance | ✗ | 5/3" in out
    assert "HTTP 404" in out


def test_data_health_missing_file_renders_message(tmp_path):
    out = _section_data_health(str(tmp_path))
    assert "_No source_health.json snapshot found._" in out


def test_data_health_empty_dict_renders_same_as_missing(tmp_path):
    _write_health(tmp_path, {})
    out = _section_data_health(str(tmp_path))
    assert "_No source_health.json snapshot found._" in out


def test_data_health_never_succeeded_renders_never(tmp_path):
    _write_health(tmp_path, {
        "FRED": {
            "last_success": None,
            "last_failure": "2026-04-25T14:00:00",
            "last_failure_reason": "Connection timeout",
            "consecutive_failures": 5,
            "today_successes": 0,
            "today_failures": 5,
            "last_checked": "2026-04-25T14:00:00",
        },
    })
    out = _section_data_health(str(tmp_path))
    assert "| FRED | ✗ | 0/5" in out
    assert "never" in out


def test_data_health_sources_sorted_alphabetically(tmp_path):
    _write_health(tmp_path, {
        "yfinance": {"consecutive_failures": 0, "today_successes": 1, "today_failures": 0, "last_success": "2026-04-25T14:00:00"},
        "ORATS":    {"consecutive_failures": 0, "today_successes": 1, "today_failures": 0, "last_success": "2026-04-25T14:00:00"},
        "FRED":     {"consecutive_failures": 0, "today_successes": 1, "today_failures": 0, "last_success": "2026-04-25T14:00:00"},
    })
    out = _section_data_health(str(tmp_path))
    fred_idx = out.index("| FRED ")
    orats_idx = out.index("| ORATS ")
    yf_idx = out.index("| yfinance ")
    assert fred_idx < orats_idx < yf_idx, "sources must render alphabetically"


# ── _section_cycle_summary tests ────────────────────────────────────────────


def test_cycle_summary_no_cycles_at_all(tmp_path):
    """Empty DB — both sub-sections show informative empty-state messages."""
    db = _make_db(tmp_path)
    result = _section_cycle_summary(TARGET_DATE, db)
    assert "### Opened today" in result
    assert "0 cycles opened today" in result
    assert "see Decisions section" in result
    assert "### Active cycles from prior days" in result
    assert "No active cycles carried over" in result
    assert "No cycles found for this date" not in result


def test_cycle_summary_opened_today(tmp_path):
    """Cycle opened on target_date appears in 'Opened today' sub-section."""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cycles (cycle_id, strategy_type, underlying, opened_at, status) VALUES (?,?,?,?,?)",
        ("c1", "wheel", "AAPL", f"{TARGET_DATE.isoformat()}T10:00:00", "ACTIVE"),
    )
    conn.commit()
    conn.close()
    result = _section_cycle_summary(TARGET_DATE, db)
    assert "### Opened today" in result
    assert "AAPL" in result
    assert "0 cycles opened today" not in result
    assert "### Active cycles from prior days" in result
    assert "No active cycles carried over" in result


def test_cycle_summary_active_from_prior_day(tmp_path):
    """Active cycle from a prior date appears in 'Active cycles from prior days'."""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cycles (cycle_id, strategy_type, underlying, opened_at, status) VALUES (?,?,?,?,?)",
        ("c1", "wheel", "MSFT", "2026-04-22T10:00:00", "ACTIVE"),
    )
    conn.commit()
    conn.close()
    result = _section_cycle_summary(TARGET_DATE, db)
    assert "### Opened today" in result
    assert "0 cycles opened today" in result
    assert "### Active cycles from prior days" in result
    assert "MSFT" in result
    assert "No active cycles carried over" not in result


def test_cycle_summary_opened_today_and_active_prior(tmp_path):
    """Both today's cycle and a prior active cycle populate their sub-sections."""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cycles (cycle_id, strategy_type, underlying, opened_at, status) VALUES (?,?,?,?,?)",
        ("c1", "wheel", "AAPL", f"{TARGET_DATE.isoformat()}T10:00:00", "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO cycles (cycle_id, strategy_type, underlying, opened_at, status) VALUES (?,?,?,?,?)",
        ("c2", "wheel", "MSFT", "2026-04-22T10:00:00", "ACTIVE"),
    )
    conn.commit()
    conn.close()
    result = _section_cycle_summary(TARGET_DATE, db)
    assert "AAPL" in result
    assert "MSFT" in result
    assert "0 cycles opened today" not in result
    assert "No active cycles carried over" not in result


def test_cycle_summary_closed_prior_not_shown(tmp_path):
    """A CLOSED cycle from a prior date should NOT appear in active-prior sub-section."""
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO cycles (cycle_id, strategy_type, underlying, opened_at, status, closed_at) VALUES (?,?,?,?,?,?)",
        ("c1", "wheel", "SPY", "2026-04-22T10:00:00", "CLOSED", "2026-04-22T15:00:00"),
    )
    conn.commit()
    conn.close()
    result = _section_cycle_summary(TARGET_DATE, db)
    assert "No active cycles carried over" in result
    assert "SPY" not in result


# ── integration test ──────────────────────────────────────────


def test_build_daily_bundle_empty_db(tmp_path):
    db = _make_db(tmp_path)
    snap_dir = tmp_path / "snapshots"
    _make_snapshots(snap_dir)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    result = build_daily_bundle(
        target_date=TARGET_DATE,
        db_path=db,
        snapshots_dir=str(snap_dir),
        log_dir=str(log_dir),
    )
    assert isinstance(result, str)
    assert result.encode("utf-8")  # valid UTF-8

    for section in [
        "## trade-pilot Daily Bundle",
        "## Market context",
        "## Cycle summary",
        "## Claude agreement snapshot",
        "## Decisions",
        "## Portfolio Greeks",
        "## Data source health",
        "## AI usage",
        "## Errors & warnings",
        "## Cycle timing",
    ]:
        assert section in result, f"Missing section: {section}"

    assert len(result) < 500_000


def test_build_daily_bundle_with_decisions(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    for sym in ["AAPL", "MSFT", "SPY"]:
        conn.execute(
            "INSERT INTO decisions (timestamp, strategy_type, underlying, action, confidence, reasoning) VALUES (?,?,?,?,?,?)",
            (f"2026-04-23T10:0{['1','2','3'][['AAPL','MSFT','SPY'].index(sym)]}:00",
             "wheel", sym, "SKIP", 0.9,
             json.dumps({"Macro": f"context for {sym}"})),
        )
    conn.commit()
    conn.close()

    snap_dir = tmp_path / "snapshots"
    _make_snapshots(snap_dir)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    result = build_daily_bundle(TARGET_DATE, db, str(snap_dir), str(log_dir))
    for sym in ["AAPL", "MSFT", "SPY"]:
        assert sym in result
