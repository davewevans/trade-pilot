"""Schema definitions for the LLM judge scorer (evaluation/scorer_judge.py).

JUDGE_OUTPUT_SCHEMA is passed as ``output_config.format.schema`` on every
Anthropic API call so Claude is physically incapable of returning output that
violates the outer structure.

``validate_judge_response`` performs additional business-logic checks after
JSON parsing: verifies all 5 expected dimensions are present and that each
score is an integer in [1, 10].
"""

from __future__ import annotations

JUDGE_DIMENSIONS: list[str] = [
    "reasoning_groundedness",
    "reasoning_relevance",
    "reasoning_specificity",
    "confidence_calibration",
    "context_utilization",
]

# JSON Schema passed to Anthropic structured outputs.
JUDGE_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scores"],
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["dimension", "score", "justification"],
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": JUDGE_DIMENSIONS,
                    },
                    "score": {
                        "type": "integer",
                    },
                    "justification": {"type": "string"},
                },
            },
        },
    },
}


def validate_judge_response(data: dict) -> None:
    """Validate a parsed judge response against business rules.

    Raises:
        ValueError: if any dimension is missing, unknown, or has an invalid score.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    scores = data.get("scores")
    if not isinstance(scores, list):
        raise ValueError("Response missing 'scores' list")

    found: set[str] = set()
    for item in scores:
        dim = item.get("dimension")
        if dim not in JUDGE_DIMENSIONS:
            raise ValueError(f"Unknown or unexpected dimension: {dim!r}")
        score = item.get("score")
        if not isinstance(score, int) or not (1 <= score <= 10):
            raise ValueError(
                f"Invalid score for dimension {dim!r}: {score!r} "
                "(expected integer 1–10)"
            )
        if not item.get("justification"):
            raise ValueError(f"Empty or missing justification for dimension {dim!r}")
        found.add(dim)

    missing = set(JUDGE_DIMENSIONS) - found
    if missing:
        raise ValueError(f"Response is missing dimensions: {missing}")
