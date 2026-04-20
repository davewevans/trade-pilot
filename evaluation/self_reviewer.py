"""Self-review scorer — Opus-driven prompt-patch suggestions.

Takes a flag from flag_detector and the hydrated lowest-scoring decisions,
calls Opus, and returns structured prompt-patch suggestions. Does not
write to any database or file — the caller is responsible for persistence.

Usage::

    from evaluation.self_reviewer import SelfReviewer

    reviewer = SelfReviewer()
    output = reviewer.review_flag(
        flag=flag_dict,
        hydrated_decisions=[...],
        relevant_prompts={"system.md": "...", "wheel_idle.md": "..."},
        prior_accepted_summaries="...",  # may be empty string
    )
    # Returns a dict matching the self-review JSON schema, or None on
    # refusal / max_tokens / network error / schema validation failure.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "self_review_v1.md"
)

_MAX_CONTEXT_CHARS_PER_DECISION = 3000  # Truncate per-decision context if larger
_RETRY_BACKOFF_SECONDS = 2.0

# JSON schema enforced on Opus output. Keys here must match the shape
# documented in prompts/self_review_v1.md.
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "flag_summary": {"type": "string"},
        "reasoning_failure_mode": {"type": "string"},
        "suggested_prompt_patches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_file": {"type": "string"},
                    "target_section": {"type": "string"},
                    "patch_type": {
                        "type": "string",
                        "enum": ["add", "modify", "remove"],
                    },
                    "exact_text": {"type": "string"},
                    "rationale": {"type": "string"},
                    "supporting_decision_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": [
                    "target_file", "target_section", "patch_type",
                    "exact_text", "rationale", "supporting_decision_ids",
                ],
                "additionalProperties": False,
            },
        },
        "guardrail_migration_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "observation": {"type": "string"},
                    "why_code_not_prompt": {"type": "string"},
                    "supporting_decision_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": [
                    "observation", "why_code_not_prompt",
                    "supporting_decision_ids",
                ],
                "additionalProperties": False,
            },
        },
        "declined_to_suggest": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "relevant_decision_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["reason", "relevant_decision_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "flag_summary", "reasoning_failure_mode",
        "suggested_prompt_patches", "guardrail_migration_candidates",
        "declined_to_suggest",
    ],
    "additionalProperties": False,
}


class SelfReviewer:
    """Call Opus once per flag and return structured prompt-patch suggestions."""

    def __init__(
        self,
        model: str | None = None,
        client: anthropic.Anthropic | None = None,
        min_supporting_decisions: int = 3,
    ) -> None:
        from config import settings
        self.model = model or settings.JUDGE_MODEL
        self._client = client if client is not None else anthropic.Anthropic()
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
        self._min_supporting_decisions = min_supporting_decisions

    def review_flag(
        self,
        flag: dict,
        hydrated_decisions: list[dict],
        relevant_prompts: dict[str, str],
        prior_accepted_summaries: str = "",
    ) -> dict | None:
        """Call Opus on one flag and return structured output.

        Returns None on any failure (refusal, max_tokens, network,
        schema validation). None is not an error — the caller should
        log it and move to the next flag.
        """
        user_message = self._build_user_message(
            flag=flag,
            hydrated_decisions=hydrated_decisions,
            relevant_prompts=relevant_prompts,
            prior_accepted_summaries=prior_accepted_summaries,
        )

        raw = self._call_api_with_retry(flag, user_message)
        if raw is None:
            return None

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error(
                "self_reviewer: JSON parse failed for flag %s/%s: %s",
                flag.get("strategy"), flag.get("dimension"), exc,
            )
            return None

        # Post-call filter: drop patches with fewer than min_supporting_decisions.
        # The system prompt forbids this, but we filter defensively anyway.
        if not self._post_filter(data, flag):
            return None

        return data

    # ── internals ──────────────────────────────────────────────────────────

    def _build_user_message(
        self,
        flag: dict,
        hydrated_decisions: list[dict],
        relevant_prompts: dict[str, str],
        prior_accepted_summaries: str,
    ) -> str:
        parts: list[str] = []

        parts.append("# Flag under review")
        parts.append(json.dumps({
            "strategy": flag.get("strategy"),
            "dimension": flag.get("dimension"),
            "severity": flag.get("severity"),
            "reason": flag.get("reason"),
        }, indent=2))
        parts.append("")

        parts.append("# Lowest-scoring decisions on this flag")
        for d in hydrated_decisions:
            d_summary = {
                "id": d.get("id"),
                "strategy": d.get("strategy_type"),
                "action": d.get("action"),
                "symbol": d.get("symbol") or d.get("underlying"),
                "reasoning": d.get("reasoning"),
                "skip_reason": d.get("skip_reason"),
                "confidence": d.get("confidence"),
                "scores": d.get("scores", []),
            }
            ctx = d.get("context")
            if ctx is not None:
                ctx_json = json.dumps(ctx, indent=2, default=str)
                if len(ctx_json) > _MAX_CONTEXT_CHARS_PER_DECISION:
                    ctx_json = (
                        ctx_json[:_MAX_CONTEXT_CHARS_PER_DECISION]
                        + "\n... [context truncated for length]"
                    )
                d_summary["context"] = ctx_json
            parts.append(json.dumps(d_summary, indent=2, default=str))
            parts.append("")

        parts.append("# Relevant current prompt text")
        for filename, text in relevant_prompts.items():
            parts.append(f"## {filename}")
            parts.append("```")
            parts.append(text)
            parts.append("```")
            parts.append("")

        if prior_accepted_summaries.strip():
            parts.append("# Prior accepted-suggestion summaries (last 3 months)")
            parts.append(prior_accepted_summaries)
            parts.append("")
            parts.append(
                "Check whether your current suggestion contradicts any "
                "of the above. If it does, decline."
            )
        else:
            parts.append(
                "# No prior accepted-suggestion summaries available."
            )

        parts.append("")
        parts.append(
            "Produce your JSON output now. Remember: decline is a "
            "legitimate response when evidence is thin."
        )

        return "\n".join(parts)

    def _call_api_with_retry(
        self, flag: dict, user_message: str,
    ) -> str | None:
        """Single retry on APIConnectionError; return None on any failure."""
        for attempt in range(2):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=4096,
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
                            "schema": _OUTPUT_SCHEMA,
                        }
                    },
                )
            except anthropic.APIConnectionError as exc:
                if attempt == 0:
                    logger.warning(
                        "self_reviewer: network error for flag %s/%s "
                        "(attempt 1) — retrying: %s",
                        flag.get("strategy"), flag.get("dimension"), exc,
                    )
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                logger.error(
                    "self_reviewer: network error for flag %s/%s "
                    "(attempt 2) — skipping: %s",
                    flag.get("strategy"), flag.get("dimension"), exc,
                )
                return None
            except Exception as exc:
                logger.error(
                    "self_reviewer: API call failed for flag %s/%s — skipping: %s",
                    flag.get("strategy"), flag.get("dimension"), exc,
                )
                return None

            if response.stop_reason in ("refusal", "max_tokens"):
                logger.error(
                    "self_reviewer: stop_reason=%s for flag %s/%s — skipping",
                    response.stop_reason,
                    flag.get("strategy"), flag.get("dimension"),
                )
                return None

            if not response.content:
                logger.error(
                    "self_reviewer: empty content for flag %s/%s — skipping",
                    flag.get("strategy"), flag.get("dimension"),
                )
                return None

            return response.content[0].text

        return None  # unreachable

    def _post_filter(self, data: dict, flag: dict) -> bool:
        """Drop patches with too few supporting IDs. Return True if any output remains.

        Mutates `data` in place. Returns False only if every field is empty
        after filtering, in which case the caller should treat the result
        as no useful output (caller can still log it).
        """
        patches = data.get("suggested_prompt_patches") or []
        filtered = [
            p for p in patches
            if len(p.get("supporting_decision_ids") or [])
            >= self._min_supporting_decisions
        ]
        dropped = len(patches) - len(filtered)
        if dropped > 0:
            logger.warning(
                "self_reviewer: dropped %d patches for flag %s/%s due to "
                "insufficient supporting_decision_ids (< %d)",
                dropped, flag.get("strategy"), flag.get("dimension"),
                self._min_supporting_decisions,
            )
        data["suggested_prompt_patches"] = filtered
        return True  # Always return the dict; caller decides what's useful.


# ── public helpers used by jobs/monthly_evaluation.py ────────────────────────


def select_flags_to_review(
    flags: list[dict], cap: int,
) -> tuple[list[dict], list[dict]]:
    """Return (flags_to_review, flags_dropped_over_cap).

    Prioritizes by severity (warning > other), then by strategy sample size
    descending. The `flags` input is the `real_flags` list from
    jobs/monthly_evaluation — post `detect_flags`, pre-persistence.
    """
    severity_rank = {"critical": 0, "high": 1, "warning": 2, "info": 3}

    def sort_key(f: dict) -> tuple:
        sev = severity_rank.get(f.get("severity", "warning"), 2)
        # Larger sample sizes first (negative for ascending sort)
        n_supporting = -len(f.get("lowest_scoring_decisions") or [])
        return (sev, n_supporting)

    sorted_flags = sorted(flags, key=sort_key)
    return sorted_flags[:cap], sorted_flags[cap:]


def load_prior_accepted_summaries(reports_dir: Path) -> str:
    """Read the operator-maintained accepted-suggestion log.

    The file `data/reports/self_review_accepted_log.md` is maintained by
    the operator — after accepting a suggestion, they append a short
    summary there. This function returns the file's content or empty
    string if the file doesn't exist.

    Intentionally simple: no parsing, no schema. The file is freeform
    markdown. Opus reads it and reasons about contradictions.
    """
    path = reports_dir / "self_review_accepted_log.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
        # Cap at ~8KB to bound token usage if operator lets it grow unbounded
        if len(text) > 8000:
            text = text[-8000:]  # keep most recent (bottom of file)
        return text
    except Exception:
        logger.warning(
            "self_reviewer: failed to read accepted log at %s", path,
            exc_info=True,
        )
        return ""


def load_relevant_prompts(
    strategy: str, prompts_dir: Path,
) -> dict[str, str]:
    """Load prompt files relevant to the flagged strategy.

    Always includes system.md. Includes strategy-specific prompts if the
    strategy name maps to known prompt files. Missing files are silently
    skipped — not an error.
    """
    files_to_load: list[str] = ["system.md"]

    # Strategy-to-prompt-file mapping. Matches the naming in prompts/.
    strategy_file_map: dict[str, list[str]] = {
        "wheel": [
            "wheel_idle.md", "wheel_short_put.md",
            "wheel_long_stock.md", "wheel_short_call.md",
        ],
        "turnover_wheel": [
            "turnover_wheel_idle.md", "turnover_wheel_short_put.md",
            "turnover_wheel_long_stock.md", "turnover_wheel_short_call.md",
        ],
        "bull_put_spread": [
            "bull_put_spread_idle.md", "bull_put_spread_open.md",
        ],
        "bear_call_spread": [
            "bear_call_spread_idle.md", "bear_call_spread_open.md",
        ],
        "iron_condor": [
            "iron_condor_idle.md", "iron_condor_open.md",
        ],
        "long_call_vertical": [
            "long_call_vertical_idle.md", "long_call_vertical_open.md",
        ],
        "iron_butterfly": [
            "iron_butterfly_idle.md", "iron_butterfly_open.md",
        ],
        "calendar_spread": [
            "calendar_spread_idle.md", "calendar_spread_open.md",
        ],
    }

    files_to_load.extend(strategy_file_map.get(strategy, []))

    loaded: dict[str, str] = {}
    for fname in files_to_load:
        path = prompts_dir / fname
        if not path.exists():
            logger.debug(
                "self_reviewer: prompt file not found, skipping: %s", fname,
            )
            continue
        try:
            loaded[fname] = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning(
                "self_reviewer: failed to read prompt %s", fname, exc_info=True,
            )

    return loaded
