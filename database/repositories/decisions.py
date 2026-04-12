"""Repository for the ``decisions`` table."""

import json
import sqlite3


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("context_json"):
        try:
            d["context"] = json.loads(d["context_json"])
        except (TypeError, ValueError):
            d["context"] = None
    return d


class DecisionRepository:
    """Persistence for Claude decisions (including skips and holds)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def insert(self, decision: dict) -> int:
        """Insert a new decision row. Returns the new ``id``.

        Accepts either a ``context_json`` string field or a raw
        ``context`` dict (which will be JSON-encoded).
        """
        ctx_json = decision.get("context_json")
        if ctx_json is None and decision.get("context") is not None:
            ctx_json = json.dumps(decision["context"], default=str)

        cur = self._conn.execute(
            """
            INSERT INTO decisions (
                timestamp, strategy_type, underlying, cycle_id, wheel_state,
                action, reasoning, confidence, alpaca_order_id,
                prompt_version, context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision["timestamp"],
                decision["strategy_type"],
                decision["underlying"],
                decision.get("cycle_id"),
                decision.get("wheel_state"),
                decision["action"],
                decision.get("reasoning"),
                decision.get("confidence"),
                decision.get("alpaca_order_id"),
                decision.get("prompt_version"),
                ctx_json,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_recent(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY timestamp DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_by_underlying(self, underlying: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM decisions
            WHERE underlying = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (underlying, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
