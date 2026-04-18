"""Tests for jobs.monthly_evaluation.

Covers:
  - Feature flag off: job exits cleanly without running pipeline
  - Low-sample month: no real flags, no notification, DB row written
  - Normal month with flags: notification fires with "warning" severity
  - Normal month without flags: notification suppressed
  - Regeneration (upsert): second run overwrites scores/flags, no duplicate
    rows, reviewed_at and action_note preserved
  - Markdown archive: written to REPORTS_DIR with expected sections
  - Spot-check sampling: rows marked spot_check_pending after run
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from database.db import Database
from database.repositories.decision_scores_repository import DecisionScoresRepository
from database.repositories.judge_spot_checks_repository import JudgeSpotChecksRepository
from database.repositories.monthly_evaluations_repository import (
    MonthlyEvaluationsRepository,
)
from jobs.monthly_evaluation import (
    _decrement_month,
    _month_window,
    _prior_calendar_month,
    _prior_n_months,
    _run_pipeline,
    run,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    d = Database(path=str(tmp_path / "test.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def conn(db):
    return db.get_connection()


# ── Settings factory ──────────────────────────────────────────────────────────


def _make_settings(db: Database, reports_dir: Path, *,
                   judge_enabled: bool = False) -> MagicMock:
    """Return a mock settings object pointing at a real test db."""
    s = MagicMock()
    s.EVALUATION_AUTOMATION_ENABLED = True
    s.EVALUATION_JUDGE_ENABLED = judge_enabled
    s.JUDGE_MODEL = "claude-opus-4-6"
    s.JUDGE_RATE_LIMIT_MS = 0
    s.ANTHROPIC_API_KEY = "test-key"
    s.DATABASE_PATH = db.path
    s.REPORTS_DIR = reports_dir
    return s


# ── DB helpers ────────────────────────────────────────────────────────────────


def _insert_decision(conn, *, decision_id: int, month: str,
                     strategy: str = "wheel", action: str = "sell_put") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO decisions
            (id, timestamp, strategy_type, action, underlying, confidence,
             reasoning, prompt_version, context_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (decision_id, f"{month}-15T10:00:00", strategy, action,
         "AAPL", 0.8, "test reasoning", "v1.0.0", "{}"),
    )
    conn.commit()


def _insert_score(conn, *, score_id: int, decision_id: int, month: str,
                  total_score: float, pass_fail: str,
                  rule_adherence: float) -> None:
    """Insert a pre-built decision_scores row (scorer_type=programmatic so
    the orchestrator's idempotency check skips re-scoring)."""
    dim_scores = [{"dimension": "rule_adherence", "score": rule_adherence,
                   "score_metadata": {}}]
    conn.execute(
        """
        INSERT OR REPLACE INTO decision_scores
            (id, decision_id, scorer_type, scored_at, total_score, max_score,
             rubric_version, dimension_scores_json, pass_fail,
             spot_check_pending, spot_check_submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
        """,
        (score_id, decision_id, "programmatic",
         f"{month}-15T12:00:00", total_score, 100.0, "v1.0.0",
         json.dumps(dim_scores), pass_fail),
    )
    conn.commit()


def _populate_month(conn, *, month: str, n: int, start_id: int = 1,
                    rule_adherence: float, pass_fail: str,
                    total_score: float, strategy: str = "wheel") -> None:
    for i in range(n):
        did = start_id + i
        _insert_decision(conn, decision_id=did, month=month, strategy=strategy)
        _insert_score(conn, score_id=did, decision_id=did, month=month,
                      total_score=total_score, pass_fail=pass_fail,
                      rule_adherence=rule_adherence)


# ── Scorer patch helpers ──────────────────────────────────────────────────────


def _noop_scorer_patches():
    """Patch both orchestrator functions to be no-ops (scores already in DB)."""
    return (
        patch("evaluation.scoring_orchestrator.score_decisions_in_range", return_value=0),
        patch("evaluation.scoring_orchestrator.score_decisions_with_judge", return_value=0),
    )


# ── Date helper unit tests ────────────────────────────────────────────────────


def test_prior_calendar_month_january():
    assert _prior_calendar_month(date(2026, 1, 15)) == "2025-12"


def test_prior_calendar_month_mid_year():
    assert _prior_calendar_month(date(2026, 5, 1)) == "2026-04"


def test_month_window_feb_leap():
    start, end = _month_window("2024-02")
    assert start == "2024-02-01T00:00:00"
    assert end == "2024-02-29T23:59:59"


def test_month_window_feb_non_leap():
    _, end = _month_window("2026-02")
    assert end == "2026-02-28T23:59:59"


def test_decrement_month_wraps_year():
    assert _decrement_month("2026-01") == "2025-12"
    assert _decrement_month("2026-04") == "2026-03"


def test_prior_n_months_oldest_first():
    assert _prior_n_months("2026-04", 3) == ["2026-01", "2026-02", "2026-03"]


# ── Feature-flag off ──────────────────────────────────────────────────────────


def test_flag_off_does_not_run_pipeline(monkeypatch):
    """EVALUATION_AUTOMATION_ENABLED=false → run() returns without touching DB."""
    monkeypatch.setattr("config.settings.EVALUATION_AUTOMATION_ENABLED", False)

    pipeline_calls: list = []
    monkeypatch.setattr(
        "jobs.monthly_evaluation._run_pipeline",
        lambda *a, **kw: pipeline_calls.append(True),
    )

    run()
    assert pipeline_calls == []


# ── Low-sample month ──────────────────────────────────────────────────────────


def test_low_sample_no_flags_no_notification(tmp_path, db, conn):
    """3 decisions (< gate of 10 for wheel) → no real flags, no ntfy push."""
    month = "2026-04"
    _populate_month(conn, month=month, n=3, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)

    settings = _make_settings(db, tmp_path / "reports")
    notify_calls: list = []

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify",
                        side_effect=lambda *a, **kw: notify_calls.append(a)):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    assert notify_calls == [], "no notification for low-sample month"

    evals_repo = MonthlyEvaluationsRepository(conn)
    row = evals_repo.get_by_month(month)
    assert row is not None, "monthly_evaluations row must exist"

    flags = json.loads(row["flags_json"] or "[]")
    assert flags == [], "flags_json must be empty list for low-sample month"


