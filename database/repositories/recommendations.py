"""Repository for watchlist_recommendations table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from database.db import db_retry


class RecommendationRepository:
    """Read/write access to watchlist_recommendations."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ── Write ─────────────────────────────────────────────────────────────────
    # CROSS-PROCESS WRITE TARGET: watchlist_recommendations is the only table
    # written by both the scheduler process (insert_batch(), called from
    # weekly_research.py) and the API process (record_decision(), called from
    # api/server.py when the operator accepts/rejects recommendations).
    # @db_retry() on both methods handles SQLite WAL lock contention between
    # the two writers. If you add new cross-process write paths, document them
    # here and confirm @db_retry() coverage.

    @db_retry()
    def insert_batch(self, recommendations: list[dict]) -> int:
        """Insert multiple recommendations in a single transaction.

        Each dict must have: generated_at, watchlist_name, symbol, action,
        score, reasoning, data_confidence.
        Optional: sub_scores (dict or None), operator_decision, operator_decided_at.

        Returns count inserted.
        """
        if not recommendations:
            return 0

        rows = []
        for r in recommendations:
            sub = r.get("sub_scores")
            sub_json = json.dumps(sub, default=str) if isinstance(sub, dict) else None
            rows.append((
                r["generated_at"],
                r["watchlist_name"],
                r["symbol"],
                r["action"],
                float(r["score"]),
                r["reasoning"],
                r["data_confidence"],
                r.get("operator_decision"),
                r.get("operator_decided_at"),
                sub_json,
            ))

        self._conn.executemany(
            """
            INSERT INTO watchlist_recommendations (
                generated_at, watchlist_name, symbol, action,
                score, reasoning, data_confidence,
                operator_decision, operator_decided_at, sub_scores_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()
        return len(rows)

    @db_retry()
    def record_decision(
        self,
        recommendation_id: int,
        decision: str,
        decided_at: Optional[str] = None,
    ) -> None:
        """Set operator_decision and operator_decided_at on one row.

        decision must be 'accepted' or 'rejected'.
        decided_at defaults to now (UTC ISO).
        """
        if decision not in ("accepted", "rejected"):
            raise ValueError(f"decision must be 'accepted' or 'rejected', got: {decision!r}")
        if decided_at is None:
            decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        self._conn.execute(
            """
            UPDATE watchlist_recommendations
               SET operator_decision = ?,
                   operator_decided_at = ?
             WHERE recommendation_id = ?
            """,
            (decision, decided_at, recommendation_id),
        )
        self._conn.commit()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_latest_batch(self) -> list[dict]:
        """Return all rows from the most recent generated_at timestamp."""
        row = self._conn.execute(
            "SELECT MAX(generated_at) FROM watchlist_recommendations"
        ).fetchone()
        if row is None or row[0] is None:
            return []
        latest = row[0]
        rows = self._conn.execute(
            "SELECT * FROM watchlist_recommendations WHERE generated_at = ? ORDER BY watchlist_name, action, score DESC",
            (latest,),
        ).fetchall()
        return [_parse_row(dict(r)) for r in rows]

    def get_pending(self) -> list[dict]:
        """Return all rows where operator_decision IS NULL."""
        rows = self._conn.execute(
            """
            SELECT * FROM watchlist_recommendations
             WHERE operator_decision IS NULL
             ORDER BY generated_at DESC, watchlist_name, action, score DESC
            """
        ).fetchall()
        return [_parse_row(dict(r)) for r in rows]

    def get_history(self, limit: int = 50) -> list[dict]:
        """Return the most recent recommendations, regardless of decision status."""
        rows = self._conn.execute(
            """
            SELECT * FROM watchlist_recommendations
             ORDER BY generated_at DESC, recommendation_id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_parse_row(dict(r)) for r in rows]


def _parse_row(d: dict) -> dict:
    """Parse sub_scores_json and return cleaned dict."""
    raw = d.pop("sub_scores_json", None)
    if raw:
        try:
            d["sub_scores"] = json.loads(raw)
        except (TypeError, ValueError):
            d["sub_scores"] = {}
    else:
        d["sub_scores"] = {}
    return d
