"""Repository for the ``decisions`` table."""

import json
import sqlite3

from database.db import db_retry


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

    @db_retry()
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
                skip_gate, skip_reason_code,
                model_version, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, estimated_cost_usd,
                job_run_id, pre_check_verdict
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                decision.get("model_version"),
                decision.get("input_tokens"),
                decision.get("output_tokens"),
                decision.get("cache_read_tokens"),
                decision.get("cache_creation_tokens"),
                decision.get("estimated_cost_usd"),
                decision.get("job_run_id"),
                decision.get("pre_check_verdict"),
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

    def get_skip_breakdown(self, account: str | None, since_days: int) -> dict:
        """Return skip counts broken down by gate and reason within gate.

        Returns:
            {
              "total_skips": int,
              "by_gate": [{"gate": str, "count": int, "pct": float}, ...],
              "by_reason_within_gate": {gate: [{"reason": str, "count": int, "pct_of_gate": float}]},
              "unclassified_count": int  # rows where skip_gate IS NULL
            }
        """
        from datetime import datetime, timedelta

        cutoff = (datetime.utcnow() - timedelta(days=since_days)).isoformat()

        where: list[str] = ["UPPER(action) = 'SKIP'", "timestamp >= ?"]
        params: list = [cutoff]

        _ACCOUNT_STRATEGY_MAP: dict[str, list[str]] = {
            "wheel": ["wheel"],
            "iron_condor": ["iron_condor"],
            "spreads": ["bull_put_spread", "bear_call_spread", "long_call_vertical"],
        }
        if account:
            strategy_types = _ACCOUNT_STRATEGY_MAP.get(account.lower())
            if strategy_types:
                placeholders = ",".join(["?"] * len(strategy_types))
                where.append(f"strategy_type IN ({placeholders})")
                params.extend(strategy_types)

        clause = " WHERE " + " AND ".join(where)

        total_row = self._conn.execute(
            f"SELECT COUNT(*) FROM decisions{clause}", params
        ).fetchone()
        total_skips = int(total_row[0]) if total_row else 0

        # Count unclassified (skip_gate IS NULL)
        unclassified_row = self._conn.execute(
            f"SELECT COUNT(*) FROM decisions{clause} AND skip_gate IS NULL", params
        ).fetchone()
        unclassified_count = int(unclassified_row[0]) if unclassified_row else 0

        # Group by gate
        gate_rows = self._conn.execute(
            f"SELECT skip_gate, COUNT(*) AS n FROM decisions{clause} AND skip_gate IS NOT NULL "
            f"GROUP BY skip_gate ORDER BY n DESC",
            params,
        ).fetchall()

        by_gate = []
        for row in gate_rows:
            gate = row[0] or "unknown"
            count = int(row[1])
            pct = round(count / total_skips * 100, 1) if total_skips > 0 else 0.0
            by_gate.append({"gate": gate, "count": count, "pct": pct})

        # Group by reason within each gate
        reason_rows = self._conn.execute(
            f"SELECT skip_gate, skip_reason_code, COUNT(*) AS n FROM decisions{clause} "
            f"AND skip_gate IS NOT NULL AND skip_reason_code IS NOT NULL "
            f"GROUP BY skip_gate, skip_reason_code ORDER BY skip_gate, n DESC",
            params,
        ).fetchall()

        # Build gate→count lookup for pct_of_gate calculation
        gate_totals = {row["gate"]: row["count"] for row in by_gate}
        by_reason_within_gate: dict[str, list] = {}
        for row in reason_rows:
            gate = row[0]
            reason = row[1]
            count = int(row[2])
            gate_total = gate_totals.get(gate, 1)
            pct_of_gate = round(count / gate_total * 100, 1) if gate_total > 0 else 0.0
            if gate not in by_reason_within_gate:
                by_reason_within_gate[gate] = []
            by_reason_within_gate[gate].append({
                "reason": reason,
                "count": count,
                "pct_of_gate": pct_of_gate,
            })

        return {
            "total_skips": total_skips,
            "by_gate": by_gate,
            "by_reason_within_gate": by_reason_within_gate,
            "unclassified_count": unclassified_count,
        }

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

    def get_in_range(self, start_iso: str, end_iso: str) -> list[dict]:
        """Return all decisions with timestamp in [start_iso, end_iso] (inclusive).

        Results are ordered by timestamp ASC, id ASC so the caller processes
        decisions in chronological order.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM decisions
             WHERE timestamp >= ? AND timestamp <= ?
             ORDER BY timestamp ASC, id ASC
            """,
            (start_iso, end_iso),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