# ── Normal month with flags ───────────────────────────────────────────────────


def test_flags_present_fires_warning_notification(tmp_path, db, conn):
    """rule_adherence=0.50 (< threshold 0.70) → flags → ntfy warning fired."""
    month = "2026-04"
    _populate_month(conn, month=month, n=15, rule_adherence=0.50,
                    pass_fail="fail", total_score=50.0)

    settings = _make_settings(db, tmp_path / "reports")
    notify_calls: list[tuple] = []

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify",
                        side_effect=lambda *a, **kw: notify_calls.append((a, kw))):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    assert len(notify_calls) == 1, "exactly one notification expected when flags present"
    call_args, call_kwargs = notify_calls[0]
    assert call_args[0] == "warning", f"expected severity 'warning', got {call_args[0]!r}"
    assert month in call_args[1], "month must appear in notification title"
    assert "evaluation" in call_kwargs.get("tags", [])
    assert "monthly_review" in call_kwargs.get("tags", [])


def test_flags_body_includes_strategy_count(tmp_path, db, conn):
    """Notification body mentions flag count and strategy count."""
    month = "2026-04"
    _populate_month(conn, month=month, n=15, rule_adherence=0.50,
                    pass_fail="fail", total_score=50.0)

    settings = _make_settings(db, tmp_path / "reports")
    captured: list[tuple] = []

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify",
                        side_effect=lambda *a, **kw: captured.append((a, kw))):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    _, kw = captured[0]
    body = captured[0][0][2]  # positional arg 3 = message
    assert "strategies" in body.lower()
    assert f"/evaluations/{month}" in body


# ── Normal month without flags ────────────────────────────────────────────────


def test_no_flags_suppresses_notification(tmp_path, db, conn):
    """rule_adherence=0.90 (above all thresholds) → no flags → no push."""
    month = "2026-04"
    _populate_month(conn, month=month, n=15, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)

    settings = _make_settings(db, tmp_path / "reports")
    notify_calls: list = []

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify",
                        side_effect=lambda *a, **kw: notify_calls.append(a)):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    assert notify_calls == [], "notification must be suppressed when no flags"


# ── Regeneration / UPSERT ─────────────────────────────────────────────────────


def test_upsert_produces_single_row(tmp_path, db, conn):
    """Running the pipeline twice for the same month results in exactly one DB row."""
    month = "2026-04"
    _populate_month(conn, month=month, n=15, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)

    settings = _make_settings(db, tmp_path / "reports")

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM monthly_evaluations WHERE month = ?", (month,)
    ).fetchone()["n"]
    assert count == 1, "upsert must not create duplicate rows"


