"""Repository for the ``judge_spot_checks`` table."""

from __future__ import annotations

import sqlite3

from database.db import db_retry


class JudgeSpotChecksRepository:
    """Persistence for human-judge spot-check reviews of automated scores."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @db_retry()
    def insert(self, check_dict: dict) -> int:
        """Insert a new judge_spot_checks row. Returns the new ``id``."""
        cur = self._conn.execute(
            """
            INSERT INTO judge_spot_checks (
                decision_score_id, submitted_at,
                judge_score, operator_verdict, verdict_notes, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                check_dict["decision_score_id"],
                check_dict["submitted_at"],
                check_dict.get("judge_score"),
                check_dict.get("operator_verdict"),
                check_dict.get("verdict_notes"),
                check_dict.get("checked_at"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_by_month(self, month: str) -> list[dict]:
        """Return all spot-check rows whose linked score falls in ``month`` (YYYY-MM).

        Joins through decision_scores to filter by scored_at month.
        """
        rows = self._conn.execute(
            """
            SELECT jsc.*
              FROM judge_spot_checks jsc
              JOIN decision_scores ds ON jsc.decision_score_id = ds.id
             WHERE ds.scored_at LIKE ?
             ORDER BY jsc.submitted_at DESC
            """,
            (f"{month}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_disagreement_rate(self, month: str) -> float | None:
        """Fraction of spot checks for ``month`` where operator_verdict='disagree'.

        Returns None if no spot checks with a recorded verdict exist for the month.
        """
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN jsc.operator_verdict = 'disagree' THEN 1 ELSE 0 END) AS disagreements
              FROM judge_spot_checks jsc
              JOIN decision_scores ds ON jsc.decision_score_id = ds.id
             WHERE ds.scored_at LIKE ?
               AND jsc.operator_verdict IS NOT NULL
            """,
            (f"{month}%",),
        ).fetchone()
        if row is None:
            return None
        total = row["total"]
        if not total:
            return None
        return row["disagreements"] / total
