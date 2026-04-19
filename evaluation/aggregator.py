"""Monthly score aggregator for the evaluation pipeline.

Consumes ``decision_scores`` rows for a calendar month and produces a
structured summary grouped by strategy type and scoring dimension.

Usage::

    from database.db import Database
    from database.repositories.decision_scores_repository import DecisionScoresRepository
    from evaluation.aggregator import aggregate_scores

    db = Database()
    db.init_schema()
    scores_repo = DecisionScoresRepository(db.get_connection())

    summary = aggregate_scores("2026-04", scores_repo)
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from database.repositories.decision_scores_repository import DecisionScoresRepository

# Maximum number of lowest-scoring decision IDs to surface per dimension.
# Stored under the private ``_lowest_decision_ids`` key for use by flag_detector.
_LOWEST_DECISIONS_LIMIT = 5


def aggregate_scores(month: str, scores_repo: DecisionScoresRepository) -> dict:
    """Aggregate decision_scores for *month* into a per-strategy summary.

    Args:
        month: Calendar month in ``"YYYY-MM"`` format.
        scores_repo: Repository that also provides access to the decisions
            table via ``scores_repo._conn``.

    Returns:
        Dict with the following top-level keys:

        ``month``
            The input month string.

        ``by_strategy``
            Mapping of strategy_type to a dict with:

            * ``by_dimension``: dimension name → stats dict (mean, median,
              stddev, n, by_prompt_version, _lowest_decision_ids).
              ``_lowest_decision_ids`` is a private list used by
              ``flag_detector.detect_flags``; callers should not rely on it.
            * ``decisions_scored``: count of unique decisions with score rows
              in this month for this strategy.
            * ``closed_trades_in_window``: count of CLOSE actions recorded
              in the decisions table for this strategy during *month*.

        ``overall``
            Aggregate stats across all strategies (by_dimension, decisions_scored,
            closed_trades_in_window).
    """
    conn = scores_repo._conn

    score_rows = scores_repo.get_by_month(month)
    if not score_rows:
        return {
            "month": month,
            "by_strategy": {},
            "overall": {
                "by_dimension": {},
                "decisions_scored": 0,
                "closed_trades_in_window": 0,
            },
        }

    # ------------------------------------------------------------------
    # Fetch decision metadata (strategy_type, prompt_version) for all
    # decision_ids referenced in the score rows.
    # ------------------------------------------------------------------
    decision_ids = list({
        int(r["decision_id"])
        for r in score_rows
        if r.get("decision_id") is not None
    })

    decision_meta: dict[int, dict[str, Any]] = {}
    if decision_ids:
        placeholders = ",".join("?" * len(decision_ids))
        rows = conn.execute(
            f"SELECT id, strategy_type, prompt_version "
            f"FROM decisions WHERE id IN ({placeholders})",
            decision_ids,
        ).fetchall()
        for row in rows:
            decision_meta[int(row["id"])] = {
                "strategy_type": row["strategy_type"],
                "prompt_version": row["prompt_version"],
            }

    # ------------------------------------------------------------------
    # Count CLOSE decisions per strategy in this month.
    # Filters by timestamp prefix so only the target month is counted.
    # ------------------------------------------------------------------
    closed_rows = conn.execute(
        """
        SELECT strategy_type, COUNT(*) AS n
          FROM decisions
         WHERE timestamp LIKE ?
           AND UPPER(action) = 'CLOSE'
         GROUP BY strategy_type
        """,
        (f"{month}%",),
    ).fetchall()
    closed_by_strategy: dict[str, int] = {
        (row["strategy_type"] or "unknown"): int(row["n"])
        for row in closed_rows
    }

    # ------------------------------------------------------------------
    # Accumulate per-strategy, per-dimension score entries.
    # accum[strategy][dimension] = {
    #     "entries": [(score, decision_id), ...],
    #     "by_pv":   {prompt_version: [score, ...], ...},
    # }
    # ------------------------------------------------------------------
    accum: dict[str, dict[str, dict]] = {}
    decisions_per_strategy: dict[str, set[int]] = {}

    for row in score_rows:
        decision_id = int(row["decision_id"])
        meta = decision_meta.get(decision_id, {})
        strategy = meta.get("strategy_type") or "unknown"
        prompt_version = meta.get("prompt_version") or "unknown"

        decisions_per_strategy.setdefault(strategy, set()).add(decision_id)

        dim_json = row.get("dimension_scores_json")
        if not dim_json:
            continue
        try:
            dims = json.loads(dim_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(dims, list):
            continue

        strategy_accum = accum.setdefault(strategy, {})
        for item in dims:
            dimension = item.get("dimension")
            score_val = item.get("score")
            if dimension is None or score_val is None:
                continue
            try:
                score = float(score_val)
            except (TypeError, ValueError):
                continue

            dim_accum = strategy_accum.setdefault(
                dimension, {"entries": [], "by_pv": {}}
            )
            dim_accum["entries"].append((score, decision_id))
            dim_accum["by_pv"].setdefault(prompt_version, []).append(score)

    # ------------------------------------------------------------------
    # Build structured output per strategy.
    # ------------------------------------------------------------------
    by_strategy: dict[str, dict] = {}
    all_dim_entries: dict[str, list[tuple[float, int]]] = {}

    for strategy, dims in accum.items():
        by_dimension: dict[str, dict] = {}

        for dimension, data in dims.items():
            entries: list[tuple[float, int]] = data["entries"]
            scores = [s for s, _ in entries]
            n = len(scores)

            mean = statistics.mean(scores) if scores else 0.0
            median = statistics.median(scores) if scores else 0.0
            stddev = statistics.stdev(scores) if n > 1 else 0.0

            # Per-prompt-version stats
            by_pv: dict[str, dict] = {}
            for pv, pv_scores in data["by_pv"].items():
                pv_n = len(pv_scores)
                by_pv[pv] = {
                    "mean": round(statistics.mean(pv_scores), 4),
                    "median": round(statistics.median(pv_scores), 4),
                    "stddev": round(
                        statistics.stdev(pv_scores) if pv_n > 1 else 0.0, 4
                    ),
                    "n": pv_n,
                }

            # Lowest-scoring decision IDs (for flag_detector.detect_flags)
            sorted_entries = sorted(entries, key=lambda x: x[0])
            lowest_ids = [did for _, did in sorted_entries[:_LOWEST_DECISIONS_LIMIT]]

            by_dimension[dimension] = {
                "mean": round(mean, 4),
                "median": round(median, 4),
                "stddev": round(stddev, 4),
                "n": n,
                "by_prompt_version": by_pv,
                "_lowest_decision_ids": lowest_ids,
            }
            all_dim_entries.setdefault(dimension, []).extend(entries)

        by_strategy[strategy] = {
            "by_dimension": by_dimension,
            "decisions_scored": len(decisions_per_strategy.get(strategy, set())),
            "closed_trades_in_window": closed_by_strategy.get(strategy, 0),
        }

    # ------------------------------------------------------------------
    # Overall stats: aggregated across all strategies.
    # ------------------------------------------------------------------
    overall_by_dimension: dict[str, dict] = {}
    for dimension, entries in all_dim_entries.items():
        scores = [s for s, _ in entries]
        n = len(scores)
        overall_by_dimension[dimension] = {
            "mean": round(statistics.mean(scores) if scores else 0.0, 4),
            "median": round(statistics.median(scores) if scores else 0.0, 4),
            "stddev": round(statistics.stdev(scores) if n > 1 else 0.0, 4),
            "n": n,
        }

    total_decisions = sum(len(ids) for ids in decisions_per_strategy.values())
    total_closed = sum(closed_by_strategy.values())

    return {
        "month": month,
        "by_strategy": by_strategy,
        "overall": {
            "by_dimension": overall_by_dimension,
            "decisions_scored": total_decisions,
            "closed_trades_in_window": total_closed,
        },
    }
