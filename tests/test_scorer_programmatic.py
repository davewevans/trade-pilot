"""Tests for evaluation/scorer_programmatic.py and scoring_orchestrator.py.

Coverage:
- Rule-adherence scoring for each strategy type with a fabricated decision + context.
- Boundary case: delta exactly at the target edge → pass.
- Boundary case: DTE exactly at range boundary → pass.
- Skip-validity: valid enum + consistent context → pass.
- Skip-validity: inconsistent context (reason=earnings but context shows 45 days) → fail.
- Idempotence: scoring same decision twice writes only one row.
"""

import json
import pytest

from database.db import Database
from database.repositories.decision_scores_repository import DecisionScoresRepository
from evaluation import RUBRIC_VERSION
from evaluation.scorer_programmatic import ProgrammaticScorer
from evaluation.scoring_orchestrator import score_decisions_in_range
from strategies.skip_codes import SkipCode


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    d = Database(path=str(tmp_path / "scorer_test.db"))
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
def scorer():
    return ProgrammaticScorer()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decision(
    id=1,
    strategy_type="wheel",
    action="SELL_PUT",
    skip_reason_code=None,
    context=None,
    reasoning=None,
    timestamp="2026-04-10T10:00:00",
):
    """Build a minimal decision dict as returned by DecisionRepository."""
    return {
        "id": id,
        "strategy_type": strategy_type,
        "action": action,
        "skip_reason_code": skip_reason_code,
        "context": context or {},
        "reasoning": reasoning,
        "timestamp": timestamp,
        "underlying": "AAPL",
    }


def _wheel_csp_context(
    iv_rank=45,
    days_until_earnings=30,
    iv_environment="MODERATE",
):
    return {
        "iv_rank": iv_rank,
        "earnings": {"days_until_earnings": days_until_earnings},
        "iv_environment": iv_environment,
    }


def _reasoning(**kwargs):
    return kwargs


# ── ProgrammaticScorer.score_decision ─────────────────────────────────────────

class TestScorerReturnShape:

    def test_entry_decision_returns_rule_adherence(self, scorer):
        d = _decision(action="SELL_PUT")
        results = scorer.score_decision(d)
        assert len(results) == 1
        assert results[0]["dimension"] == "rule_adherence"

    def test_skip_decision_returns_skip_validity(self, scorer):
        d = _decision(
            action="SKIP",
            skip_reason_code=SkipCode.LOW_IVR,
            context={"iv_rank": 20},
        )
        results = scorer.score_decision(d)
        assert len(results) == 1
        assert results[0]["dimension"] == "skip_validity_structural"

    def test_hold_returns_empty(self, scorer):
        d = _decision(action="HOLD")
        assert scorer.score_decision(d) == []

    def test_close_returns_empty(self, scorer):
        d = _decision(action="CLOSE")
        assert scorer.score_decision(d) == []

    def test_result_has_required_keys(self, scorer):
        d = _decision(action="SELL_PUT")
        results = scorer.score_decision(d)
        r = results[0]
        assert "decision_id" in r
        assert "dimension" in r
        assert "score" in r
        assert "score_metadata" in r

    def test_score_is_fraction(self, scorer):
        d = _decision(action="SELL_PUT")
        results = scorer.score_decision(d)
        score = results[0]["score"]
        assert 0.0 <= score <= 1.0


# ── Rule adherence — wheel CSP ────────────────────────────────────────────────

