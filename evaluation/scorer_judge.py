"""LLM-as-judge scorer for trade decisions.

Uses Claude Opus to evaluate qualitative reasoning dimensions that the
programmatic scorer cannot assess:
  - reasoning_groundedness
  - reasoning_relevance
  - reasoning_specificity
  - confidence_calibration
  - context_utilization

See knowledge/synthesized/decision_rubric.md for the programmatic dimensions
(rule_adherence, skip_validity_structural) and prompts/judge/decision_rubric_v1.md
for the judge rubric.

Usage::

    import anthropic
    from evaluation.scorer_judge import JudgeScorer

    scorer = JudgeScorer()
    dimension_dicts = scorer.score_decision(decision_row)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import anthropic

from evaluation.judge_schema import (
    JUDGE_DIMENSIONS,
    JUDGE_OUTPUT_SCHEMA,
    validate_judge_response,
)

logger = logging.getLogger(__name__)

_JUDGE_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "judge" / "decision_rubric_v1.md"
)

# Max characters of context_json to include in the user message.
# Approx 2 000 tokens at 4 chars/token.  Context exceeding this is truncated
# with a note so the judge is aware it saw a subset.
_MAX_CONTEXT_CHARS = 8_000

# Seconds to wait before the single retry after a network error.
_RETRY_BACKOFF_SECONDS = 2.0


class JudgeScorer:
    """Score individual decisions on LLM-quality reasoning dimensions.

    Args:
        model: Anthropic model string.  Defaults to ``"claude-opus-4-7"``.
        client: Pre-built ``anthropic.Anthropic`` client.  If ``None`` a new
            client is created using the ``ANTHROPIC_API_KEY`` env var.

    Usage::

        scorer = JudgeScorer()
        dimension_dicts = scorer.score_decision(decision_row)
        # Returns [] on refusal / max_tokens / network error / validation failure.
    """

    SCORER_TYPE = "judge"

    def __init__(
        self,
        model: str = "claude-opus-4-7",
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self._client = client if client is not None else anthropic.Anthropic()
        self._system_prompt = _JUDGE_PROMPT_PATH.read_text(encoding="utf-8")

    # ── Public API ────────────────────────────────────────────────────────────

    def score_decision(self, decision: dict) -> list[dict]:
        """Call Opus to score reasoning-quality dimensions for one decision.

        Args:
            decision: A decision row dict as returned by ``DecisionRepository``
                (context_json already decoded into ``context``; reasoning may
                be a JSON string or a dict).

        Returns:
            List of dimension-score dicts, one per scored dimension (5 total).
            Each dict has keys: ``decision_id``, ``dimension``, ``score``
            (float 0.0–1.0), ``score_metadata`` (dict with ``justification``
            and ``scorer_model``).
            Returns an **empty list** on refusal, max_tokens, network error, or
            schema validation failure — the caller must not write partial scores.
        """
        decision_id = decision.get("id")
        user_message = self._build_user_message(decision)

        raw_response = self._call_api_with_retry(decision_id, user_message)
        if raw_response is None:
            return []

        # Parse + validate
        try:
            data = json.loads(raw_response)
            validate_judge_response(data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error(
                "Judge schema validation failed for decision_id=%s: %s | response=%r",
                decision_id,
                exc,
                raw_response[:500],
            )
            return []

        return self._build_dimension_dicts(decision_id, data)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_user_message(self, decision: dict) -> str:
        """Compose the user message sent to the judge."""
        decision_id = decision.get("id")
        strategy_type = decision.get("strategy_type") or "unknown"
        action = decision.get("action") or "unknown"
        confidence = decision.get("confidence")
        timestamp = decision.get("timestamp") or "unknown"
        underlying = decision.get("underlying") or decision.get("symbol") or "unknown"

        # Reasoning — parse from JSON string if needed
        raw_reasoning = decision.get("reasoning")
        reasoning = _parse_json_field(raw_reasoning) if raw_reasoning else {}

        # Context — already decoded by DecisionRepository._row_to_dict
        context = decision.get("context") or {}
        context_str = json.dumps(context, indent=2, default=str)
        truncated = False
        if len(context_str) > _MAX_CONTEXT_CHARS:
            context_str = context_str[:_MAX_CONTEXT_CHARS]
            truncated = True

        reasoning_str = json.dumps(reasoning, indent=2, default=str)

        lines = [
            "## Decision Under Review",
            "",
            f"- **Decision ID**: {decision_id}",
            f"- **Strategy**: {strategy_type}",
            f"- **Symbol**: {underlying}",
            f"- **Action**: {action}",
            f"- **Confidence**: {confidence}",
            f"- **Timestamp**: {timestamp}",
            "",
            "## Reasoning (6 fields)",
            "",
            "```json",
            reasoning_str,
            "```",
            "",
            "## Market Context (at time of decision)",
        ]
        if truncated:
            lines.append(
                f"*(Context truncated to {_MAX_CONTEXT_CHARS} characters to fit token budget)*"
            )
        lines += [
            "",
            "```json",
            context_str,
            "```",
            "",
            "Score this decision now.",
        ]
        return "\n".join(lines)

    def _call_api_with_retry(
        self, decision_id: int | None, user_message: str
    ) -> str | None:
        """Call the Anthropic API, retrying once on network error.

        Returns the raw text content on success, or ``None`` to signal that
        scoring should be skipped for this decision.
        """
        for attempt in range(2):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=[
                        {
                            "type": "text",
                            "text": self._system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_message}],
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": JUDGE_OUTPUT_SCHEMA,
                        }
                    },
                )
            except anthropic.APIConnectionError as exc:
                if attempt == 0:
                    logger.warning(
                        "Judge API network error for decision_id=%s (attempt 1) — "
                        "retrying in %.1fs: %s",
                        decision_id,
                        _RETRY_BACKOFF_SECONDS,
                        exc,
                    )
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                logger.error(
                    "Judge API network error for decision_id=%s (attempt 2) — "
                    "skipping: %s",
                    decision_id,
                    exc,
                )
                return None
            except Exception as exc:
                logger.error(
                    "Judge API call failed for decision_id=%s — skipping: %s",
                    decision_id,
                    exc,
                )
                return None

            # Stop-reason checks
            if response.stop_reason in ("refusal", "max_tokens"):
                logger.error(
                    "Judge stop_reason=%s for decision_id=%s — skipping",
                    response.stop_reason,
                    decision_id,
                )
                return None

            if not response.content:
                logger.error(
                    "Judge returned empty content for decision_id=%s — skipping",
                    decision_id,
                )
                return None

            return response.content[0].text

        return None  # unreachable but satisfies type checker

    def _build_dimension_dicts(self, decision_id: int | None, data: dict) -> list[dict]:
        """Transform validated judge response into scorer output dicts."""
        result = []
        for item in data["scores"]:
            dimension = item["dimension"]
            raw_score = item["score"]  # integer 1–10
            justification = item["justification"]

            # Normalise to [0.0, 1.0] to match programmatic scorer convention
            score_normalised = raw_score / 10.0

            result.append(
                {
                    "decision_id": decision_id,
                    "dimension": dimension,
                    "score": score_normalised,
                    "score_metadata": {
                        "raw_score": raw_score,
                        "justification": justification,
                        "scorer_model": self.model,
                    },
                }
            )
        return result


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_json_field(value) -> dict:
    """Return *value* as a dict, parsing from JSON string if needed."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}
