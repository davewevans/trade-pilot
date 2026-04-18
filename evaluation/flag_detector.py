"""Flag detector for the monthly evaluation pipeline.

Consumes an aggregate produced by ``evaluation.aggregator.aggregate_scores``
and optionally a list of prior monthly aggregates, then emits flag dicts
for dimensions that warrant operator attention.

Usage::

    from evaluation.flag_detector import detect_flags

    flags = detect_flags(current_aggregate, history=[prev_month_agg, ...])
    for flag in flags:
        if flag.get("insufficient_sample"):
            print(f"Skipped flagging {flag['strategy']}: too few decisions")
        else:
            print(f"FLAG {flag['strategy']}/{flag['dimension']}: {flag['reason']}")
"""

from __future__ import annotations

from evaluation.thresholds import (
    ABSOLUTE_THRESHOLDS,
    SAMPLE_SIZE_GATES,
    TREND_SENSITIVITY,
    TREND_WINDOW_MONTHS,
)

# Number of lowest-scoring decision IDs to include in each flag.
_LOWEST_DECISIONS_LIMIT = 5


def detect_flags(aggregate: dict, history: list[dict]) -> list[dict]:
    """Detect quality flags from a monthly aggregate.

    For each strategy that meets its sample-size gate, two checks are run:

    1. **Absolute threshold**: flags if ``mean < ABSOLUTE_THRESHOLDS[dimension]``.
    2. **Negative trend**: flags if the score has declined monotonically over
       ``TREND_WINDOW_MONTHS`` consecutive months with a total drop ≥
       ``TREND_SENSITIVITY``.  Skipped (not errored) when ``history`` contains
       fewer than ``TREND_WINDOW_MONTHS`` prior months.

    A TODO stub marks the deferred judge-operator disagreement check; see inline.

    Args:
        aggregate: Output from ``aggregate_scores()`` for the month under review.
        history:   Prior monthly aggregates in chronological order (oldest first).
                   Pass an empty list when no history is available.

    Returns:
        List of dicts.  Two shapes are possible:

        *Insufficient sample marker* (strategy skipped entirely)::

            {
                "strategy": "wheel",
                "insufficient_sample": True,
                "decisions_scored": 3,
                "sample_size_gate": 10,
            }

        *Flag* (dimension-level issue detected)::

            {
                "strategy": "wheel",
                "dimension": "rule_adherence",
                "reason": "<human-readable explanation>",
                "severity": "warning",
                "lowest_scoring_decisions": [decision_id, ...],
            }
    """
    results: list[dict] = []
    by_strategy: dict = aggregate.get("by_strategy", {})

    for strategy, strategy_data in by_strategy.items():
        decisions_scored: int = strategy_data.get("decisions_scored", 0)
        gate: int = SAMPLE_SIZE_GATES.get(strategy, SAMPLE_SIZE_GATES["_default"])

        # Hard-skip flagging for low-sample strategies.
        if decisions_scored < gate:
            results.append(
                {
                    "strategy": strategy,
                    "insufficient_sample": True,
                    "decisions_scored": decisions_scored,
                    "sample_size_gate": gate,
                }
            )
            continue

        for dimension, dim_data in strategy_data.get("by_dimension", {}).items():
            mean: float = dim_data.get("mean", 0.0)
            lowest_ids: list = dim_data.get("_lowest_decision_ids", [])
            threshold: float = ABSOLUTE_THRESHOLDS.get(dimension, 0.70)

            # ----------------------------------------------------------
            # Check 1: absolute threshold
            # Boundary: exactly at threshold is a pass (not flagged).
            # ----------------------------------------------------------
            if mean < threshold:
                results.append(
                    {
                        "strategy": strategy,
                        "dimension": dimension,
                        "reason": (
                            f"mean_score {mean:.4f} is below "
                            f"absolute_threshold {threshold:.4f}"
                        ),
                        "severity": "warning",
                        "lowest_scoring_decisions": lowest_ids[:_LOWEST_DECISIONS_LIMIT],
                    }
                )

            # ----------------------------------------------------------
            # Check 2: negative trend over TREND_WINDOW_MONTHS months
            # Requires at least TREND_WINDOW_MONTHS prior months in history.
            # If history is shorter, skip silently (do not error).
            # ----------------------------------------------------------
            if len(history) < TREND_WINDOW_MONTHS:
                # Not enough history for trend analysis — skip this check.
                continue

            # Build the score series: last (TREND_WINDOW_MONTHS - 1) prior
            # months from history + the current month.
            recent_history = history[-(TREND_WINDOW_MONTHS - 1):]
            series: list[float] = []
            for prior_agg in recent_history:
                prior_mean = (
                    prior_agg.get("by_strategy", {})
                    .get(strategy, {})
                    .get("by_dimension", {})
                    .get(dimension, {})
                    .get("mean")
                )
                if prior_mean is None:
                    series = []  # incomplete series — abort trend check
                    break
                series.append(prior_mean)

            # Append current month to complete the window
            if len(series) == TREND_WINDOW_MONTHS - 1:
                series.append(mean)

            if len(series) < TREND_WINDOW_MONTHS:
                continue  # couldn't form a complete series

            # Monotone decline AND total drop exceeds sensitivity threshold
            is_monotone_decline = all(
                series[i] > series[i + 1] for i in range(len(series) - 1)
            )
            total_drop = series[0] - series[-1]

            if is_monotone_decline and total_drop >= TREND_SENSITIVITY:
                results.append(
                    {
                        "strategy": strategy,
                        "dimension": dimension,
                        "reason": (
                            f"declining_trend over {TREND_WINDOW_MONTHS} months "
                            f"(series={[round(s, 4) for s in series]}, "
                            f"total_drop={total_drop:.4f})"
                        ),
                        "severity": "warning",
                        "lowest_scoring_decisions": lowest_ids[:_LOWEST_DECISIONS_LIMIT],
                    }
                )

            # ----------------------------------------------------------
            # TODO: Judge-operator disagreement check (deferred)
            #
            # When integrated, query JudgeSpotChecksRepository for this
            # strategy + dimension and flag if the operator disagreement rate
            # exceeds a configured threshold (e.g. > 0.30).  This requires
            # DB access not available via the aggregate dict alone, so the
            # function signature will need a ``spot_checks_repo`` parameter
            # or the check should be run in a separate pass.
            #
            # Reference: JudgeSpotChecksRepository.get_disagreement_rate(month)
            # ----------------------------------------------------------

    return results