class TestWheelCSPRuleAdherence:

    def test_all_rules_pass_gives_score_1(self, scorer):
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(iv_rank=45, days_until_earnings=30),
            reasoning=_reasoning(delta=0.25, dte=28, open_interest=300),
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_ivr_below_floor_fails_rule(self, scorer):
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(iv_rank=20),
            reasoning=_reasoning(delta=0.25, dte=28),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        ivr_rule = next(r for r in meta["details"] if r["rule"] == "ivr_floor")
        assert ivr_rule["passed"] is False
        assert results[0]["score"] < 1.0

    def test_earnings_too_close_fails_rule(self, scorer):
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(days_until_earnings=10),
            reasoning=_reasoning(delta=0.25, dte=28, open_interest=300),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        earnings_rule = next(r for r in meta["details"] if r["rule"] == "earnings_buffer")
        assert earnings_rule["passed"] is False

    def test_delta_out_of_range_fails_rule(self, scorer):
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(),
            reasoning=_reasoning(delta=0.45, dte=28),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        delta_rule = next(r for r in meta["details"] if r["rule"] == "delta_range")
        assert delta_rule["passed"] is False

    def test_dte_out_of_range_fails_rule(self, scorer):
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(),
            reasoning=_reasoning(delta=0.25, dte=50),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        dte_rule = next(r for r in meta["details"] if r["rule"] == "dte_range")
        assert dte_rule["passed"] is False

    def test_missing_data_skipped_not_penalised(self, scorer):
        """When delta/DTE/OI are not available they should be skipped, not failed."""
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(iv_rank=45, days_until_earnings=30),
            reasoning=None,
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        # Only IVR and earnings should be checked
        checked_rules = {r["rule"] for r in meta["details"]}
        assert "ivr_floor" in checked_rules
        assert "earnings_buffer" in checked_rules
        # delta/dte/open_interest should be skipped
        assert "delta_range" in meta["skipped_rules"]
        assert "dte_range" in meta["skipped_rules"]


# ── Boundary cases ────────────────────────────────────────────────────────────

