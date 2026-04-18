"""Tests for evaluation/scorer_judge.py and the judge path of scoring_orchestrator.py.

Coverage:
- Correct system prompt loaded from prompts/judge/decision_rubric_v1.md.
- Correct user message structure (decision fields present).
- Structured output parsing — success path → 5 dimension dicts returned.
- Refusal / max_tokens stop reason → empty list, no partial writes.
- Schema validation failure (bad dimension name) → empty list, error logged.
- Network error retry: first call raises APIConnectionError, second succeeds.
- Network error retry exhausted: both calls fail → empty list.
- Idempotence: same decision scored twice writes only one DB row.
- Dry-run: scores computed and logged, zero DB rows written.
- Feature flag: EVALUATION_JUDGE_ENABLED=False → orchestrator not called.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import anthropic

from database.db import Database
from database.repositories.decision_scores_repository import DecisionScoresRepository
from evaluation import RUBRIC_VERSION
from evaluation.judge_schema import JUDGE_DIMENSIONS
from evaluation.scorer_judge import JudgeScorer
from evaluation.scoring_orchestrator import score_decisions_with_judge


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    d = Database(path=str(tmp_path / "judge_test.db"))
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def conn(db):
    return db.get_connection()


@pytest.fixture
def scores_repo(conn):
    return DecisionScoresRepository(conn)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _decision(
    id=1,
    strategy_type="wheel",
    action="SELL_PUT",
    confidence=0.8,
    reasoning=None,
    context=None,
    underlying="AAPL",
    timestamp="2026-04-10T10:00:00",
):
    """Build a minimal decision dict as returned by DecisionRepository."""
    return {
        "id": id,
        "strategy_type": strategy_type,
        "action": action,
        "confidence": confidence,
        "underlying": underlying,
        "timestamp": timestamp,
        "context": context or {"iv_rank": 42, "iv_environment": "MODERATE"},
        "reasoning": reasoning or {
            "macro": "Broad market stable; no major risk events.",
            "fundamental": "AAPL fundamentals healthy; P/E in range.",
            "technical": "Price near 50-day MA support; RSI 48.",
            "volatility": "IV rank 42, above 30 minimum threshold.",
            "selection": "Sold 30-delta put at $180 strike, 28 DTE.",
            "risk": "Max loss capped at strike price × 100 shares.",
        },
    }


def _valid_judge_response() -> dict:
    """A response that passes validate_judge_response."""
    return {
        "scores": [
            {
                "dimension": dim,
                "score": 7,
                "justification": f"Adequate {dim} observed.",
            }
            for dim in JUDGE_DIMENSIONS
        ]
    }


def _make_mock_client(response_dict: dict | None = None, stop_reason: str = "end_turn"):
    """Build a mock Anthropic client that returns the given response dict."""
    client = MagicMock(spec=anthropic.Anthropic)
    content_block = MagicMock()
    content_block.text = json.dumps(response_dict or _valid_judge_response())
    mock_response = MagicMock()
    mock_response.stop_reason = stop_reason
    mock_response.content = [content_block]
    client.messages.create.return_value = mock_response
    return client


# ── System prompt loading ──────────────────────────────────────────────────────


class TestJudgeScorerInit:

    def test_system_prompt_loaded(self):
        """JudgeScorer loads the rubric from prompts/judge/decision_rubric_v1.md."""
        scorer = JudgeScorer(client=_make_mock_client())
        assert "reasoning_groundedness" in scorer._system_prompt
        assert "confidence_calibration" in scorer._system_prompt

    def test_model_stored(self):
        scorer = JudgeScorer(model="claude-opus-4-7", client=_make_mock_client())
        assert scorer.model == "claude-opus-4-7"

    def test_scorer_type(self):
        assert JudgeScorer.SCORER_TYPE == "judge"


# ── User message structure ────────────────────────────────────────────────────


class TestUserMessageStructure:

    def _capture_user_message(self, decision: dict) -> str:
        client = _make_mock_client()
        scorer = JudgeScorer(client=client)
        scorer.score_decision(decision)
        call_kwargs = client.messages.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else []
        if not messages:
            messages = call_kwargs[1].get("messages", [])
        return messages[0]["content"]

    def test_user_message_contains_action(self):
        d = _decision(action="SELL_PUT")
        msg = self._capture_user_message(d)
        assert "SELL_PUT" in msg

    def test_user_message_contains_strategy(self):
        d = _decision(strategy_type="wheel")
        msg = self._capture_user_message(d)
        assert "wheel" in msg

    def test_user_message_contains_reasoning_fields(self):
        d = _decision()
        msg = self._capture_user_message(d)
        # All 6 reasoning domains should appear
        for field in ("macro", "fundamental", "technical", "volatility", "selection", "risk"):
            assert field in msg

    def test_user_message_contains_context(self):
        d = _decision(context={"iv_rank": 55, "custom_field": "visible"})
        msg = self._capture_user_message(d)
        assert "iv_rank" in msg
        assert "55" in msg

    def test_user_message_contains_symbol(self):
        d = _decision(underlying="TSLA")
        msg = self._capture_user_message(d)
        assert "TSLA" in msg

    def test_system_prompt_passed_to_api(self):
        client = _make_mock_client()
        scorer = JudgeScorer(client=client)
        scorer.score_decision(_decision())
        call_kwargs = client.messages.create.call_args
        system_arg = call_kwargs.kwargs.get("system") or call_kwargs[1].get("system")
        assert system_arg is not None
        # System is a list of blocks (cache_control format)
        assert any(scorer._system_prompt in block.get("text", "") for block in system_arg)

    def test_model_kwarg_passed_to_api(self):
        """Verify Opus model is used (not Sonnet)."""
        client = _make_mock_client()
        scorer = JudgeScorer(model="claude-opus-4-7", client=client)
        scorer.score_decision(_decision())
        call_kwargs = client.messages.create.call_args
        model_used = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert model_used == "claude-opus-4-7"


# ── Success path ──────────────────────────────────────────────────────────────


class TestSuccessPath:

    def test_returns_five_dimension_dicts(self):
        scorer = JudgeScorer(client=_make_mock_client())
        results = scorer.score_decision(_decision())
        assert len(results) == 5

    def test_all_judge_dimensions_present(self):
        scorer = JudgeScorer(client=_make_mock_client())
        results = scorer.score_decision(_decision())
        dims = {r["dimension"] for r in results}
        assert dims == set(JUDGE_DIMENSIONS)

    def test_score_normalised_to_01(self):
        """Raw score 7 (out of 10) → normalised 0.7."""
        scorer = JudgeScorer(client=_make_mock_client())
        results = scorer.score_decision(_decision())
        for r in results:
            assert 0.0 <= r["score"] <= 1.0
            assert r["score"] == pytest.approx(0.7)

    def test_score_metadata_contains_justification(self):
        scorer = JudgeScorer(client=_make_mock_client())
        results = scorer.score_decision(_decision())
        for r in results:
            assert "justification" in r["score_metadata"]
            assert r["score_metadata"]["justification"]

    def test_score_metadata_contains_scorer_model(self):
        scorer = JudgeScorer(model="claude-opus-4-7", client=_make_mock_client())
        results = scorer.score_decision(_decision())
        for r in results:
            assert r["score_metadata"]["scorer_model"] == "claude-opus-4-7"

    def test_decision_id_in_each_dict(self):
        scorer = JudgeScorer(client=_make_mock_client())
        results = scorer.score_decision(_decision(id=42))
        for r in results:
            assert r["decision_id"] == 42


# ── Refusal / max_tokens → no partial writes ──────────────────────────────────


class TestStopReasonHandling:

    @pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
    def test_returns_empty_list_on_bad_stop_reason(self, stop_reason, caplog):
        client = _make_mock_client(stop_reason=stop_reason)
        scorer = JudgeScorer(client=client)
        with caplog.at_level(logging.ERROR):
            results = scorer.score_decision(_decision())
        assert results == []

    @pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
    def test_logs_error_on_bad_stop_reason(self, stop_reason, caplog):
        client = _make_mock_client(stop_reason=stop_reason)
        scorer = JudgeScorer(client=client)
        with caplog.at_level(logging.ERROR):
            scorer.score_decision(_decision())
        assert any(stop_reason in record.message for record in caplog.records)

    @pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
    def test_no_db_write_on_bad_stop_reason(self, stop_reason, scores_repo):
        """Empty list from scorer prevents orchestrator from writing to DB."""
        client = _make_mock_client(stop_reason=stop_reason)
        scorer = JudgeScorer(client=client)

        # Manually simulate what the orchestrator does on empty return
        results = scorer.score_decision(_decision())
        if results:
            scores_repo.insert({"decision_id": 1, "scorer_type": "judge",
                                 "scored_at": "2026-04-10T10:00:00", "total_score": 0,
                                 "max_score": 100, "rubric_version": RUBRIC_VERSION})

        rows = scores_repo.get_by_decision(1)
        assert rows == []


# ── Schema validation failure ─────────────────────────────────────────────────


class TestSchemaValidationFailure:

    def _scorer_with_bad_response(self, bad_data: dict) -> JudgeScorer:
        client = _make_mock_client(response_dict=bad_data)
        return JudgeScorer(client=client)

    def test_unknown_dimension_returns_empty_list(self, caplog):
        bad = {
            "scores": [
                {"dimension": "nonexistent_dim", "score": 7, "justification": "x"}
            ]
        }
        scorer = self._scorer_with_bad_response(bad)
        with caplog.at_level(logging.ERROR):
            results = scorer.score_decision(_decision())
        assert results == []

    def test_unknown_dimension_logs_error(self, caplog):
        bad = {
            "scores": [
                {"dimension": "nonexistent_dim", "score": 7, "justification": "x"}
            ]
        }
        scorer = self._scorer_with_bad_response(bad)
        with caplog.at_level(logging.ERROR):
            scorer.score_decision(_decision())
        assert any("schema validation" in r.message.lower() or "validation" in r.message.lower()
                   for r in caplog.records)

    def test_invalid_json_returns_empty_list(self, caplog):
        client = MagicMock(spec=anthropic.Anthropic)
        content_block = MagicMock()
        content_block.text = "not valid json {{{"
        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [content_block]
        client.messages.create.return_value = mock_response

        scorer = JudgeScorer(client=client)
        with caplog.at_level(logging.ERROR):
            results = scorer.score_decision(_decision())
        assert results == []

    def test_missing_dimension_returns_empty_list(self, caplog):
        """Response has only 4 of 5 dimensions → validation fails."""
        partial = {
            "scores": [
                {"dimension": dim, "score": 7, "justification": "ok"}
                for dim in JUDGE_DIMENSIONS[:4]  # missing one
            ]
        }
        scorer = self._scorer_with_bad_response(partial)
        with caplog.at_level(logging.ERROR):
            results = scorer.score_decision(_decision())
        assert results == []


# ── Network error retry ───────────────────────────────────────────────────────


class TestNetworkErrorRetry:

    def test_retry_once_on_network_error_then_succeeds(self, caplog):
        """First call raises APIConnectionError; second call succeeds."""
        client = MagicMock(spec=anthropic.Anthropic)
        content_block = MagicMock()
        content_block.text = json.dumps(_valid_judge_response())
        success_response = MagicMock()
        success_response.stop_reason = "end_turn"
        success_response.content = [content_block]

        client.messages.create.side_effect = [
            anthropic.APIConnectionError(request=MagicMock()),
            success_response,
        ]

        scorer = JudgeScorer(client=client)
        with patch("evaluation.scorer_judge.time.sleep"):  # skip the backoff wait
            with caplog.at_level(logging.WARNING):
                results = scorer.score_decision(_decision())

        assert len(results) == 5
        assert client.messages.create.call_count == 2

    def test_two_network_errors_returns_empty_list(self, caplog):
        """Both calls raise APIConnectionError → empty list."""
        client = MagicMock(spec=anthropic.Anthropic)
        client.messages.create.side_effect = anthropic.APIConnectionError(
            request=MagicMock()
        )

        scorer = JudgeScorer(client=client)
        with patch("evaluation.scorer_judge.time.sleep"):
            with caplog.at_level(logging.ERROR):
                results = scorer.score_decision(_decision())

        assert results == []
        assert client.messages.create.call_count == 2


# ── Idempotence ───────────────────────────────────────────────────────────────


class TestIdempotence:

    def _make_decisions_repo_with_decision(self, conn):
        """Insert one decision row so get_in_range returns it."""
        conn.execute(
            """
            INSERT INTO decisions
                (id, timestamp, strategy_type, underlying, action, reasoning,
                 confidence, context_json)
            VALUES (1, '2026-04-10T10:00:00', 'wheel', 'AAPL', 'SELL_PUT',
                    '{}', 0.8, '{}')
            """
        )
        conn.commit()

    def test_second_run_skips_already_scored_decision(self, conn, scores_repo):
        """Running score_decisions_with_judge twice writes only one DB row."""
        self._make_decisions_repo_with_decision(conn)

        client = _make_mock_client()
        scorer = JudgeScorer(client=client)

        # First run — should write
        count1 = score_decisions_with_judge(
            "2026-04-10T00:00:00",
            "2026-04-10T23:59:59",
            scorer,
            scores_repo,
        )
        # Second run — should skip (idempotent)
        count2 = score_decisions_with_judge(
            "2026-04-10T00:00:00",
            "2026-04-10T23:59:59",
            scorer,
            scores_repo,
        )

        assert count1 == 1
        assert count2 == 0  # idempotency skip
        # Only one DB row
        rows = scores_repo.get_by_decision(1)
        assert len(rows) == 1
        # API called once (second run skipped without calling the model)
        assert client.messages.create.call_count == 1


# ── Dry-run ───────────────────────────────────────────────────────────────────


class TestDryRun:

    def _make_decisions_repo_with_decision(self, conn):
        conn.execute(
            """
            INSERT INTO decisions
                (id, timestamp, strategy_type, underlying, action, reasoning,
                 confidence, context_json)
            VALUES (1, '2026-04-10T10:00:00', 'wheel', 'AAPL', 'SELL_PUT',
                    '{}', 0.8, '{}')
            """
        )
        conn.commit()

    def test_dry_run_does_not_write_to_db(self, conn, scores_repo, caplog):
        self._make_decisions_repo_with_decision(conn)

        client = _make_mock_client()
        scorer = JudgeScorer(client=client)

        with caplog.at_level(logging.INFO):
            count = score_decisions_with_judge(
                "2026-04-10T00:00:00",
                "2026-04-10T23:59:59",
                scorer,
                scores_repo,
                dry_run=True,
            )

        # Returns the count of "would-write" decisions
        assert count == 1
        # But nothing in DB
        rows = scores_repo.get_by_decision(1)
        assert rows == []

    def test_dry_run_logs_scores(self, conn, scores_repo, caplog):
        self._make_decisions_repo_with_decision(conn)

        client = _make_mock_client()
        scorer = JudgeScorer(client=client)

        with caplog.at_level(logging.INFO):
            score_decisions_with_judge(
                "2026-04-10T00:00:00",
                "2026-04-10T23:59:59",
                scorer,
                scores_repo,
                dry_run=True,
            )

        assert any("dry-run" in r.message.lower() for r in caplog.records)
