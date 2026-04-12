"""Repository for the ``cycles`` table."""

import sqlite3
import uuid
from datetime import datetime, timezone


class CycleRepository:
    """Persistence for income cycles (wheel + spread)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def insert(self, cycle: dict) -> str:
        """Insert a new cycle row. Returns the ``cycle_id``.

        ``cycle_id`` is generated as a UUID4 if not provided.
        ``opened_at`` defaults to now (UTC, ISO 8601) if not provided.
        ``status`` defaults to 'ACTIVE'.
        """
        cycle_id = cycle.get("cycle_id") or str(uuid.uuid4())
        opened_at = cycle.get("opened_at") or datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            """
            INSERT INTO cycles (
                cycle_id, strategy_type, underlying, opened_at,
                closed_at, status, outcome, total_premium, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                cycle["strategy_type"],
                cycle["underlying"],
                opened_at,
                cycle.get("closed_at"),
                cycle.get("status", "ACTIVE"),
                cycle.get("outcome"),
                cycle.get("total_premium"),
                cycle.get("notes"),
            ),
        )
        self._conn.commit()
        return cycle_id

    def get_active(self, underlying: str, strategy_type: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT * FROM cycles
            WHERE underlying = ? AND strategy_type = ? AND status = 'ACTIVE'
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            (underlying, strategy_type),
        ).fetchone()
        return dict(row) if row else None

    def close_cycle(
        self, cycle_id: str, outcome: str, total_premium: float,
    ) -> None:
        """Mark a cycle COMPLETED with the given outcome and total premium."""
        closed_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE cycles
               SET status        = 'COMPLETED',
                   outcome       = ?,
                   total_premium = ?,
                   closed_at     = ?
             WHERE cycle_id = ?
            """,
            (outcome, total_premium, closed_at, cycle_id),
        )
        self._conn.commit()

    def get_recent(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM cycles ORDER BY opened_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