class TestBoundaryBehaviour:

    def test_delta_exactly_at_min_boundary_is_pass(self, scorer):
        """delta = delta_min = 0.20 → pass (boundary is inclusive)."""
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(),
            reasoning=_reasoning(delta=0.20, dte=28),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        delta_rule = next(r for r in meta["details"] if r["rule"] == "delta_range")
        assert delta_rule["passed"] is True, "delta at lower boundary must pass"

    def test_delta_exactly_at_max_boundary_is_pass(self, scorer):
        """delta = delta_max = 0.30 → pass (boundary is inclusive)."""
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(),
            reasoning=_reasoning(delta=0.30, dte=28),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        delta_rule = next(r for r in meta["details"] if r["rule"] == "delta_range")
        assert delta_rule["passed"] is True, "delta at upper boundary must pass"

    def test_dte_exactly_at_min_boundary_is_pass(self, scorer):
        """dte = dte_min = 21 → pass (boundary is inclusive)."""
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(),
            reasoning=_reasoning(delta=0.25, dte=21),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        dte_rule = next(r for r in meta["details"] if r["rule"] == "dte_range")
        assert dte_rule["passed"] is True, "dte at lower boundary must pass"

    def test_dte_exactly_at_max_boundary_is_pass(self, scorer):
        """dte = dte_max = 35 → pass (boundary is inclusive)."""
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(),
            reasoning=_reasoning(delta=0.25, dte=35),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        dte_rule = next(r for r in meta["details"] if r["rule"] == "dte_range")
        assert dte_rule["passed"] is True, "dte at upper boundary must pass"

    def test_ivr_exactly_at_minimum_is_pass(self, scorer):
        """iv_rank = iv_rank_min = 30 → pass (boundary is inclusive)."""
        d = _decision(
            strategy_type="wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(iv_rank=30),
            reasoning=None,
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        ivr_rule = next(r for r in meta["details"] if r["rule"] == "ivr_floor")
        assert ivr_rule["passed"] is True, "iv_rank at minimum boundary must pass"


# ── Rule adherence — other strategy types ─────────────────────────────────────

class TestBullPutSpreadRuleAdherence:

    def test_all_rules_pass(self, scorer):
        d = _decision(
            strategy_type="bull_put_spread",
            action="OPEN",
            context={
                "iv_environment": "MODERATE",
                "fundamentals": {"days_to_earnings": 30},
            },
            reasoning=_reasoning(delta=0.25, dte=28, net_credit=0.65),
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_iv_env_mismatch_fails(self, scorer):
        d = _decision(
            strategy_type="bull_put_spread",
            action="OPEN",
            context={
                "iv_environment": "LOW",  # not allowed for bull_put_spread
                "fundamentals": {"days_to_earnings": 30},
            },
            reasoning=_reasoning(delta=0.25, dte=28, net_credit=0.65),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        env_rule = next(r for r in meta["details"] if r["rule"] == "iv_environment")
        assert env_rule["passed"] is False


class TestLongCallVerticalRuleAdherence:

    def test_regime_bull_required(self, scorer):
        d = _decision(
            strategy_type="long_call_vertical",
            action="OPEN",
            context={
                "confirmed_market_regime": "BEAR",
                "iv_environment": "LOW",
            },
            reasoning=_reasoning(delta=0.50, dte=40, net_debit=1.00),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        regime_rule = next(r for r in meta["details"] if r["rule"] == "regime_bull")
        assert regime_rule["passed"] is False

    def test_bull_regime_and_low_iv_pass(self, scorer):
        d = _decision(
            strategy_type="long_call_vertical",
            action="OPEN",
            context={
                "confirmed_market_regime": "BULL",
                "iv_environment": "LOW",
                "fundamentals": {"days_to_earnings": 60},
            },
            reasoning=_reasoning(delta=0.50, dte=40, net_debit=1.00),
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)


class TestIronCondorRuleAdherence:

    def test_neutral_regime_and_high_iv_required(self, scorer):
        d = _decision(
            strategy_type="iron_condor",
            action="OPEN",
            context={
                "confirmed_market_regime": "BULL",  # wrong
                "iv_environment": "MODERATE",        # wrong
                "iv_rank": 60,
                "fundamentals": {"days_to_earnings": 45},
            },
            reasoning=_reasoning(delta=0.20, dte=30, total_credit=1.20),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        regime_rule = next(r for r in meta["details"] if r["rule"] == "regime_neutral")
        env_rule = next(r for r in meta["details"] if r["rule"] == "iv_environment_high")
        assert regime_rule["passed"] is False
        assert env_rule["passed"] is False

    def test_all_rules_pass(self, scorer):
        d = _decision(
            strategy_type="iron_condor",
            action="OPEN",
            context={
                "confirmed_market_regime": "NEUTRAL",
                "iv_environment": "HIGH",
                "iv_rank": 60,
                "fundamentals": {"days_to_earnings": 45},
            },
            reasoning=_reasoning(delta=0.20, dte=30, total_credit=1.20),
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)


class TestTurnoverWheelRuleAdherence:

    def test_csp_uses_turnover_wheel_params(self, scorer):
        """Turnover wheel CSP has max_position_pct_of_bp=5 (not 10)."""
        d = _decision(
            strategy_type="turnover_wheel",
            action="SELL_PUT",
            context=_wheel_csp_context(iv_rank=35, days_until_earnings=30),
            reasoning=_reasoning(delta=0.25, dte=25, open_interest=250),
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_cc_delta_range_is_skipped_no_cap(self, scorer):
        """Turnover wheel CC has no delta cap — delta_range rule must be skipped, not failed."""
        d = _decision(
            strategy_type="turnover_wheel",
            action="SELL_CALL",
            context={
                "earnings": {"days_until_earnings": 30},
                "turnover_wheel_cost_basis": {"effective_cost_basis": 100.0},
            },
            reasoning=_reasoning(delta=0.60, dte=10, strike=105.0),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        assert "delta_range" in meta["skipped_rules"], (
            "delta_range must be skipped for turnover_wheel CC (no delta cap defined)"
        )


class TestCalendarSpreadRuleAdherence:

    def test_high_iv_fails(self, scorer):
        d = _decision(
            strategy_type="calendar_spread",
            action="OPEN",
            context={
                "iv_environment": "HIGH",   # not allowed
                "fundamentals": {"days_to_earnings": 60},
            },
            reasoning=_reasoning(dte=25, net_debit=1.00),
        )
        results = scorer.score_decision(d)
        meta = results[0]["score_metadata"]
        env_rule = next(r for r in meta["details"] if r["rule"] == "iv_environment")
        assert env_rule["passed"] is False


# ── Skip-validity structural ───────────────────────────────────────────────────

class TestSkipValidityStructural:

    def test_valid_code_with_consistent_context_passes(self, scorer):
        """EARNINGS_TOO_CLOSE + earnings in 10 days → consistent (pass)."""
        d = _decision(
            action="SKIP",
            strategy_type="wheel",
            skip_reason_code=SkipCode.EARNINGS_TOO_CLOSE,
            context={"earnings": {"days_until_earnings": 10}},
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_valid_code_inconsistent_earnings_context_fails(self, scorer):
        """EARNINGS_TOO_CLOSE + earnings 45 days away → inconsistent (fail)."""
        d = _decision(
            action="SKIP",
            strategy_type="wheel",
            skip_reason_code=SkipCode.EARNINGS_TOO_CLOSE,
            context={"earnings": {"days_until_earnings": 45}},
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(0.0)
        meta = results[0]["score_metadata"]
        ctx_check = next(
            c for c in meta["context_checks"]
            if c["check"] == "earnings_within_block_window"
        )
        assert ctx_check["passed"] is False
        assert ctx_check["observed"] == 45

    def test_invalid_code_fails(self, scorer):
        """An unknown skip_reason_code (not in SkipCode.ALL) → fail."""
        d = _decision(
            action="SKIP",
            skip_reason_code="NOT_A_REAL_CODE",
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(0.0)
        meta = results[0]["score_metadata"]
        code_check = next(c for c in meta["details"] if c["check"] == "code_is_valid_enum")
        assert code_check["passed"] is False

    def test_low_ivr_code_consistent_context_passes(self, scorer):
        """LOW_IVR + iv_rank=20 (below 30 floor) → consistent (pass)."""
        d = _decision(
            action="SKIP",
            strategy_type="wheel",
            skip_reason_code=SkipCode.LOW_IVR,
            context={"iv_rank": 20},
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_low_ivr_code_inconsistent_context_fails(self, scorer):
        """LOW_IVR + iv_rank=55 (above floor) → inconsistent (fail)."""
        d = _decision(
            action="SKIP",
            strategy_type="wheel",
            skip_reason_code=SkipCode.LOW_IVR,
            context={"iv_rank": 55},
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(0.0)

    def test_regime_mismatch_consistent_passes(self, scorer):
        """REGIME_MISMATCH + regime=BEAR for long_call_vertical (needs BULL) → pass."""
        d = _decision(
            action="SKIP",
            strategy_type="long_call_vertical",
            skip_reason_code=SkipCode.REGIME_MISMATCH,
            context={"confirmed_market_regime": "BEAR"},
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_confidence_low_always_passes(self, scorer):
        """CONFIDENCE_LOW has no context consistency check — always passes."""
        d = _decision(
            action="SKIP",
            skip_reason_code=SkipCode.CONFIDENCE_LOW,
            context={},
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_other_code_always_passes(self, scorer):
        d = _decision(
            action="SKIP",
            skip_reason_code=SkipCode.OTHER,
            context={},
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)

    def test_missing_earnings_context_gives_benefit_of_doubt(self, scorer):
        """When earnings data is absent, EARNINGS_TOO_CLOSE context check passes."""
        d = _decision(
            action="SKIP",
            strategy_type="wheel",
            skip_reason_code=SkipCode.EARNINGS_TOO_CLOSE,
            context={},   # no earnings data
        )
        results = scorer.score_decision(d)
        assert results[0]["score"] == pytest.approx(1.0)


# ── Orchestrator idempotency ───────────────────────────────────────────────────

class TestScoringOrchestratorIdempotency:

    def _insert_decision(self, conn, id=1, action="SELL_PUT", timestamp="2026-04-10T10:00:00"):
        """Insert a minimal decision row directly."""
        conn.execute(
            """
            INSERT INTO decisions (
                id, timestamp, strategy_type, underlying, action
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (id, timestamp, "wheel", "AAPL", action),
        )
        conn.commit()

    def test_scoring_same_decision_twice_writes_one_row(self, conn, scores_repo):
        self._insert_decision(conn, id=1, action="SELL_PUT")
        scorer = ProgrammaticScorer()

        count1 = score_decisions_in_range(
            "2026-04-10T00:00:00", "2026-04-10T23:59:59",
            scorer, scores_repo,
        )
        count2 = score_decisions_in_range(
            "2026-04-10T00:00:00", "2026-04-10T23:59:59",
            scorer, scores_repo,
        )

        assert count1 == 1, "first run should write 1 row"
        assert count2 == 0, "second run should write 0 rows (idempotent)"

        rows = scores_repo.get_by_decision(1)
        assert len(rows) == 1, "exactly one score row should exist"

    def test_score_row_has_correct_rubric_version(self, conn, scores_repo):
        self._insert_decision(conn, id=2, action="SELL_PUT")
        scorer = ProgrammaticScorer()
        score_decisions_in_range(
            "2026-04-10T00:00:00", "2026-04-10T23:59:59",
            scorer, scores_repo,
        )
        rows = scores_repo.get_by_decision(2)
        assert rows[0]["rubric_version"] == RUBRIC_VERSION

    def test_score_row_has_correct_scorer_type(self, conn, scores_repo):
        self._insert_decision(conn, id=3, action="SKIP")
        # Insert a skip_reason_code directly
        conn.execute(
            "UPDATE decisions SET skip_reason_code = ? WHERE id = 3",
            (SkipCode.LOW_IVR,),
        )
        conn.commit()

        scorer = ProgrammaticScorer()
        score_decisions_in_range(
            "2026-04-10T00:00:00", "2026-04-10T23:59:59",
            scorer, scores_repo,
        )
        rows = scores_repo.get_by_decision(3)
        assert rows[0]["scorer_type"] == ProgrammaticScorer.SCORER_TYPE

    def test_hold_decisions_are_not_written(self, conn, scores_repo):
        """HOLD decisions have no applicable dimension — no row written."""
        self._insert_decision(conn, id=4, action="HOLD")
        scorer = ProgrammaticScorer()
        count = score_decisions_in_range(
            "2026-04-10T00:00:00", "2026-04-10T23:59:59",
            scorer, scores_repo,
        )
        assert count == 0
        assert scores_repo.get_by_decision(4) == []

    def test_dimension_scores_json_contains_dimension_name(self, conn, scores_repo):
        self._insert_decision(conn, id=5, action="SELL_PUT")
        scorer = ProgrammaticScorer()
        score_decisions_in_range(
            "2026-04-10T00:00:00", "2026-04-10T23:59:59",
            scorer, scores_repo,
        )
        rows = scores_repo.get_by_decision(5)
        dims = json.loads(rows[0]["dimension_scores_json"])
        assert any(d["dimension"] == "rule_adherence" for d in dims)


# ── SCHEMA_INVALID skip-validity structural check ──────────────────────────────

class TestSchemaInvalidSkipValidity:

    def test_schema_invalid_skip_passes_structural_check(self, scorer):
        """SCHEMA_INVALID is a valid SkipCode and has no context check — always scores 1.0."""
        d = _decision(
            action="SKIP",
            skip_reason_code=SkipCode.SCHEMA_INVALID,
            context={},
        )
        results = scorer.score_decision(d)
        assert len(results) == 1
        r = results[0]
        assert r["dimension"] == "skip_validity_structural"
        assert r["score"] == 1.0
        assert r["score_metadata"]["skip_reason_code"] == SkipCode.SCHEMA_INVALID

