"""Integration tests for the self-review phase in jobs/monthly_evaluation.py.

Covers:
  - SELF_REVIEW_ENABLED=false → self-review skipped, archive has no Self-Review section
  - SELF_REVIEW_ENABLED=true but real_flags=[] → self-review skipped
  - Valid Opus output → archive contains ## Self-Review section with patches
  - All Opus calls return None → archive shows Skipped lines, no notification
  - At least one patch returned → self-review notification fires with correct counts
  - Zero patches despite successful calls → notification suppressed
  - Self-review phase raises exception → pipeline still completes
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from database.db import Database
from database.repositories.decision_scores_repository import DecisionScoresRepository
from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository
from jobs.monthly_evaluation import _run_pipeline, _write_markdown_archive


# ── Fixtures & helpers ────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    d = Database(path=str(tmp_path / "test.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def conn(db):
    return db.get_connection()


def _make_settings(db: Database, reports_dir: Path, *,
                   self_review_enabled: bool = False,
                   self_review_max_flags: int = 5,
                   self_review_min_decisions: int = 3) -> MagicMock:
    s = MagicMock()
    s.EVALUATION_AUTOMATION_ENABLED = True
    s.EVALUATION_JUDGE_ENABLED = False
    s.JUDGE_MODEL = "claude-opus-4-7"
    s.JUDGE_RATE_LIMIT_MS = 0
    s.ANTHROPIC_API_KEY = "test-key"
    s.DATABASE_PATH = db.path
    s.REPORTS_DIR = reports_dir
    s.SELF_REVIEW_ENABLED = self_review_enabled
    s.SELF_REVIEW_MAX_FLAGS_PER_MONTH = self_review_max_flags
    s.SELF_REVIEW_MIN_SUPPORTING_DECISIONS = self_review_min_decisions
    return s


def _insert_decision(conn, decision_id: int, month: str = "2026-03",
                     strategy: str = "wheel") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO decisions
            (id, timestamp, strategy_type, action, underlying, confidence,
             reasoning, prompt_version, context_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (decision_id, f"{month}-15T10:00:00", strategy, "SELL_PUT",
         "AAPL", 0.8, "reasoning", "v1", "{}"),
    )
    conn.commit()


def _insert_score(conn, score_id: int, decision_id: int, month: str = "2026-03") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO decision_scores
            (id, decision_id, scorer_type, scored_at, total_score, max_score,
             rubric_version, dimension_scores_json, pass_fail,
             spot_check_pending, spot_check_submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
        """,
        (score_id, decision_id, "programmatic",
         f"{month}-15T12:00:00", 40.0, 100.0, "v1.0.0", "{}", "fail"),
    )
    conn.commit()


def _populate_month(conn, month: str = "2026-03", n: int = 5,
                    strategy: str = "wheel") -> list[int]:
    ids = list(range(1, n + 1))
    for did in ids:
        _insert_decision(conn, did, month=month, strategy=strategy)
        _insert_score(conn, did, did, month=month)
    return ids


def _noop_scorers():
    """Return context managers that patch the scoring orchestrator to no-ops."""
    return (
        patch("evaluation.scoring_orchestrator.score_decisions_in_range",
              return_value=0),
        patch("evaluation.scoring_orchestrator.score_decisions_with_judge",
              return_value=0),
    )


def _fake_flag(strategy: str = "wheel",
               dimension: str = "confidence_calibration") -> dict:
    return {
        "strategy": strategy,
        "dimension": dimension,
        "severity": "warning",
        "reason": "below threshold",
        "lowest_scoring_decisions": [1, 2, 3],
        "insufficient_sample": False,
    }


def _with_fake_flags(*flags):
    """Patch detect_flags to return the given flags."""
    return patch(
        "evaluation.flag_detector.detect_flags",
        return_value=list(flags),
    )


def _valid_self_review_result(flag: dict, n_patches: int = 1) -> dict:
    return {
        "flag": flag,
        "output": {
            "flag_summary": "Test flag summary.",
            "reasoning_failure_mode": "Failure mode details.",
            "suggested_prompt_patches": [
                {
                    "target_file": "prompts/system.md",
                    "target_section": "Confidence",
                    "patch_type": "add",
                    "exact_text": "Add this guidance.",
                    "rationale": "Reason tied to IDs 1, 2, 3.",
                    "supporting_decision_ids": [1, 2, 3],
                }
            ] * n_patches,
            "guardrail_migration_candidates": [],
            "declined_to_suggest": [],
        },
        "skipped_reason": None,
    }