def test_upsert_preserves_reviewed_at_and_action_note(tmp_path, db, conn):
    """Regenerating a reviewed month leaves reviewed_at and action_note intact."""
    month = "2026-04"
    _populate_month(conn, month=month, n=15, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)

    settings = _make_settings(db, tmp_path / "reports")

    p1, p2 = _noop_scorer_patches()
    # First run
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    # Operator marks the month as reviewed
    evals_repo = MonthlyEvaluationsRepository(conn)
    evals_repo.mark_reviewed(month, "Looks fine — no action needed")
    reviewed_at_before = evals_repo.get_by_month(month)["reviewed_at"]
    assert reviewed_at_before is not None

    # Second run (regenerate)
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    row_after = evals_repo.get_by_month(month)
    assert row_after["reviewed_at"] == reviewed_at_before, (
        "reviewed_at must survive regeneration"
    )
    assert row_after["action_note"] == "Looks fine — no action needed", (
        "action_note must survive regeneration"
    )


# ── Markdown archive ──────────────────────────────────────────────────────────


def test_markdown_archive_created(tmp_path, db, conn):
    """Markdown file is created at REPORTS_DIR/monthly_eval_YYYY-MM.md."""
    month = "2026-04"
    _populate_month(conn, month=month, n=15, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)

    reports_dir = tmp_path / "reports"
    settings = _make_settings(db, reports_dir)

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    archive = reports_dir / f"monthly_eval_{month}.md"
    assert archive.exists(), "markdown archive file must exist"
    text = archive.read_text(encoding="utf-8")
    assert f"# Monthly Evaluation: {month}" in text
    assert "## Summary" in text
    assert "Decisions scored" in text
    assert "## Score Distribution" in text


def test_markdown_archive_flagged_section(tmp_path, db, conn):
    """Flagged Dimensions section appears when real flags are detected."""
    month = "2026-04"
    _populate_month(conn, month=month, n=15, rule_adherence=0.50,
                    pass_fail="fail", total_score=50.0)

    reports_dir = tmp_path / "reports"
    settings = _make_settings(db, reports_dir)

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    text = (reports_dir / f"monthly_eval_{month}.md").read_text(encoding="utf-8")
    assert "## Flagged Dimensions" in text
    assert "rule_adherence" in text


# ── Spot-check sampling ───────────────────────────────────────────────────────


def test_spot_check_rows_marked_pending(tmp_path, db, conn):
    """At least one score row is marked spot_check_pending after pipeline runs."""
    month = "2026-04"
    _populate_month(conn, month=month, n=20, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)

    settings = _make_settings(db, tmp_path / "reports")

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM decision_scores WHERE spot_check_pending = 1"
    ).fetchone()["n"]
    assert pending >= 1, "at least one score row must be marked spot_check_pending"


def test_spot_check_fraction_within_range(tmp_path, db, conn):
    """10–20% of score rows should be marked (±1 for rounding)."""
    month = "2026-04"
    n = 100
    _populate_month(conn, month=month, n=n, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)

    settings = _make_settings(db, tmp_path / "reports")

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM decision_scores WHERE spot_check_pending = 1"
    ).fetchone()["n"]
    # expect ~15 (15% of 100), accept 10–20 tolerance
    assert 10 <= pending <= 20, (
        f"expected 10-20 rows spot-checked from 100, got {pending}"
    )


# ── avg_score and pct_pass persisted ─────────────────────────────────────────


def test_avg_score_and_pct_pass_written(tmp_path, db, conn):
    """avg_score and pct_pass in monthly_evaluations reflect the score data."""
    month = "2026-04"
    # 10 pass + 5 fail
    _populate_month(conn, month=month, n=10, start_id=1, rule_adherence=0.90,
                    pass_fail="pass", total_score=90.0)
    _populate_month(conn, month=month, n=5, start_id=11, rule_adherence=0.50,
                    pass_fail="fail", total_score=50.0)

    settings = _make_settings(db, tmp_path / "reports")

    p1, p2 = _noop_scorer_patches()
    with p1, p2, patch("notifications.notify"):
        _run_pipeline(month, f"{month}-01T00:00:00", f"{month}-31T23:59:59", settings)

    row = MonthlyEvaluationsRepository(conn).get_by_month(month)
    assert row["avg_score"] is not None
    assert row["pct_pass"] is not None
    # 10/15 = 0.6667 pass rate
    assert abs(row["pct_pass"] - (10 / 15)) < 0.01
