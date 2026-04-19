"""Repository for recommendation_outcomes table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from database.db import db_retry


class OutcomeRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @db_retry()
    def insert(self, data: dict) -> None:
        """Insert or ignore an outcome row (idempotent on PK conflict)."""
        proxy_json = data.get("proxy_params_json")
        if isinstance(proxy_json, dict):
            proxy_json = json.dumps(proxy_json, default=str)

        self._conn.execute(
            """
            INSERT OR IGNORE INTO recommendation_outcomes (
                recommendation_id, outcome_type, window_days,
                window_end_date, trade_count, pnl_total,
                pnl_per_trade, win_rate, proxy_params_json, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["recommendation_id"],
                data["outcome_type"],
                data["window_days"],
                data["window_end_date"],
                data.get("trade_count"),
                data.get("pnl_total"),
                data.get("pnl_per_trade"),
                data.get("win_rate"),
                proxy_json,
                data.get("computed_at", datetime.now(timezone.utc).isoformat()),
            ),
        )
        self._conn.commit()

    def exists(self, recommendation_id: int, window_days: int) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM recommendation_outcomes
               WHERE recommendation_id = ? AND window_days = ?
               LIMIT 1""",
            (recommendation_id, window_days),
        ).fetchone()
        return row is not None

    def get_all(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM recommendation_outcomes
               ORDER BY recommendation_id, outcome_type"""
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("proxy_params_json"):
                try:
                    d["proxy_params"] = json.loads(d["proxy_params_json"])
                except (TypeError, ValueError):
                    d["proxy_params"] = {}
            result.append(d)
        return result
