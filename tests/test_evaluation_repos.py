"""Tests for the offline-evaluation repository layer.

Covers:
  - decision_scores: insert/get roundtrips, spot-check queue filter,
    mark_spot_check_submitted, mark_for_spot_check
  - monthly_evaluations: insert/get roundtrip, uniqueness constraint,
    mark_reviewed
  - judge_spot_checks: insert/get roundtrip, get_by_month join,
    get_disagreement_rate with mixed verdicts
"""

import pytest

from database.db import Database
from database.repositories.decision_scores_repository import DecisionScoresRepository
from database.repositories.judge_spot_checks_repository import JudgeSpotChecksRepository
from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    d = Database(path=str(tmp_path / "eval_test.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def conn(db):
    return db.get_connection()


@pytest.fixture
def scores_repo(conn):
    return DecisionScoresRepository(conn)


@pytest.fixture
def evals_repo(conn):
    return MonthlyEvaluationsRepository(conn)


@pytest.fixture
def checks_repo(conn):
    return JudgeSpotChecksRepository(conn)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _score(decision_id=1, scorer_type="automated_rubric", scored_at="2026-04-15T10:00:00",
           total_score=80.0, max_score=100.0, **kwargs):
    return dict(
        decision_id=decision_id,
        scorer_type=scorer_type,
        scored_at=scored_at,
        total_score=total_score,
        max_score=max_score,
        **kwargs,
    )


def _eval(month="2026-04", decisions_evaluated=5, avg_score=78.0,
          created_at="2026-04-30T23:59:59", **kwargs):
    return dict(
        month=month,
        decisions_evaluated=decisions_evaluated,
        avg_score=avg_score,
        created_at=created_at,
        **kwargs,
    )


def _check(decision_score_id, submitted_at="2026-04-20T12:00:00", **kwargs):
    return dict(decision_score_id=decision_score_id, submitted_at=submitted_at, **kwargs)


# ── decision_scores ───────────────────────────────────────────────────────────

class TestDecisionScoresRepository:

    def test_insert_returns_id(self, scores_repo):
        row_id = scores_repo.insert(_score())
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_get_by_decision_roundtrip(self, scores_repo):
        scores_repo.insert(_score(decision_id=42, total_score=75.0, notes="good"))
        rows = scores_repo.get_by_decision(42)
        assert len(rows) == 1
        assert rows[0]["decision_id"] == 42
        assert rows[0]["total_score"] == 75.0
        assert rows[0]["notes"] == "good"

    def test_get_by_decision_empty(self, scores_repo):
        assert scores_repo.get_by_decision(999) == []

    def test_get_by_decision_multiple(self, scores_repo):
        scores_repo.insert(_score(decision_id=10, scorer_type="automated_rubric"))
        scores_repo.insert(_score(decision_id=10, scorer_type="judge_manual"))
        rows = scores_repo.get_by_decision(10)
        assert len(rows) == 2
        types = {r["scorer_type"] for r in rows}
        assert types == {"automated_rubric", "judge_manual"}

    def test_get_by_month_filters_correctly(self, scores_repo):
        scores_repo.insert(_score(decision_id=1, scored_at="2026-04-10T09:00:00"))
        scores_repo.insert(_score(decision_id=2, scored_at="2026-04-22T14:00:00"))
        scores_repo.insert(_score(decision_id=3, scored_at="2026-05-01T08:00:00"))

        april = scores_repo.get_by_month("2026-04")
        assert len(april) == 2
        for r in april:
            assert r["scored_at"].startswith("2026-04")

        may = scores_repo.get_by_month("2026-05")
        assert len(may) == 1

    def test_spot_check_queue_filters_pending(self, scores_repo):
        # pending in target month
        id1 = scores_repo.insert(_score(decision_id=1, scored_at="2026-04-01T00:00:00",
                                         spot_check_pending=1))
        # not pending
        scores_repo.insert(_score(decision_id=2, scored_at="2026-04-02T00:00:00",
                                   spot_check_pending=0))
        # pending but wrong month
        scores_repo.insert(_score(decision_id=3, scored_at="2026-05-01T00:00:00",
                                   spot_check_pending=1))

        queue = scores_repo.get_spot_check_queue("2026-04")
        assert len(queue) == 1
        assert queue[0]["id"] == id1

    def test_spot_check_queue_respects_limit(self, scores_repo):
        for i in range(5):
            scores_repo.insert(_score(
                decision_id=i,
                scored_at=f"2026-04-{i+1:02d}T00:00:00",
                spot_check_pending=1,
            ))
        queue = scores_repo.get_spot_check_queue("2026-04", limit=3)
        assert len(queue) == 3

    def test_mark_spot_check_submitted_clears_flag(self, scores_repo):
        row_id = scores_repo.insert(_score(spot_check_pending=1))
        scores_repo.mark_spot_check_submitted(row_id)

        rows = scores_repo.get_by_decision(1)
        row = next(r for r in rows if r["id"] == row_id)
        assert row["spot_check_pending"] == 0
        assert row["spot_check_submitted_at"] is not None

    def test_mark_for_spot_check_sets_flag(self, scores_repo):
        id1 = scores_repo.insert(_score(decision_id=1, spot_check_pending=0))
        id2 = scores_repo.insert(_score(decision_id=2, spot_check_pending=0))

        scores_repo.mark_for_spot_check([id1, id2])

        for did, sid in ((1, id1), (2, id2)):
            rows = scores_repo.get_by_decision(did)
            row = next(r for r in rows if r["id"] == sid)
            assert row["spot_check_pending"] == 1

    def test_mark_for_spot_check_empty_list_is_noop(self, scores_repo):
        # Should not raise
        scores_repo.mark_for_spot_check([])

    def test_optional_fields_nullable(self, scores_repo):
        row_id = scores_repo.insert(_score())
        rows = scores_repo.get_by_decision(1)
        assert rows[0]["rubric_version"] is None
        assert rows[0]["dimension_scores_json"] is None
        assert rows[0]["pass_fail"] is None
        assert rows[0]["notes"] is None
        assert rows[0]["spot_check_submitted_at"] is None


# ── monthly_evaluations ───────────────────────────────────────────────────────

class TestMonthlyEvaluationsRepository:

    def test_insert_returns_id(self, evals_repo):
        row_id = evals_repo.insert(_eval())
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_get_by_month_roundtrip(self, evals_repo):
        evals_repo.insert(_eval(month="2026-03", decisions_evaluated=12, avg_score=82.5))
        row = evals_repo.get_by_month("2026-03")
        assert row is not None
        assert row["month"] == "2026-03"
        assert row["decisions_evaluated"] == 12
        assert row["avg_score"] == 82.5

    def test_get_by_month_missing(self, evals_repo):
        assert evals_repo.get_by_month("2020-01") is None

    def test_unique_month_constraint(self, evals_repo):
        import sqlite3 as _sqlite3
        evals_repo.insert(_eval(month="2026-04"))
        with pytest.raises(_sqlite3.IntegrityError):
            evals_repo.insert(_eval(month="2026-04"))

    def test_get_list_newest_first(self, evals_repo):
        for m in ["2026-01", "2026-03", "2026-02"]:
            evals_repo.insert(_eval(month=m))
        rows = evals_repo.get_list()
        months = [r["month"] for r in rows]
        assert months == ["2026-03", "2026-02", "2026-01"]

    def test_get_list_pagination(self, evals_repo):
        for i in range(1, 6):
            evals_repo.insert(_eval(month=f"2026-{i:02d}"))
        page1 = evals_repo.get_list(limit=2, offset=0)
        page2 = evals_repo.get_list(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["month"] != page2[0]["month"]

    def test_mark_reviewed(self, evals_repo):
        evals_repo.insert(_eval(month="2026-04"))
        evals_repo.mark_reviewed("2026-04", "no action required")
        row = evals_repo.get_by_month("2026-04")
        assert row["reviewed_at"] is not None
        assert row["action_note"] == "no action required"

    def test_mark_reviewed_null_action_note(self, evals_repo):
        evals_repo.insert(_eval(month="2026-04"))
        evals_repo.mark_reviewed("2026-04", None)
        row = evals_repo.get_by_month("2026-04")
        assert row["reviewed_at"] is not None
        assert row["action_note"] is None

    def test_optional_fields_nullable(self, evals_repo):
        evals_repo.insert(_eval(month="2026-06"))
        row = evals_repo.get_by_month("2026-06")
        assert row["pct_pass"] is None
        assert row["score_distribution_json"] is None
        assert row["flags_json"] is None
        assert row["reviewed_at"] is None
        assert row["action_note"] is None


# ── judge_spot_checks ─────────────────────────────────────────────────────────

class TestJudgeSpotChecksRepository:

    def _setup_score(self, scores_repo, decision_id=1, scored_at="2026-04-10T10:00:00"):
        return scores_repo.insert(_score(decision_id=decision_id, scored_at=scored_at))

    def test_insert_returns_id(self, scores_repo, checks_repo):
        score_id = self._setup_score(scores_repo)
        check_id = checks_repo.insert(_check(score_id))
        assert isinstance(check_id, int)
        assert check_id > 0

    def test_get_by_month_roundtrip(self, scores_repo, checks_repo):
        score_id = self._setup_score(scores_repo, scored_at="2026-04-10T10:00:00")
        checks_repo.insert(_check(score_id, operator_verdict="agree"))
        rows = checks_repo.get_by_month("2026-04")
        assert len(rows) == 1
        assert rows[0]["operator_verdict"] == "agree"

    def test_get_by_month_joins_correctly(self, scores_repo, checks_repo):
        score_april = self._setup_score(scores_repo, decision_id=1, scored_at="2026-04-10T00:00:00")
        score_may   = self._setup_score(scores_repo, decision_id=2, scored_at="2026-05-10T00:00:00")
        checks_repo.insert(_check(score_april, operator_verdict="agree"))
        checks_repo.insert(_check(score_may,   operator_verdict="disagree"))

        april_rows = checks_repo.get_by_month("2026-04")
        may_rows   = checks_repo.get_by_month("2026-05")
        assert len(april_rows) == 1
        assert april_rows[0]["operator_verdict"] == "agree"
        assert len(may_rows) == 1
        assert may_rows[0]["operator_verdict"] == "disagree"

    def test_disagreement_rate_all_agree(self, scores_repo, checks_repo):
        for i in range(3):
            sid = self._setup_score(scores_repo, decision_id=i,
                                     scored_at=f"2026-04-{i+1:02d}T00:00:00")
            checks_repo.insert(_check(sid, operator_verdict="agree"))
        rate = checks_repo.get_disagreement_rate("2026-04")
        assert rate == 0.0

    def test_disagreement_rate_all_disagree(self, scores_repo, checks_repo):
        for i in range(2):
            sid = self._setup_score(scores_repo, decision_id=i,
                                     scored_at=f"2026-04-{i+1:02d}T00:00:00")
            checks_repo.insert(_check(sid, operator_verdict="disagree"))
        rate = checks_repo.get_disagreement_rate("2026-04")
        assert rate == 1.0

    def test_disagreement_rate_mixed(self, scores_repo, checks_repo):
        verdicts = ["agree", "disagree", "disagree", "agree"]
        for i, verdict in enumerate(verdicts):
            sid = self._setup_score(scores_repo, decision_id=i,
                                     scored_at=f"2026-04-{i+1:02d}T00:00:00")
            checks_repo.insert(_check(sid, operator_verdict=verdict))
        rate = checks_repo.get_disagreement_rate("2026-04")
        assert abs(rate - 0.5) < 1e-9

    def test_disagreement_rate_excludes_null_verdict(self, scores_repo, checks_repo):
        sid1 = self._setup_score(scores_repo, decision_id=1, scored_at="2026-04-01T00:00:00")
        sid2 = self._setup_score(scores_repo, decision_id=2, scored_at="2026-04-02T00:00:00")
        checks_repo.insert(_check(sid1, operator_verdict="disagree"))
        checks_repo.insert(_check(sid2, operator_verdict=None))   # not yet adjudicated
        # Only the row with a verdict counts → 1/1 = 1.0
        rate = checks_repo.get_disagreement_rate("2026-04")
        assert rate == 1.0

    def test_disagreement_rate_no_checks_returns_none(self, checks_repo):
        assert checks_repo.get_disagreement_rate("2099-01") is None

    def test_disagreement_rate_only_null_verdicts_returns_none(self, scores_repo, checks_repo):
        sid = self._setup_score(scores_repo, scored_at="2026-04-01T00:00:00")
        checks_repo.insert(_check(sid, operator_verdict=None))
        assert checks_repo.get_disagreement_rate("2026-04") is None

    def test_optional_fields_nullable(self, scores_repo, checks_repo):
        score_id = self._setup_score(scores_repo)
        check_id = checks_repo.insert(_check(score_id))
        rows = checks_repo.get_by_month("2026-04")
        row = next(r for r in rows if r["id"] == check_id)
        assert row["judge_score"] is None
        assert row["operator_verdict"] is None
        assert row["verdict_notes"] is None
        assert row["checked_at"] is None


# ── Schema idempotency ────────────────────────────────────────────────────────

def test_init_schema_idempotent(tmp_path):
    """Running init_schema twice on the same database must not raise."""
    db = Database(path=str(tmp_path / "idem.db"))
    db.init_schema()
    db.init_schema()   # second call should be a no-op
    db.close()


def test_all_three_tables_created(tmp_path):
    """All three evaluation tables must exist after init_schema."""
    db = Database(path=str(tmp_path / "tables.db"))
    db.init_schema()
    conn = db.get_connection()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "decision_scores" in tables
    assert "monthly_evaluations" in tables
    assert "judge_spot_checks" in tables
    db.close()