def _declined_self_review_result(flag: dict) -> dict:
    return {
        "flag": flag,
        "output": {
            "flag_summary": "No clear failure.",
            "reasoning_failure_mode": "Insufficient evidence.",
            "suggested_prompt_patches": [],
            "guardrail_migration_candidates": [],
            "declined_to_suggest": [
                {"reason": "insufficient evidence", "relevant_decision_ids": [1]}
            ],
        },
        "skipped_reason": None,
    }


def _api_failure_result(flag: dict) -> dict:
    return {
        "flag": flag,
        "output": None,
        "skipped_reason": "api_or_schema_failure",
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSelfReviewDisabled:

    def test_self_review_skipped_when_disabled(self, db, conn, tmp_path):
        month = "2026-03"
        _populate_month(conn, month=month)
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=False)

        run_self_review_calls = []
        with patch("jobs.monthly_evaluation._run_self_review",
                   side_effect=lambda **kw: run_self_review_calls.append(True)):
            p1, p2 = _noop_scorers()
            with p1, p2:
                _run_pipeline(month, f"{month}-01T00:00:00",
                              f"{month}-31T23:59:59", settings)

        assert run_self_review_calls == []

    def test_archive_has_no_self_review_section_when_disabled(
        self, db, conn, tmp_path
    ):
        month = "2026-03"
        _populate_month(conn, month=month)
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=False)
        p1, p2 = _noop_scorers()
        with p1, p2:
            _run_pipeline(month, f"{month}-01T00:00:00",
                          f"{month}-31T23:59:59", settings)

        archive = (tmp_path / "reports" / f"monthly_eval_{month}.md").read_text()
        assert "## Self-Review" not in archive


class TestSelfReviewNoFlags:

    def test_self_review_skipped_when_no_real_flags(self, db, tmp_path):
        month = "2026-03"
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=True)

        run_self_review_calls = []
        with patch("jobs.monthly_evaluation._run_self_review",
                   side_effect=lambda **kw: run_self_review_calls.append(True)):
            p1, p2 = _noop_scorers()
            with p1, p2:
                _run_pipeline(month, f"{month}-01T00:00:00",
                              f"{month}-31T23:59:59", settings)

        assert run_self_review_calls == []


class TestSelfReviewWithOutput:

    def test_archive_contains_self_review_section_with_patches(
        self, db, conn, tmp_path
    ):
        month = "2026-03"
        _populate_month(conn, month=month)
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=True)

        flag = _fake_flag()
        fake_output = [_valid_self_review_result(flag, n_patches=1)]

        with _with_fake_flags(flag):
            with patch("jobs.monthly_evaluation._run_self_review",
                       return_value=fake_output):
                p1, p2 = _noop_scorers()
                with p1, p2:
                    _run_pipeline(month, f"{month}-01T00:00:00",
                                  f"{month}-31T23:59:59", settings)

        archive = (tmp_path / "reports" / f"monthly_eval_{month}.md").read_text()
        assert "## Self-Review" in archive
        assert "wheel / confidence_calibration" in archive
        assert "Test flag summary." in archive

    def test_api_failure_shows_skipped_line_no_notification(
        self, db, conn, tmp_path
    ):
        month = "2026-03"
        _populate_month(conn, month=month)
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=True)

        flag = _fake_flag()
        fake_output = [_api_failure_result(flag)]

        notify_calls = []
        with _with_fake_flags(flag):
            with patch("jobs.monthly_evaluation._run_self_review",
                       return_value=fake_output):
                with patch("notifications.notify",
                           side_effect=lambda *a, **kw: notify_calls.append(a)):
                    p1, p2 = _noop_scorers()
                    with p1, p2:
                        _run_pipeline(month, f"{month}-01T00:00:00",
                                      f"{month}-31T23:59:59", settings)

        archive = (tmp_path / "reports" / f"monthly_eval_{month}.md").read_text()
        assert "Skipped: api_or_schema_failure" in archive
        # self-review notification should NOT fire (0 patches)
        self_review_notifs = [c for c in notify_calls if "self_review" in str(c)]
        assert self_review_notifs == []

    def test_notification_fires_when_patches_present(self, db, conn, tmp_path):
        month = "2026-03"
        _populate_month(conn, month=month)
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=True)

        flag = _fake_flag()
        fake_output = [_valid_self_review_result(flag, n_patches=2)]

        notify_calls = []
        with _with_fake_flags(flag):
            with patch("jobs.monthly_evaluation._run_self_review",
                       return_value=fake_output):
                with patch("notifications.notify",
                           side_effect=lambda *a, **kw: notify_calls.append((a, kw))):
                    p1, p2 = _noop_scorers()
                    with p1, p2:
                        _run_pipeline(month, f"{month}-01T00:00:00",
                                      f"{month}-31T23:59:59", settings)

        self_review_notifs = [
            c for c in notify_calls
            if c[1].get("tags") and "self_review" in c[1]["tags"]
        ]
        assert len(self_review_notifs) == 1
        assert "2 suggested patches" in self_review_notifs[0][0][2]

    def test_notification_suppressed_when_zero_patches(self, db, conn, tmp_path):
        month = "2026-03"
        _populate_month(conn, month=month)
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=True)

        flag = _fake_flag()
        fake_output = [_declined_self_review_result(flag)]

        notify_calls = []
        with _with_fake_flags(flag):
            with patch("jobs.monthly_evaluation._run_self_review",
                       return_value=fake_output):
                with patch("notifications.notify",
                           side_effect=lambda *a, **kw: notify_calls.append((a, kw))):
                    p1, p2 = _noop_scorers()
                    with p1, p2:
                        _run_pipeline(month, f"{month}-01T00:00:00",
                                      f"{month}-31T23:59:59", settings)

        self_review_notifs = [
            c for c in notify_calls
            if c[1].get("tags") and "self_review" in c[1]["tags"]
        ]
        assert self_review_notifs == []

    def test_self_review_exception_pipeline_still_completes(
        self, db, conn, tmp_path
    ):
        month = "2026-03"
        _populate_month(conn, month=month)
        settings = _make_settings(db, tmp_path / "reports", self_review_enabled=True)

        flag = _fake_flag()
        with _with_fake_flags(flag):
            with patch("jobs.monthly_evaluation._run_self_review",
                       side_effect=RuntimeError("self-review boom")):
                p1, p2 = _noop_scorers()
                with p1, p2:
                    # Should not raise
                    _run_pipeline(month, f"{month}-01T00:00:00",
                                  f"{month}-31T23:59:59", settings)

        archive_path = tmp_path / "reports" / f"monthly_eval_{month}.md"
        assert archive_path.exists()
        assert "## Summary" in archive_path.read_text()


