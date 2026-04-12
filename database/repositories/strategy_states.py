"""Repository for the ``strategy_states`` table."""

import json
import sqlite3
from datetime import datetime, timezone


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("payload_json"):
        try:
            d["payload"] = json.loads(d["payload_json"])
        except (TypeError, ValueError):
            d["payload"] = None
    return d


class StrategyStateRepository:
    """Persistence for live strategy state, keyed by (strategy_type, underlying)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(
        self,
        strategy_type: str,
        underlying: str,
        state: str,
        cycle_id: str | None,
        payload: dict | None,
    ) -> None:
        """Insert or update the row for ``(strategy_type, underlying)``.

        Relies on the table's ``UNIQUE(strategy_type, underlying)``
        constraint via ON CONFLICT.
        """
        payload_json = (
            json.dumps(payload, default=str) if payload is not None else None
        )
        updated_at = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            """
            INSERT INTO strategy_states (
                strategy_type, underlying, state, active_cycle_id,
                updated_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_type, underlying) DO UPDATE SET
                state           = excluded.state,
                active_cycle_id = excluded.active_cycle_id,
                updated_at      = excluded.updated_at,
                payload_json    = excluded.payload_json
            """,
            (
                strategy_type, underlying, state, cycle_id,
                updated_at, payload_json,
            ),
        )
        self._conn.commit()

    def get(self, strategy_type: str, underlying: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT * FROM strategy_states
            WHERE strategy_type = ? AND underlying = ?
            """,
            (strategy_type, underlying),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_all(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM strategy_states ORDER BY strategy_type, underlying"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
