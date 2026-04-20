"""Hydrate decision IDs into full decision dicts with parsed context and scores.

Used by both the /api/evaluations/{month}/flagged-decisions endpoint and the
self_reviewer module. Consolidated here to avoid duplication.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.repositories.decision_scores_repository import DecisionScoresRepository

logger = logging.getLogger(__name__)


def hydrate_decisions(
    decision_ids: list[int],
    conn: sqlite3.Connection,
    scores_repo: "DecisionScoresRepository",
) -> list[dict]:
    """Fetch decisions by ID and attach parsed context + score rows.

    Missing decision IDs are silently skipped (not an error — stale flag data
    may reference deleted decisions).

    Args:
        decision_ids: Ordered list of decision IDs to hydrate.
        conn: Open sqlite3 connection (row_factory should yield Row or dict-like).
        scores_repo: DecisionScoresRepository instance.

    Returns:
        List of dicts preserving the input order. Each dict is the raw
        `decisions` row with:
          - "context": parsed dict (or None if context_json was missing/invalid)
          - "reasoning": parsed dict if it was a JSON string (else unchanged)
          - "scores": list of decision_scores rows for this decision
    """
    if not decision_ids:
        return []

    placeholders = ",".join(["?"] * len(decision_ids))
    rows = conn.execute(
        f"SELECT * FROM decisions WHERE id IN ({placeholders})",
        decision_ids,
    ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}

    result: list[dict] = []
    for did in decision_ids:
        d = by_id.get(did)
        if d is None:
            continue
        if d.get("context_json"):
            try:
                d["context"] = json.loads(d["context_json"])
            except (json.JSONDecodeError, TypeError):
                d["context"] = None
        else:
            d["context"] = None
        if isinstance(d.get("reasoning"), str):
            try:
                d["reasoning"] = json.loads(d["reasoning"])
            except (json.JSONDecodeError, TypeError):
                pass  # leave as string
        d["scores"] = scores_repo.get_by_decision(did)
        result.append(d)

    return result
