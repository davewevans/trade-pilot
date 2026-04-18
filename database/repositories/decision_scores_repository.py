"""Repository for the ``decision_scores`` table."""

from __future__ import annotations

import sqlite3

from database.db import db_retry


class DecisionScoresRepository:
    """Persistence for offline rubric evaluation scores on individual decisions."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @db_retry()
    def insert(self, score_dict: dict) -> int:
        """Insert a new decision_scores row. Returns the new ``id``."""
        cur = self._conn.execute(
            """
            INSERT INTO decision_scores (
                decision_id, scorer_type, scored_at,
                total_score, max_score, rubric_version,
                dimension_scores_json, pass_fail, notes,
                spot_check_pending, spot_check_submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score_dict["decision_id"],
                score_dict["scorer_type"],
                score_dict["scored_at"],
                score_dict["total_score"],
                score_dict["max_score"],
                score_dict.get("rubric_version"),
                score_dict.get("dimension_scores_json"),
                score_dict.get("pass_fail"),
                score_dict.get("notes"),
                int(score_dict.get("spot_check_pending", 0)),
                score_dict.get("spot_check_submitted_at"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_by_decision(self, decision_id: int) -> list[dict]:
        """Return all score rows for a given decision, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM decision_scores WHERE decision_id = ? ORDER BY scored_at DESC",
            (decision_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_month(self, month: str) -> list[dict]:
        """Return all score rows whose ``scored_at`` falls in ``month`` (YYYY-MM)."""
        rows = self._conn.execute(
            "SELECT * FROM decision_scores WHERE scored_at LIKE ? ORDER BY scored_at DESC",
            (f"{month}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_spot_check_queue(self, month: str, limit: int = 20) -> list[dict]:
        """Return up to ``limit`` rows with spot_check_pending=1 for ``month``."""
        rows = self._conn.execute(
            """
            SELECT * FROM decision_scores
            WHERE spot_check_pending = 1
              AND scored_at LIKE ?
            ORDER BY scored_at ASC
            LIMIT ?
            """,
            (f"{month}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @db_retry()
    def mark_spot_check_submitted(self, score_id: int) -> None:
        """Clear spot_check_pending and record the submission timestamp."""
        self._conn.execute(
            """
            UPDATE decision_scores
               SET spot_check_pending = 0,
                   spot_check_submitted_at = datetime('now')
             WHERE id = ?
            """,
            (score_id,),
        )
        self._conn.commit()

    @db_retry()
    def mark_for_spot_check(self, score_ids: list[int]) -> None:
        """Set spot_check_pending=1 for each id in ``score_ids``."""
        if not score_ids:
            return
        placeholders = ",".join(["?"] * len(score_ids))
        self._conn.execute(
            f"UPDATE decision_scores SET spot_check_pending = 1 WHERE id IN ({placeholders})",
            score_ids,
        )
        self._conn.commit()