# ── _write_markdown_archive directly ─────────────────────────────────────────


class TestWriteMarkdownArchive:

    def _base_kwargs(self, tmp_path) -> dict:
        return {
            "month": "2026-03",
            "aggregate": {"by_strategy": {}, "overall": {}},
            "flags": [],
            "has_insufficient_sample": False,
            "decisions_scored": 10,
            "closed_trades": 3,
            "disagreement_rate": None,
            "created_at": "2026-04-01T05:00:00",
            "reports_dir": tmp_path / "reports",
        }

    def test_no_self_review_output_section_absent(self, tmp_path):
        kwargs = self._base_kwargs(tmp_path)
        _write_markdown_archive(**kwargs)
        content = (tmp_path / "reports" / "monthly_eval_2026-03.md").read_text()
        assert "## Self-Review" not in content

    def test_empty_self_review_output_section_absent(self, tmp_path):
        kwargs = self._base_kwargs(tmp_path)
        _write_markdown_archive(**kwargs, self_review_output=[])
        content = (tmp_path / "reports" / "monthly_eval_2026-03.md").read_text()
        assert "## Self-Review" not in content

    def test_with_self_review_output_section_present(self, tmp_path):
        kwargs = self._base_kwargs(tmp_path)
        flag = {"strategy": "wheel", "dimension": "confidence_calibration",
                "severity": "warning", "reason": "test",
                "lowest_scoring_decisions": [1, 2, 3]}
        sr_output = [
            {
                "flag": flag,
                "output": {
                    "flag_summary": "Summary here.",
                    "reasoning_failure_mode": "Failure mode here.",
                    "suggested_prompt_patches": [
                        {
                            "target_file": "prompts/system.md",
                            "target_section": "Section X",
                            "patch_type": "add",
                            "exact_text": "Add this text.",
                            "rationale": "Rationale here.",
                            "supporting_decision_ids": [1, 2, 3],
                        }
                    ],
                    "guardrail_migration_candidates": [],
                    "declined_to_suggest": [],
                },
                "skipped_reason": None,
            }
        ]
        _write_markdown_archive(**kwargs, self_review_output=sr_output)
        content = (tmp_path / "reports" / "monthly_eval_2026-03.md").read_text()
        assert "## Self-Review" in content
        assert "Summary here." in content
        assert "Add this text." in content

    def test_existing_sections_unchanged_when_self_review_absent(self, tmp_path):
        """Existing sections must be byte-identical when self_review_output is None."""
        kwargs = self._base_kwargs(tmp_path)
        _write_markdown_archive(**kwargs, self_review_output=None)
        content_none = (tmp_path / "reports" / "monthly_eval_2026-03.md").read_text()

        (tmp_path / "reports" / "monthly_eval_2026-03.md").unlink()
        _write_markdown_archive(**kwargs)
        content_default = (tmp_path / "reports" / "monthly_eval_2026-03.md").read_text()

        assert content_none == content_default
