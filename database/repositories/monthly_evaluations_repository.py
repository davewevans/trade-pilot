"""Repository for the ``monthly_evaluations`` table."""

from __future__ import annotations

import sqlite3

from database.db import db_retry


class MonthlyEvaluationsRepository:
    """Persistence for per-month rubric pipeline summaries."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @db_retry()
    def insert(self, eval_dict: dict) -> int:
        """Insert a new monthly_evaluations row. Returns the new ``id``.

        Raises ``sqlite3.IntegrityError`` if a row for ``month`` already exists.
        """
        cur = self._conn.execute(
            """
            INSERT INTO monthly_evaluations (
                month, decisions_evaluated, avg_score, pct_pass,
                score_distribution_json, flags_json, created_at,
                reviewed_at, action_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eval_dict["month"],
                eval_dict.get("decisions_evaluated", 0),
                eval_dict.get("avg_score"),
                eval_dict.get("pct_pass"),
                eval_dict.get("score_distribution_json"),
                eval_dict.get("flags_json"),
                eval_dict["created_at"],
                eval_dict.get("reviewed_at"),
                eval_dict.get("action_note"),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    @db_retry()
    def upsert(self, eval_dict: dict) -> int:
        """Insert or update the monthly_evaluations row for ``month``.

        On regeneration, scores/flags/timestamp are overwritten but
        ``reviewed_at`` and ``action_note`` are preserved so an operator's
        review note is not lost when re-running a month.

        Returns the row id.
        """
        existing = self.get_by_month(eval_dict["month"])
        if existing:
            self._conn.execute(
                """
                UPDATE monthly_evaluations
                   SET decisions_evaluated    = ?,
                       avg_score              = ?,
                       pct_pass               = ?,
                       score_distribution_json = ?,
                       flags_json             = ?,
                       created_at             = ?
                 WHERE month = ?
                """,
                (
                    eval_dict.get("decisions_evaluated", 0),
                    eval_dict.get("avg_score"),
                    eval_dict.get("pct_pass"),
                    eval_dict.get("score_distribution_json"),
                    eval_dict.get("flags_json"),
                    eval_dict["created_at"],
                    eval_dict["month"],
                ),
            )
            self._conn.commit()
            return int(existing["id"])
        return self.insert(eval_dict)

    def get_by_month(self, month: str) -> dict | None:
        """Return the evaluation row for ``month`` (YYYY-MM), or None."""
        row = self._conn.execute(
            "SELECT * FROM monthly_evaluations WHERE month = ?",
            (month,),
        ).fetchone()
        return dict(row) if row else None

    def get_list(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """Return evaluation rows newest-first with pagination."""
        rows = self._conn.execute(
            "SELECT * FROM monthly_evaluations ORDER BY month DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    @db_retry()
    def mark_reviewed(self, month: str, action_note: str | None) -> None:
        """Stamp ``reviewed_at`` on the row for ``month`` and record ``action_note``."""
        self._conn.execute(
            """
            UPDATE monthly_evaluations
               SET reviewed_at = datetime('now'),
                   action_note = ?
             WHERE month = ?
            """,
            (action_note, month),
        )
        self._conn.commit()
