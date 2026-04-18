"""Orchestrator that fetches decisions in a date range, scores them, and persists results.

Entry point::

    from evaluation.scoring_orchestrator import score_decisions_in_range
    from evaluation.scorer_programmatic import ProgrammaticScorer
    from database.repositories.decision_scores_repository import DecisionScoresRepository

    count = score_decisions_in_range(
        "2026-04-01T00:00:00",
        "2026-04-30T23:59:59",
        ProgrammaticScorer(),
        scores_repo,
    )
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from evaluation import RUBRIC_VERSION
from database.repositories.decisions import DecisionRepository

logger = logging.getLogger(__name__)


def score_decisions_in_range(
    start_iso: str,
    end_iso: str,
    scorer,
    repo,
) -> int:
    """Fetch all decisions in [start_iso, end_iso] and write programmatic scores.

    Args:
        start_iso: ISO-format start timestamp (inclusive), e.g. "2026-04-01T00:00:00".
        end_iso:   ISO-format end timestamp (inclusive), e.g. "2026-04-30T23:59:59".
        scorer:    A scorer instance with a ``score_decision(decision) -> list[dict]``
                   method and a ``SCORER_TYPE`` class attribute.
        repo:      A ``DecisionScoresRepository`` instance.  The orchestrator derives
                   a ``DecisionRepository`` from the same connection.

    Returns:
        Number of decision_scores rows written (each dimension per decision counts
        as one row in the aggregated sense; this returns the count of
        *decisions* scored, i.e. rows written to decision_scores).
    """
    conn = repo._conn
    decision_repo = DecisionRepository(conn)

    decisions = decision_repo.get_in_range(start_iso, end_iso)
    logger.info(
        "Scoring %d decisions in range %s – %s (rubric=%s, scorer=%s)",
        len(decisions), start_iso, end_iso, RUBRIC_VERSION, scorer.SCORER_TYPE,
    )

    written = 0
    skipped = 0

    for decision in decisions:
        decision_id = decision.get("id")

        # Idempotency: skip if we already scored this decision with this
        # rubric version and scorer type.
        if _already_scored(repo, decision_id, scorer.SCORER_TYPE):
            skipped += 1
            continue

        try:
            dimension_dicts = scorer.score_decision(decision)
        except Exception:
            logger.exception(
                "Scorer raised on decision_id=%s — skipping", decision_id
            )
            continue

        if not dimension_dicts:
            # No applicable dimensions for this action type (e.g. HOLD)
            continue

        row = _build_score_row(decision_id, scorer.SCORER_TYPE, dimension_dicts)
        try:
            repo.insert(row)
            written += 1
        except Exception:
            logger.exception("Failed to write score row for decision_id=%s", decision_id)

    logger.info(
        "Scoring complete: %d written, %d already scored (idempotency skip)",
        written, skipped,
    )
    return written


def _already_scored(repo, decision_id: int, scorer_type: str) -> bool:
    """Return True if a score row already exists for (decision_id, RUBRIC_VERSION, scorer_type)."""
    rows = repo._conn.execute(
        """
        SELECT 1 FROM decision_scores
         WHERE decision_id = ?
           AND rubric_version = ?
           AND scorer_type = ?
         LIMIT 1
        """,
        (decision_id, RUBRIC_VERSION, scorer_type),
    ).fetchone()
    return rows is not None


def _build_score_row(
    decision_id: int,
    scorer_type: str,
    dimension_dicts: list[dict],
) -> dict:
    """Aggregate per-dimension dicts into a single decision_scores row."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Serialize score_metadata dicts inside each dimension dict to JSON strings
    serialized_dims = []
    for d in dimension_dicts:
        meta = d.get("score_metadata") or {}
        serialized_dims.append({
            "dimension": d.get("dimension"),
            "score": d.get("score", 0.0),
            "score_metadata": meta,
        })

    # Aggregate: average of dimension scores, scaled to 0–100
    scores = [d.get("score", 0.0) for d in dimension_dicts]
    avg = sum(scores) / len(scores) if scores else 0.0
    total_score = round(avg * 100, 2)
    max_score = 100.0
    pass_fail = "pass" if avg >= 0.8 else "fail"

    return {
        "decision_id": decision_id,
        "scorer_type": scorer_type,
        "scored_at": now,
        "total_score": total_score,
        "max_score": max_score,
        "rubric_version": RUBRIC_VERSION,
        "dimension_scores_json": json.dumps(serialized_dims, default=str),
        "pass_fail": pass_fail,
        "notes": None,
        "spot_check_pending": 0,
        "spot_check_submitted_at": None,
    }
