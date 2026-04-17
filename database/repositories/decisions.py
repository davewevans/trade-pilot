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
    # Reasoning is stored as TEXT; the recorder JSON-encodes dicts on write.
    # Try to parse it back; legacy rows written as Python-repr strings or
    # plain prose stay as strings (frontend handles both).
    if isinstance(d.get("reasoning"), str):
        try:
            d["reasoning"] = json.loads(d["reasoning"])
        except (json.JSONDecodeError, TypeError):
            pass  # leave as string
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

        research_json = decision.get("research_metadata_json")
        if research_json is None and decision.get("research_metadata") is not None:
            research_json = json.dumps(decision["research_metadata"], default=str)

        cur = self._conn.execute(
            """
            INSERT INTO decisions (
                timestamp, strategy_type, underlying, cycle_id, wheel_state,
                action, reasoning, confidence, alpaca_order_id,
                prompt_version, context_json, research_metadata_json,
                skip_gate, skip_reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                research_json,
                decision.get("skip_gate"),
                decision.get("skip_reason_code"),
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

    def query(
        self,
        limit: int = 50,
        underlying: str | None = None,
        action: str | None = None,
        strategy_types: list[str] | None = None,
        confidence: float | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Filtered decision query for the API.

        Returns ``(rows, total_count_after_filter)``. ``action`` and
        ``underlying`` filters are case-insensitive. ``strategy_types``
        scopes results to a list of strategies (used by the API's
        ``?account=`` filter). ``confidence`` filters by exact match
        (the recorder writes discrete 0.9/0.6/0.3 values). ``offset``
        supports paginated "load more" — the total count returned is
        the full filtered total, ignoring offset.
        """
        where: list[str] = []
        params: list = []
        if underlying:
            where.append("UPPER(underlying) = UPPER(?)")
            params.append(underlying)
        if action:
            where.append("UPPER(action) = UPPER(?)")
            params.append(action)
        if strategy_types:
            placeholders = ",".join(["?"] * len(strategy_types))
            where.append(f"strategy_type IN ({placeholders})")
            params.extend(strategy_types)
        if confidence is not None:
            where.append("confidence = ?")
            params.append(confidence)
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM decisions{clause}", params,
        ).fetchone()[0]

        rows = self._conn.execute(
            f"""
            SELECT * FROM decisions{clause}
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
        return [_row_to_dict(r) for r in rows], int(total)
