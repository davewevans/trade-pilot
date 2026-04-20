"""Tests for evaluation/self_reviewer.py."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import anthropic

from evaluation.self_reviewer import (
    SelfReviewer,
    load_prior_accepted_summaries,
    load_relevant_prompts,
    select_flags_to_review,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _valid_self_review_response(n_patches: int = 2) -> dict:
    return {
        "flag_summary": "confidence_calibration scores declined for wheel strategy.",
        "reasoning_failure_mode": "Claude underspecified confidence rationale.",
        "suggested_prompt_patches": [
            {
                "target_file": "prompts/system.md",
                "target_section": "Confidence Scoring",
                "patch_type": "add",
                "exact_text": "When assigning confidence, cite the specific IV rank and trend.",
                "rationale": "Decisions 1, 2, 3 lacked explicit IV citation.",
                "supporting_decision_ids": [1, 2, 3],
            }
        ] * n_patches,
        "guardrail_migration_candidates": [],
        "declined_to_suggest": [],
    }


def _make_mock_client(response_dict=None, stop_reason: str = "end_turn"):
    client = MagicMock(spec=anthropic.Anthropic)
    content_block = MagicMock()
    content_block.text = json.dumps(response_dict or _valid_self_review_response())
    mock_response = MagicMock()
    mock_response.stop_reason = stop_reason
    mock_response.content = [content_block]
    client.messages.create.return_value = mock_response
    return client


def _make_reviewer(client=None, min_supporting_decisions: int = 3) -> SelfReviewer:
    with pytest.MonkeyPatch().context() as mp:
        pass  # just to get access to monkeypatch; we'll patch config inside init
    mock_settings = MagicMock()
    mock_settings.JUDGE_MODEL = "claude-opus-4-7"
    from unittest.mock import patch
    with patch("evaluation.self_reviewer.SelfReviewer.__init__") as mock_init:
        mock_init.return_value = None
        reviewer = SelfReviewer.__new__(SelfReviewer)
        reviewer.model = "claude-opus-4-7"
        reviewer._client = client or _make_mock_client()
        from pathlib import Path as _Path
        reviewer._system_prompt = (
            _Path(__file__).resolve().parent.parent / "prompts" / "self_review_v1.md"
        ).read_text(encoding="utf-8")
        reviewer._min_supporting_decisions = min_supporting_decisions
    return reviewer


def _flag(strategy="wheel", dimension="confidence_calibration",
          severity="warning", n_lowest=5):
    return {
        "strategy": strategy,
        "dimension": dimension,
        "severity": severity,
        "reason": "Below threshold",
        "lowest_scoring_decisions": list(range(1, n_lowest + 1)),
    }


def _hydrated_decisions(n: int = 3):
    return [
        {
            "id": i, "strategy_type": "wheel", "action": "SELL_PUT",
            "underlying": "AAPL", "confidence": 0.7, "reasoning": {},
            "skip_reason": None, "scores": [], "context": {"iv_rank": 42},
        }
        for i in range(1, n + 1)
    ]


# ── Schema validation ─────────────────────────────────────────────────────────


class TestReviewFlagValidResponse:

    def test_valid_response_passes_through(self):
        response = _valid_self_review_response(n_patches=2)
        client = _make_mock_client(response_dict=response)
        reviewer = _make_reviewer(client=client)
        result = reviewer.review_flag(
            flag=_flag(),
            hydrated_decisions=_hydrated_decisions(),
            relevant_prompts={"system.md": "# System"},
        )
        assert result is not None
        assert result["flag_summary"] == response["flag_summary"]
        assert len(result["suggested_prompt_patches"]) == 2

    def test_stop_reason_refusal_returns_none(self):
        client = _make_mock_client(stop_reason="refusal")
        reviewer = _make_reviewer(client=client)
        result = reviewer.review_flag(
            flag=_flag(),
            hydrated_decisions=_hydrated_decisions(),
            relevant_prompts={},
        )
        assert result is None

    def test_stop_reason_max_tokens_returns_none(self):
        client = _make_mock_client(stop_reason="max_tokens")
        reviewer = _make_reviewer(client=client)
        result = reviewer.review_flag(
            flag=_flag(),
            hydrated_decisions=_hydrated_decisions(),
            relevant_prompts={},
        )
        assert result is None

    def test_invalid_json_returns_none_no_raise(self):
        client = MagicMock(spec=anthropic.Anthropic)
        content_block = MagicMock()
        content_block.text = "NOT VALID JSON {{{"
        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [content_block]
        client.messages.create.return_value = mock_response
        reviewer = _make_reviewer(client=client)
        result = reviewer.review_flag(
            flag=_flag(),
            hydrated_decisions=_hydrated_decisions(),
            relevant_prompts={},
        )
        assert result is None


# ── Post-filter ───────────────────────────────────────────────────────────────


class TestPostFilter:

    def test_patches_with_insufficient_ids_dropped_and_warning_logged(
        self, caplog
    ):
        response = {
            "flag_summary": "test",
            "reasoning_failure_mode": "test",
            "suggested_prompt_patches": [
                {
                    "target_file": "prompts/system.md",
                    "target_section": "S1",
                    "patch_type": "add",
                    "exact_text": "Add this.",
                    "rationale": "Good reason.",
                    "supporting_decision_ids": [1, 2, 3],  # 3 IDs — passes min=3
                },
                {
                    "target_file": "prompts/system.md",
                    "target_section": "S2",
                    "patch_type": "add",
                    "exact_text": "Add that.",
                    "rationale": "Bad evidence.",
                    "supporting_decision_ids": [1, 2],  # 2 IDs — fails min=3
                },
            ],
            "guardrail_migration_candidates": [],
            "declined_to_suggest": [],
        }
        client = _make_mock_client(response_dict=response)
        reviewer = _make_reviewer(client=client, min_supporting_decisions=3)
        with caplog.at_level(logging.WARNING, logger="evaluation.self_reviewer"):
            result = reviewer.review_flag(
                flag=_flag(),
                hydrated_decisions=_hydrated_decisions(),
                relevant_prompts={},
            )
        assert result is not None
        assert len(result["suggested_prompt_patches"]) == 1
        assert result["suggested_prompt_patches"][0]["target_section"] == "S1"
        assert any("dropped" in r.message for r in caplog.records)


# ── select_flags_to_review ────────────────────────────────────────────────────


class TestSelectFlagsToReview:

    def test_respects_cap(self):
        flags = [_flag(strategy=f"s{i}") for i in range(7)]
        to_review, over_cap = select_flags_to_review(flags, cap=3)
        assert len(to_review) == 3
        assert len(over_cap) == 4

    def test_returns_all_when_under_cap(self):
        flags = [_flag(strategy=f"s{i}") for i in range(3)]
        to_review, over_cap = select_flags_to_review(flags, cap=5)
        assert len(to_review) == 3
        assert len(over_cap) == 0

    def test_prioritizes_by_severity_then_sample_size(self):
        flags = [
            {**_flag(strategy="a", severity="info", n_lowest=5), "dimension": "d1"},
            {**_flag(strategy="b", severity="warning", n_lowest=3), "dimension": "d1"},
            {**_flag(strategy="c", severity="critical", n_lowest=1), "dimension": "d1"},
            {**_flag(strategy="d", severity="warning", n_lowest=5), "dimension": "d1"},
        ]
        to_review, _ = select_flags_to_review(flags, cap=3)
        strategies = [f["strategy"] for f in to_review]
        # critical first, then warning (larger sample), then warning (smaller)
        assert strategies[0] == "c"
        assert strategies[1] == "d"
        assert strategies[2] == "b"

    def test_empty_input(self):
        to_review, over_cap = select_flags_to_review([], cap=5)
        assert to_review == []
        assert over_cap == []


# ── load_prior_accepted_summaries ─────────────────────────────────────────────


class TestLoadPriorAcceptedSummaries:

    def test_returns_empty_string_when_file_missing(self, tmp_path):
        result = load_prior_accepted_summaries(tmp_path)
        assert result == ""

    def test_returns_file_content_when_exists(self, tmp_path):
        log_file = tmp_path / "self_review_accepted_log.md"
        log_file.write_text("# Accepted suggestions\n- Entry 1", encoding="utf-8")
        result = load_prior_accepted_summaries(tmp_path)
        assert "Entry 1" in result

    def test_truncates_to_last_8000_chars(self, tmp_path):
        log_file = tmp_path / "self_review_accepted_log.md"
        content = "X" * 10000
        log_file.write_text(content, encoding="utf-8")
        result = load_prior_accepted_summaries(tmp_path)
        assert len(result) == 8000
        assert result == "X" * 8000

    def test_does_not_truncate_when_under_limit(self, tmp_path):
        log_file = tmp_path / "self_review_accepted_log.md"
        content = "short content"
        log_file.write_text(content, encoding="utf-8")
        result = load_prior_accepted_summaries(tmp_path)
        assert result == content


# ── load_relevant_prompts ─────────────────────────────────────────────────────


class TestLoadRelevantPrompts:

    def test_always_includes_system_md(self, tmp_path):
        (tmp_path / "system.md").write_text("# System", encoding="utf-8")
        result = load_relevant_prompts("wheel", tmp_path)
        assert "system.md" in result

    def test_skips_missing_files_silently(self, tmp_path):
        # system.md exists, strategy files don't
        (tmp_path / "system.md").write_text("# System", encoding="utf-8")
        result = load_relevant_prompts("wheel", tmp_path)
        assert "system.md" in result
        # wheel files not present — not in result, no error
        assert "wheel_idle.md" not in result

    def test_loads_strategy_files_when_present(self, tmp_path):
        (tmp_path / "system.md").write_text("# System", encoding="utf-8")
        (tmp_path / "wheel_idle.md").write_text("# Wheel idle", encoding="utf-8")
        result = load_relevant_prompts("wheel", tmp_path)
        assert "wheel_idle.md" in result
        assert result["wheel_idle.md"] == "# Wheel idle"

    def test_unknown_strategy_only_returns_system_md(self, tmp_path):
        (tmp_path / "system.md").write_text("# System", encoding="utf-8")
        result = load_relevant_prompts("unknown_strategy", tmp_path)
        assert list(result.keys()) == ["system.md"]

    def test_missing_system_md_returns_empty(self, tmp_path):
        result = load_relevant_prompts("wheel", tmp_path)
        assert result == {}
